#!/usr/bin/env python3
"""
CONTADOR DE TOKENS DE OLLAMA
============================

Qué hace, en una frase:
    Mide cuántos tokens (entrada/salida) consume cada llamada al LLM mientras
    corre `agent.py`, sin modificar `agent.py` ni un solo carácter de él.

Cómo funciona:
    Importa `agent` como módulo y "envuelve" dos funciones suyas:
      - `agent.build_llm`   -> se le pone una carcasa que cuenta los tokens de
                               cada respuesta (vienen en `usage_metadata`).
      - `agent.fetch_rows`  -> se etiqueta cada oferta para saber a qué CV
                               corresponde cada llamada.
    Después ejecuta `agent.main()` igual que si lo hubieras lanzado a mano.

Uso:
    python contador_tokens.py --provider ollama --model gemma4:12b \
        --db empleos_santiago.db --table empleos \
        --id-col identificador --title-col empleo --out output

    python contador_tokens.py --reporte          # resumen de todas las corridas
    python contador_tokens.py --reporte --html    # abre el resumen en el navegador

Dónde se guarda el historial:
    logs/tokens.jsonl   (una línea JSON por corrida)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "tokens.jsonl"


# ----------------------------------------------------------------------------
# CONTADOR
# ----------------------------------------------------------------------------
class ContadorTokens:
    """Acumula el uso de tokens por oferta."""

    def __init__(self):
        self.model = "?"
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_time = 0.0
        self.per_job: dict[str, dict] = {}
        self.current_job: str = "(sin oferta)"
        self.current_file: str = ""

    # -- registro de una llamada al LLM -------------------------------------
    def record(self, resp) -> None:
        usage = getattr(resp, "usage_metadata", None) or {}
        meta = getattr(resp, "response_metadata", None) or {}

        inp = int(usage.get("input_tokens") or meta.get("prompt_eval_count") or 0)
        out = int(usage.get("output_tokens") or meta.get("eval_count") or 0)
        dur = float(meta.get("total_duration") or 0) / 1e9  # ns -> s

        self.llm_calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.total_time += dur

        j = self.per_job.setdefault(
            self.current_job,
            {"id": self.current_job, "file": self.current_file, "calls": 0,
             "input": 0, "output": 0, "seconds": 0.0},
        )
        j["calls"] += 1
        j["input"] += inp
        j["output"] += out
        j["seconds"] += dur

    # -- resumen ------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "model": self.model,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "seconds": round(self.total_time, 1),
            "jobs": list(self.per_job.values()),
        }

    def print_summary(self) -> None:
        s = self.summary()
        print("\n" + "=" * 72)
        print("📊 RESUMEN DE TOKENS")
        print("=" * 72)
        print(f"Modelo        : {s['model']}")
        print(f"Llamadas LLM  : {s['llm_calls']}")
        print(f"Tokens entrada: {s['input_tokens']:,}")
        print(f"Tokens salida : {s['output_tokens']:,}")
        print(f"Tokens TOTALES: {s['total_tokens']:,}")
        print(f"Tiempo en LLM : {s['seconds']} s")
        if s["jobs"]:
            print("-" * 72)
            print(f"{'id':<14}{'archivo':<38}{'calls':>6}{'entrada':>10}{'salida':>9}")
            for j in s["jobs"]:
                name = (j["file"] or j["id"])[:36]
                print(f"{j['id']:<14}{name:<38}{j['calls']:>6}"
                      f"{j['input']:>10,}{j['output']:>9,}")
        print("=" * 72)
        print(f"Historial guardado en {LOG_FILE}\n")


# ----------------------------------------------------------------------------
# CARCASA DEL MODELO (envuelve el LLM para contar tokens)
# ----------------------------------------------------------------------------
class LLMConContador:
    """Delegación total al LLM real, pero contando tokens de cada respuesta."""

    def __init__(self, inner, contador: ContadorTokens):
        self._inner = inner
        self._contador = contador
        meta = getattr(inner, "model", None) or getattr(inner, "model_name", None)
        if meta:
            contador.model = str(meta)

    def invoke(self, *args, **kwargs):
        resp = self._inner.invoke(*args, **kwargs)
        self._contador.record(resp)
        return resp

    def __getattr__(self, name):        # todo lo demás se delega
        return getattr(self._inner, name)


# ----------------------------------------------------------------------------
# PARCHE (no se modifica agent.py: solo se sustituyen atributos en memoria)
# ----------------------------------------------------------------------------
def instalar_contador(contador: ContadorTokens) -> None:
    import agent

    # 1. build_llm -> devolver el LLM envuelto
    build_llm_original = agent.build_llm

    def build_llm_envuelto(*args, **kwargs):
        llm = build_llm_original(*args, **kwargs)
        return LLMConContador(llm, contador)

    agent.build_llm = build_llm_envuelto

    # 2. fetch_rows -> saber a qué oferta pertenece cada llamada
    fetch_rows_original = agent.fetch_rows
    slugify = agent.slugify

    def fetch_rows_envuelto(db_path, table, query=None):
        for row in fetch_rows_original(db_path, table, query):
            job_id = str(row.get("identificador") or row.get("id") or "")
            title = row.get("empleo") or row.get("title") or ""
            contador.current_job = job_id or "(sin id)"
            contador.current_file = f"cv_{job_id}_{slugify(title)}.md" if job_id else ""
            yield row

    agent.fetch_rows = fetch_rows_envuelto


# ----------------------------------------------------------------------------
# HISTORIAL Y REPORTE
# ----------------------------------------------------------------------------
def guardar_historial(registro: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(registro, ensure_ascii=False) + "\n")


def leer_historial() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    out = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def reporte() -> None:
    filas = leer_historial()
    if not filas:
        print(f"Sin historial todavía ({LOG_FILE} no existe o está vacío).")
        return

    tot_in = sum(f.get("input_tokens", 0) for f in filas)
    tot_out = sum(f.get("output_tokens", 0) for f in filas)
    tot_calls = sum(f.get("llm_calls", 0) for f in filas)
    tot_jobs = len({j["id"] for f in filas for j in f.get("jobs", [])})

    print("\n" + "=" * 72)
    print("📊 HISTORIAL DE TOKENS (todas las corridas)")
    print("=" * 72)
    print(f"{'fecha':<20}{'modelo':<18}{'calls':>7}{'entrada':>12}{'salida':>11}{'total':>12}")
    for f in filas:
        print(f"{f.get('when', '')[:19]:<20}{f.get('model', '?')[:16]:<18}"
              f"{f.get('llm_calls', 0):>7}{f.get('input_tokens', 0):>12,}"
              f"{f.get('output_tokens', 0):>11,}{f.get('total_tokens', 0):>12,}")
    print("-" * 72)
    print(f"TOTALES: {tot_calls} llamadas · {tot_jobs} ofertas · "
          f"{tot_in:,} entrada + {tot_out:,} salida = {tot_in + tot_out:,} tokens")
    print("=" * 72 + "\n")


# ----------------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------------
def main():
    if "--reporte" in sys.argv:
        reporte()
        return

    import agent  # noqa: F401  (se importa para validar que exista)

    contador = ContadorTokens()
    instalar_contador(contador)

    # Los argumentos que faltan los pone agent.py; los que llegan se pasan tal cual.
    sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:]]

    t0 = time.time()
    try:
        agent.main()
    finally:
        s = contador.summary()
        s["when"] = datetime.now().isoformat(timespec="seconds")
        s["wall_seconds"] = round(time.time() - t0, 1)
        s["argv"] = sys.argv[1:]
        guardar_historial(s)
        contador.print_summary()


if __name__ == "__main__":
    main()
