#!/usr/bin/env python3
"""
VISOR DE OFERTAS + CVs GENERADOS
================================

Qué hace, en una frase:
    Levanta una app web local que muestra, para cada oferta de empleo de la base
    SQLite `empleos_santiago.db`, la oferta original (desde la BD) junto al CV
    generado por el agente (carpeta `output/`), emparejados por identificador.

Relación con el resto del proyecto:
    - `linkedin_scraper.py` crea la tabla `empleos` en la BD (una fila por oferta,
      clave primaria `identificador`).
    - `agent.py` lee esa tabla y escribe un archivo por oferta en `output/`
      llamado `cv_{identificador}_{slug_del_titulo}.md`.
    Esta app NO modifica ninguno de los dos: solo los lee en modo lectura.

Sin dependencias extra: usa solo la librería estándar de Python
(http.server + sqlite3). La interfaz es un único archivo HTML en `web/`.

Uso:
    python app.py                       # http://localhost:8000
    python app.py --port 8080 --db empleos_santiago.db --output output
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import sqlite3
import sys
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import md2html

# `render.py` es el renderizador de diseño del proyecto (Markdown -> HTML con estilos
# de CV). Se usa si está disponible; si no, se recurre a `md2html`, que no depende
# de ningún paquete externo.
try:
    import render as cv_render
except Exception:  # pragma: no cover - depende del entorno
    cv_render = None


def weasyprint_available() -> bool:
    """True si se puede generar PDF (WeasyPrint instalado y funcional)."""
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False

# ----------------------------------------------------------------------------
# CONFIGURACIÓN POR DEFECTO
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "empleos_santiago.db"
DEFAULT_OUT = BASE_DIR / "output"
WEB_DIR = BASE_DIR / "web"

# Tabla y columnas de la BD (las mismas que usa linkedin_scraper.py).
TABLE = "empleos"
COL_ID = "identificador"
COL_TITLE = "empleo"
COL_COMPANY = "empresa"
COL_LOCATION = "ubicacion"

# agent.py guarda los CVs como: cv_{identificador}_{slug}.md
CV_FILENAME_RE = re.compile(r"^cv_(?P<id>[^_]+)_(?P<slug>.*)\.(?P<ext>md|docx|txt|html)$", re.IGNORECASE)

# Formato "bonito" de los metadatos de la oferta (etiqueta -> columna en la BD).
META_FIELDS = [
    ("Empresa", COL_COMPANY),
    ("Ubicación", COL_LOCATION),
    ("Fecha de creación", "fecha_creacion"),
    ("Publicación", "fecha_publicacion_texto"),
    ("Nivel de experiencia", "nivel_experiencia"),
    ("Tipo de empleo", "tipo_empleo"),
    ("Función laboral", "funcion_laboral"),
    ("Industrias", "industrias"),
    ("Postulantes", "num_postulantes"),
    ("Criterio de búsqueda", "criterio_busqueda"),
    ("Extracción", "fecha_extraccion"),
]

# Secciones largas de la oferta, en el orden en que se muestran.
BODY_SECTIONS = [
    ("Responsabilidades", "responsabilidades"),
    ("Requisitos", "requisitos"),
    ("Descripción", "descripcion"),
]


# ----------------------------------------------------------------------------
# CAPA DE DATOS: LECTURA DE LA BD Y DE LA CARPETA output/
# ----------------------------------------------------------------------------
def load_token_stats(log_file: Path = None) -> dict:
    """
    Lee el historial de `contador_tokens.py` (logs/tokens.jsonl) y devuelve el
    consumo de tokens por identificador de empleo.

    Devuelve: {id_empleo: {"input":..., "output":..., "total":..., "calls":...,
                           "model":..., "when":...}}
    """
    path = log_file or (BASE_DIR / "logs" / "tokens.jsonl")
    stats: dict[str, dict] = {}
    if not path.exists():
        return stats
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            reg = json.loads(line)
            for job in reg.get("jobs", []):
                jid = str(job.get("id") or "")
                if not jid:
                    continue
                cur = stats.setdefault(
                    jid,
                    {"input": 0, "output": 0, "total": 0, "calls": 0,
                     "model": reg.get("model", ""), "when": reg.get("when", "")},
                )
                cur["input"] += job.get("input", 0)
                cur["output"] += job.get("output", 0)
                cur["total"] += job.get("input", 0) + job.get("output", 0)
                cur["calls"] += job.get("calls", 0)
                cur["when"] = reg.get("when", cur["when"])
    except (OSError, json.JSONDecodeError):
        return stats
    return stats


def connect(db_path: Path) -> sqlite3.Connection:
    """Abre la BD en modo lectura (SQLite no tiene modo lectura real, pero
    al menos no creamos tablas ni escribimos nada)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_jobs(db_path: Path) -> list[dict]:
    """Devuelve todas las ofertas como lista de diccionarios."""
    with connect(db_path) as conn:
        rows = conn.execute(f'SELECT * FROM "{TABLE}"').fetchall()
    return [dict(r) for r in rows]


def fetch_job(db_path: Path, job_id: str) -> dict | None:
    """Devuelve una oferta por identificador, o None si no existe."""
    with connect(db_path) as conn:
        row = conn.execute(
            f'SELECT * FROM "{TABLE}" WHERE "{COL_ID}" = ?', (job_id,)
        ).fetchone()
    return dict(row) if row else None


def scan_output(out_dir: Path) -> dict[str, list[dict]]:
    """
    Escanea la carpeta `output/` y agrupa los CVs por identificador de empleo.

    Se aceptan los nombres que produce agent.py: `cv_<id>_<slug>.md`.
    Si el prefijo no viene con `_` (por ejemplo `cv_<id>.md`) también se acepta.

    Devuelve:
        {identificador: [{"file":..., "slug":..., "ext":..., "size":..., "mtime":...}, ...]}
    """
    index: dict[str, list[dict]] = {}
    if not out_dir.exists():
        return index

    for path in sorted(out_dir.iterdir()):
        if not path.is_file():
            continue
        m = CV_FILENAME_RE.match(path.name)
        if m:
            job_id = m.group("id")
            slug = m.group("slug")
            ext = m.group("ext").lower()
        else:
            m2 = re.match(r"^cv_(?P<id>[^.]+)\.(?P<ext>md|docx|txt|html)$", path.name, re.IGNORECASE)
            if not m2:
                continue
            job_id, slug, ext = m2.group("id"), "", m2.group("ext").lower()

        st = path.stat()
        index.setdefault(job_id, []).append(
            {
                "file": path.name,
                "slug": slug.replace("_", " "),
                "ext": ext,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    return index


def build_job_summary(job: dict, cvs: list[dict]) -> dict:
    """Resumen ligero de una oferta para la lista lateral de la UI."""
    return {
        "id": job.get(COL_ID) or "",
        "title": job.get(COL_TITLE) or "(sin título)",
        "company": job.get(COL_COMPANY) or "",
        "location": job.get(COL_LOCATION) or "",
        "date": job.get("fecha_creacion") or "",
        "url": job.get("url") or "",
        "has_cv": bool(cvs),
        "cv_count": len(cvs),
    }


# ----------------------------------------------------------------------------
# SERVIDOR HTTP
# ----------------------------------------------------------------------------
class JobAppHandler(BaseHTTPRequestHandler):
    """Maneja las peticiones: '/' sirve la UI, '/api/*' los datos."""

    server_version = "JobViewer/1.0"

    # Inyectado por main()
    db_path: Path = DEFAULT_DB
    out_dir: Path = DEFAULT_OUT
    web_dir: Path = WEB_DIR

    # -- utilidades ---------------------------------------------------------
    def log_message(self, fmt, *args):  # silencia el log por petición en consola
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path, download: bool = False):
        if not path.is_file():
            self._send_error_json("Archivo no encontrado", 404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in (".md", ".txt"):
            ctype = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    # -- enrutado -----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if route == "/" or route == "/index.html":
                return self._send_file(self.web_dir / "index.html")
            if route == "/api/jobs":
                return self._api_jobs(query)
            if route.startswith("/api/job/"):
                return self._api_job(route[len("/api/job/"):])
            if route.startswith("/api/cv/"):
                return self._api_cv(route[len("/api/cv/"):], query)
            if route == "/api/reload":
                return self._send_json({"ok": True, "message": "Recargado"})
            if route == "/health":
                return self._send_json({"ok": True})
        except Exception as e:  # no romper el servidor por un error puntual
            return self._send_error_json(f"Error interno: {e}", 500)

        self._send_error_json("Ruta no encontrada", 404)

    # -- endpoints ----------------------------------------------------------
    def _api_jobs(self, query: dict):
        """GET /api/jobs?q=texto&only_cv=1 -> lista de ofertas + estado de CV."""
        jobs = fetch_jobs(self.db_path)
        cv_index = scan_output(self.out_dir)
        token_stats = load_token_stats()

        q = (query.get("q", [""])[0] or "").strip().lower()
        only_cv = query.get("only_cv", ["0"])[0] in ("1", "true", "yes")

        summaries = []
        for job in jobs:
            job_id = str(job.get(COL_ID) or "")
            cvs = cv_index.get(job_id, [])
            if only_cv and not cvs:
                continue
            s = build_job_summary(job, cvs)
            tok = token_stats.get(str(job.get(COL_ID) or ""))
            s["tokens"] = tok["total"] if tok else 0
            if q:
                haystack = " ".join(
                    str(s.get(k) or "") for k in ("id", "title", "company", "location")
                ).lower()
                if q not in haystack:
                    continue
            summaries.append(s)

        summaries.sort(key=lambda s: (s.get("date") or "", s.get("id") or ""), reverse=True)

        return self._send_json(
            {
                "jobs": summaries,
                "stats": {
                    "total": len(jobs),
                    "con_cv": sum(1 for j in jobs if cv_index.get(str(j.get(COL_ID) or ""))),
                    "sin_cv": sum(1 for j in jobs if not cv_index.get(str(j.get(COL_ID) or ""))),
                    "archivos_cv": sum(len(v) for v in cv_index.values()),
                    "tokens_total": sum(t.get("total", 0) for t in token_stats.values()),
                },
                "db": str(self.db_path),
                "output": str(self.out_dir),
            }
        )

    def _api_job(self, job_id: str):
        """GET /api/job/<id> -> oferta completa + lista de CVs generados."""
        job = fetch_job(self.db_path, job_id)
        if not job:
            return self._send_error_json(f"No existe la oferta con id {job_id}", 404)

        cv_index = scan_output(self.out_dir)
        cvs = cv_index.get(job_id, [])

        meta = [{"label": label, "value": job.get(col)} for label, col in META_FIELDS]
        meta = [m for m in meta if m["value"] not in (None, "", [])]

        sections = [
            {"label": label, "value": job.get(col)}
            for label, col in BODY_SECTIONS
            if job.get(col) not in (None, "", [])
        ]

        token_stats = load_token_stats()
        job_tokens = token_stats.get(str(job.get(COL_ID) or ""), None)

        return self._send_json(
            {
                "job": job,
                "id": job.get(COL_ID),
                "title": job.get(COL_TITLE) or "(sin título)",
                "company": job.get(COL_COMPANY) or "",
                "url": job.get("url") or "",
                "meta": meta,
                "sections": sections,
                "cvs": cvs,
                "tokens": job_tokens,
                "can_pdf": weasyprint_available(),
                "renderer": "render.py" if cv_render is not None else "md2html",
            }
        )

    def _api_cv(self, rest: str, query: dict):
        """
        GET /api/cv/<id>                     -> contenido del CV (texto crudo)
        GET /api/cv/<id>?file=xxx            -> contenido de un archivo concreto
        GET /api/cv/<id>/<file>?view=html    -> el CV como documento HTML (imprimible)
        GET /api/cv/<id>/<file>?view=pdf     -> el CV como PDF (si hay WeasyPrint)
        GET /api/cv/<id>/<file>?view=html&download=1 -> descarga el .html
        GET /api/cv/<id>/<file>?download=1   -> descarga el archivo original
        """
        job_id, _, filename = rest.partition("/")
        cv_index = scan_output(self.out_dir)
        cvs = cv_index.get(job_id, [])
        if not cvs:
            return self._send_error_json(f"No hay CV generado para el id {job_id}", 404)

        if filename:
            target = next((c for c in cvs if c["file"] == filename), None)
            if not target:
                return self._send_error_json("Ese CV no pertenece a esta oferta", 404)
        else:
            target = cvs[0]

        # Seguridad: el archivo debe estar dentro de la carpeta output/
        path = (self.out_dir / target["file"]).resolve()
        if self.out_dir.resolve() not in path.parents:
            return self._send_error_json("Ruta no permitida", 403)

        want_download = query.get("download", ["0"])[0] in ("1", "true", "yes")
        view = query.get("view", [""])[0].lower()

        # --- Vista HTML o PDF ------------------------------------------------
        if view in ("html", "htm", "pdf"):
            # Si el archivo ya es un documento HTML y se pide PDF:
            if path.suffix.lower() in (".html", ".htm") and view == "pdf":
                doc = path.read_text(encoding="utf-8", errors="replace")
                pdf = self._html_to_pdf(doc)
                if pdf is None:
                    return self._send_error_json(
                        "No se puede generar PDF: fallo al procesar con WeasyPrint.",
                        501,
                    )
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf)))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{path.stem}.pdf"',
                )
                self.end_headers()
                self.wfile.write(pdf)
                return

            # Si el archivo ya es un documento HTML y se pide HTML, se sirve tal cual:
            if path.suffix.lower() in (".html", ".htm"):
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                if want_download:
                    self.send_header(
                        "Content-Disposition", f'attachment; filename="{path.name}"'
                    )
                self.end_headers()
                self.wfile.write(data)
                return

            doc = self._cv_to_html_doc(path, target, job_id)
            if doc is None:
                return self._send_error_json("No se pudo convertir a HTML", 500)

            # --- PDF (requiere WeasyPrint) ---
            if view == "pdf":
                pdf = self._html_to_pdf(doc)
                if pdf is None:
                    return self._send_error_json(
                        "No se puede generar PDF: falta WeasyPrint "
                        "(pip install weasyprint). Usa 'Abrir HTML' -> Imprimir -> Guardar como PDF.",
                        501,
                    )
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf)))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{path.stem}.pdf"',
                )
                self.end_headers()
                self.wfile.write(pdf)
                return

            data = doc.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if want_download:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{path.stem}.html"'
                )
            self.end_headers()
            self.wfile.write(data)
            return

        if want_download:
            return self._send_file(path, download=True)

        if path.suffix.lower() == ".docx":
            return self._send_file(path, download=True)

        return self._send_file(path)

    # -- helpers para la vista HTML -----------------------------------------
    def _cv_to_html_doc(self, path: Path, target: dict, job_id: str) -> str | None:
        """
        Devuelve el documento HTML completo del CV.

        Prioridad:
          1. `render.py` (el diseño del proyecto) para los .md
          2. `md2html` (propio, sin dependencias) para .md y .docx
        """
        ext = path.suffix.lower()
        try:
            if ext == ".docx":
                body = md2html.docx_to_html(path)
                return md2html.render_document(
                    body,
                    title=self._cv_title(target),
                    subtitle=self._cv_subtitle(job_id),
                    meta=target["file"],
                    include_toolbar=True,
                )

            text = path.read_text(encoding="utf-8", errors="replace")

            # 1. render.py: diseño oficial del proyecto (Markdown -> HTML con estilos).
            if cv_render is not None:
                try:
                    return cv_render.md_to_html(text, title=self._cv_title(target))
                except Exception:
                    pass  # si falla, se usa el conversor propio

            # 2. md2html: conversor propio, sin dependencias externas.
            body = md2html.markdown_to_html(text)
            return md2html.render_document(
                body,
                title=self._cv_title(target),
                subtitle=self._cv_subtitle(job_id),
                meta=target["file"],
                include_toolbar=True,
            )
        except Exception:
            return None

    def _html_to_pdf(self, html: str) -> bytes | None:
        """Convierte el HTML del CV a PDF con WeasyPrint (si está disponible)."""
        try:
            from weasyprint import HTML
        except Exception:
            return None
        try:
            doc = HTML(string=html).render()
            pdf_bytes = doc.write_pdf()
            return pdf_bytes if isinstance(pdf_bytes, bytes) else bytes(pdf_bytes)
        except Exception:
            return None

    def _cv_title(self, target: dict) -> str:
        """Título del documento HTML: el slug del nombre del archivo, limpio."""
        slug = (target.get("slug") or "").strip()
        return slug or target.get("file", "CV")

    def _cv_subtitle(self, job_id: str) -> str:
        """Subtítulo del documento HTML: la empresa de la oferta."""
        job = fetch_job(self.db_path, job_id)
        if not job:
            return ""
        parts = [p for p in (job.get(COL_COMPANY), job.get(COL_TITLE)) if p]
        return " — ".join(parts)


# ----------------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------------
def port_is_free(host: str, port: int) -> bool:
    """True si se puede abrir un socket en (host, port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, port: int, max_try: int = 20) -> int:
    """
    Devuelve un puerto libre.

    - Si el puerto pedido está libre, lo usa tal cual.
    - Si está ocupado, busca el siguiente disponible (port, port+1, ...)
      para no tener que pelear con el error 'Address already in use'.
    """
    if port == 0 or port_is_free(host, port):
        return port
    for candidate in range(port + 1, port + max_try + 1):
        if port_is_free(host, candidate):
            return candidate
    sys.exit(
        f"No hay puertos libres entre {port} y {port + max_try}. "
        "Prueba con otro: --port 9000"
    )


def main():
    p = argparse.ArgumentParser(description="App web para revisar ofertas vs CVs generados.")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Ruta de la base SQLite (empleos_santiago.db).")
    p.add_argument("--output", default=str(DEFAULT_OUT), help="Carpeta con los CVs generados (output/).")
    p.add_argument("--host", default="127.0.0.1", help="Host (usa 0.0.0.0 para exponer en la red local).")
    p.add_argument("--port", type=int, default=8000,
                   help="Puerto HTTP. Si está ocupado se usa el siguiente libre. Usa 0 para que el "
                        "sistema elija uno cualquiera.")
    p.add_argument("--open", action="store_true", help="Abre el navegador automáticamente.")
    args = p.parse_args()

    db_path = Path(args.db).resolve()
    out_dir = Path(args.output).resolve()

    if not db_path.exists():
        sys.exit(f"No existe la base de datos: {db_path}")
    if not (db_path.parent / "linkedin_scraper.py").exists():
        print("Aviso: no se encontró linkedin_scraper.py junto a la app (no es bloqueante).")

    JobAppHandler.db_path = db_path
    JobAppHandler.out_dir = out_dir
    JobAppHandler.web_dir = WEB_DIR

    jobs = fetch_jobs(db_path)
    cvs = scan_output(out_dir)
    print(f"Base de datos : {db_path}  ({len(jobs)} ofertas)")
    print(f"CVs generados : {out_dir}  ({sum(len(v) for v in cvs.values())} archivos, "
          f"{len(cvs)} ofertas con CV)")

    # Puerto: si el pedido está ocupado, se avisa y se toma el siguiente libre.
    port = pick_port(args.host, args.port)
    if port != args.port and port != 0:
        print(f"\n⚠ El puerto {args.port} está ocupado; se usará el {port}.")

    try:
        server = ThreadingHTTPServer((args.host, port), JobAppHandler)
    except OSError as e:
        sys.exit(f"No se pudo abrir el puerto {port}: {e}\nPrueba con: --port {port + 1}")

    url = f"http://{args.host}:{port}/"
    print(f"\nApp corriendo en {url}  (Ctrl+C para salir)\n")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando...")
        server.shutdown()


if __name__ == "__main__":
    main()
