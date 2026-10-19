#!/usr/bin/env python3
"""
AGENTE GENERADOR DE CVs PERSONALIZADOS
======================================

Qué hace, en una frase:
    Lee ofertas de empleo desde una base SQLite y, para CADA oferta, usa un LLM
    (Gemini por API u Ollama local) para escribir un CV adaptado a esa oferta,
    usando únicamente tu experiencia real (archivo cv_base.md).

Flujo por cada oferta (grafo de LangGraph):

    START -> analizar_oferta -> redactar_cv -> revisar --(hay problemas y quedan
                                   ^                       reintentos)--+
                                   |                                    |
                                   +------------------------------------+
                                                       |
                                        (sin problemas o sin reintentos)
                                                       v
                                                      END

Ver DOCUMENTACION.md para la explicación completa.
"""

# ----------------------------------------------------------------------------
# IMPORTS
# ----------------------------------------------------------------------------
import argparse   # Lee los parámetros que escribes en la terminal (--provider, --db, ...)
import json       # Convierte texto JSON en diccionarios de Python (para la respuesta del auditor)
import os         # Acceso a variables de entorno (aquí: GOOGLE_API_KEY)
import re         # Expresiones regulares: buscar/limpiar patrones dentro de textos
import sqlite3    # Librería estándar para leer bases de datos SQLite
import sys        # sys.exit() para terminar el programa con un mensaje de error
from pathlib import Path          # Manejo cómodo de rutas de archivos y carpetas
from typing import List, TypedDict  # Tipos para describir la forma del "estado" del grafo

# Mensajes que se envían al LLM: SystemMessage = instrucciones/rol, HumanMessage = el pedido.
from langchain_core.messages import HumanMessage, SystemMessage
# StateGraph = constructor del grafo; START y END = nodos especiales de inicio y fin.
from langgraph.graph import END, START, StateGraph

# ----------------------------------------------------------------------------
# CABECERA DE CONTACTO
# ----------------------------------------------------------------------------
# Texto fijo que se pega al inicio de CADA CV. Lo editas tú a mano una sola vez.
# El LLM nunca lo ve ni lo modifica: lo agrega el código al final (ver main()).
CONTACT_HEADER = """# [NOMBRE COMPLETO]

[EMAIL] | [TELÉFONO] | [CIUDAD, PAÍS] | [LINKEDIN] | [GITHUB / PORTAFOLIO]

---
"""

# ----------------------------------------------------------------------------
# PROMPTS (las instrucciones que se le dan al LLM en cada paso)
# ----------------------------------------------------------------------------

# Prompt del PASO 1 (analizar_oferta): le pide al LLM que "desarme" la oferta
# en requisitos, palabras clave y responsabilidades.
ANALYZE_SYSTEM = """Eres un analista de reclutamiento experto en sistemas ATS.
Lee la oferta de empleo y extrae, de forma concisa:
1. Título del cargo y seniority esperado.
2. Requisitos imprescindibles (hard skills, herramientas, años de experiencia).
3. Requisitos deseables.
4. Palabras clave exactas que un ATS buscaría (tal como aparecen en la oferta).
5. Responsabilidades principales y qué problema de negocio resuelve el rol.
6. Idioma de la oferta.
Responde en lista breve, sin inventar nada que no esté en la oferta."""

# Reglas del PASO 2 (redactar_cv). Se inyectan dentro del prompt de sistema.
# Aquí está la regla más importante: NO inventar nada.
WRITE_RULES = """REGLAS ESTRICTAS PARA REDACTAR EL CV:
- Usa ÚNICAMENTE hechos presentes en el CV base. Prohibido inventar empleos, fechas,
  títulos, certificaciones, herramientas, métricas o logros.
- Puedes: reordenar, priorizar, reformular, resaltar lo más relevante, omitir lo irrelevante
  y usar la terminología exacta de la oferta SOLO cuando describa algo que el CV base
  ya demuestra.
- Si la oferta pide algo que el CV base no respalda, NO lo incluyas.
- Estructura sugerida en Markdown: Resumen profesional (3-4 líneas orientado al cargo),
  Habilidades clave (alineadas a la oferta), Experiencia (viñetas con verbo de acción +
  resultado; las más relevantes primero), Educación, Certificaciones/Idiomas si existen.
- Máximo ~2 páginas. Sin tablas, sin imágenes, formato limpio compatible con ATS.
- NO incluyas datos de contacto ni el nombre: se agregan aparte. Empieza directamente
  por el Resumen profesional.
- Escribe en el mismo idioma que la oferta.
- Devuelve SOLO el CV en Markdown, sin comentarios ni bloques de código."""

# Prompt del PASO 3 (revisar): un "auditor" que compara CV generado vs CV base.
# Debe responder SOLO con JSON para que el código pueda leerlo.
REVIEW_SYSTEM = """Eres un auditor de veracidad de CVs. Compara el CV generado contra el CV base.
Marca como problema cualquier dato del CV generado que NO esté respaldado por el CV base
(empleos, fechas, cifras, herramientas, títulos, certificaciones inventados o exagerados),
y también si incluye datos de contacto.
Responde EXCLUSIVAMENTE con JSON válido, sin texto extra:
{"ok": true|false, "issues": ["problema 1", "problema 2"]}"""


# ----------------------------------------------------------------------------
# FÁBRICA DE LLMs  (aquí se elige Gemini u Ollama)
# ----------------------------------------------------------------------------
def build_llm(provider: str, model: str | None, temperature: float, base_url: str | None):
    """
    Crea y devuelve el objeto "chat model" de LangChain según el proveedor elegido.
    Es el único lugar del programa que sabe si usas Gemini u Ollama; el resto del
    código trata a ambos igual (todos exponen el método .invoke()).

    Parámetros:
        provider    : "gemini" (API de Google en la nube) u "ollama" (modelo local).
        model       : nombre del modelo. Si es None se usa un valor por defecto:
                      "gemini-2.5-flash" para Gemini, "gemma4" para Ollama.
        temperature : creatividad del modelo. 0 = muy determinista y literal;
                      valores altos = más variado/creativo. Para CVs conviene bajo
                      (0.2-0.4) para que no "invente".
        base_url    : dirección del servidor Ollama (solo aplica a Ollama).
                      Por defecto http://localhost:11434.

    Devuelve:
        Un objeto de LangChain con método .invoke(mensajes) -> respuesta.
    """
    if provider == "gemini":
        # Import dentro del if: así no necesitas tener instalada la librería de
        # Gemini si solo usas Ollama (y viceversa).
        from langchain_google_genai import ChatGoogleGenerativeAI

        # La clave de la API se lee desde la variable de entorno, nunca del código.
        if not os.getenv("GOOGLE_API_KEY"):
            sys.exit("Falta la variable de entorno GOOGLE_API_KEY para usar Gemini.")
        return ChatGoogleGenerativeAI(
            model=model or "gemini-2.5-flash", temperature=temperature
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model or "gemma4",
            temperature=temperature,
            base_url=base_url or "http://localhost:11434",
        )
    sys.exit(f"Proveedor no soportado: {provider}")


# ----------------------------------------------------------------------------
# FUNCIONES AUXILIARES PARA TRATAR LAS RESPUESTAS DEL LLM
# ----------------------------------------------------------------------------
def msg_text(msg) -> str:
    """
    Extrae el texto de la respuesta del LLM como un string limpio.

    Por qué existe: según el proveedor/versión, `msg.content` puede ser un string
    simple o una lista de "bloques" (texto, imágenes, etc.). Esta función unifica
    ambos casos para que el resto del código siempre reciba texto.

    Parámetros:
        msg : el mensaje que devuelve llm.invoke(...).

    Devuelve:
        Texto de la respuesta, sin espacios sobrantes al inicio/final.
    """
    content = msg.content
    if isinstance(content, str):          # caso simple: ya es texto
        return content.strip()
    parts = []
    for block in content:                 # caso lista de bloques
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


def strip_fences(text: str) -> str:
    """
    Quita las "vallas" de código Markdown (```markdown ... ```) que algunos
    modelos añaden aunque se les pida que no. Así el CV queda limpio.

    Parámetros:
        text : texto devuelto por el LLM.

    Devuelve:
        El mismo texto sin la valla de apertura ni la de cierre.
    """
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text.strip())  # valla de apertura
    return re.sub(r"\s*```$", "", text).strip()                  # valla de cierre


def parse_review(text: str) -> dict:
    """
    Convierte la respuesta del auditor (texto con JSON) en un diccionario Python.

    Es "tolerante a fallos": los modelos pequeños/locales a veces devuelven JSON
    roto o con texto extra. Si no se puede interpretar, se asume que todo está bien
    (ok=True) para no bloquear el proceso.

    Parámetros:
        text : respuesta cruda del auditor. Se espera algo como
               {"ok": false, "issues": ["inventó una certificación"]}

    Devuelve:
        dict con dos claves:
            "ok"     : bool, True si el CV es fiel al CV base.
            "issues" : lista de strings con los problemas detectados.
    """
    # Busca el primer bloque que empiece con { y termine con } (el JSON dentro del texto).
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"ok": True, "issues": []}
    try:
        data = json.loads(match.group(0))
        return {"ok": bool(data.get("ok", True)), "issues": list(data.get("issues", []))}
    except (json.JSONDecodeError, TypeError):
        return {"ok": True, "issues": []}


# ----------------------------------------------------------------------------
# GRAFO LANGGRAPH  (el "cerebro" del agente)
# ----------------------------------------------------------------------------
class CVState(TypedDict):
    """
    ESTADO del grafo: la "libreta compartida" que viaja por todos los pasos.
    Cada nodo lee de aquí lo que necesita y devuelve solo las claves que modifica.

    Claves:
        base_cv  : texto completo de tu cv_base.md (fuente de verdad). No cambia.
        job_text : texto de la oferta actual (todas las columnas de la fila). No cambia.
        analysis : resumen estructurado de la oferta, lo escribe analizar_oferta.
        cv_body  : cuerpo del CV (sin contacto), lo escribe/reescribe redactar_cv.
        issues   : lista de problemas del auditor. Vacía = CV aprobado.
        attempts : cuántas veces se ha redactado el CV (para limitar los reintentos).
    """
    base_cv: str
    job_text: str
    analysis: str
    cv_body: str
    issues: List[str]
    attempts: int


def build_graph(llm, max_revisions: int):
    """
    Construye y compila el grafo con los tres nodos y sus conexiones.

    Parámetros:
        llm           : el modelo creado por build_llm(). Los nodos lo usan
                        porque están definidos DENTRO de esta función (closure).
        max_revisions : cuántas reescrituras extra se permiten si el auditor
                        encuentra problemas. 0 = nunca reescribir; 1 = una vez.

    Devuelve:
        Un grafo compilado; se ejecuta con graph.invoke(estado_inicial).
    """

    # ---- NODO 1 ------------------------------------------------------------
    def analizar_oferta(state: CVState) -> dict:
        """
        Pide al LLM un análisis estructurado de la oferta (requisitos, palabras
        clave ATS, responsabilidades). Ese análisis guía la redacción del CV.

        Lee del estado  : job_text
        Escribe al estado: analysis
        """
        resp = llm.invoke(
            [SystemMessage(content=ANALYZE_SYSTEM), HumanMessage(content=state["job_text"])]
        )
        return {"analysis": msg_text(resp)}

    # ---- NODO 2 ------------------------------------------------------------
    def redactar_cv(state: CVState) -> dict:
        """
        Escribe el CV adaptado a la oferta usando SOLO el CV base.
        Si el auditor ya marcó problemas (state["issues"]), se los entrega junto
        con el borrador anterior para que los corrija (ciclo de mejora).

        Lee del estado  : base_cv, job_text, analysis, issues, cv_body, attempts
        Escribe al estado: cv_body (nuevo borrador), attempts (+1)
        """
        # Prompt de sistema: rol + CV base (la única fuente de verdad) + reglas.
        system = f"""Eres un redactor experto de CVs que maximiza las probabilidades de
entrevista sin faltar jamás a la verdad.

=== CV BASE DEL CANDIDATO (única fuente de verdad) ===
{state['base_cv']}

{WRITE_RULES}"""
        # Mensaje del usuario: la oferta y su análisis.
        user = f"""=== OFERTA DE EMPLEO ===
{state['job_text']}

=== ANÁLISIS DE LA OFERTA ===
{state['analysis']}
"""
        # Si venimos de una revisión fallida, se agregan los problemas y el borrador previo.
        if state["issues"]:
            user += (
                "\n=== CORRIGE ESTOS PROBLEMAS DETECTADOS EN TU BORRADOR ANTERIOR ===\n"
                + "\n".join(f"- {i}" for i in state["issues"])
                + f"\n\n=== BORRADOR ANTERIOR ===\n{state['cv_body']}\n"
            )
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return {"cv_body": strip_fences(msg_text(resp)), "attempts": state["attempts"] + 1}

    # ---- NODO 3 ------------------------------------------------------------
    def revisar(state: CVState) -> dict:
        """
        Auditor de veracidad: compara el CV generado con el CV base y reporta
        cualquier dato inventado o exagerado.

        Lee del estado  : base_cv, cv_body
        Escribe al estado: issues (lista vacía si todo está bien)
        """
        user = (
            f"=== CV BASE ===\n{state['base_cv']}\n\n"
            f"=== CV GENERADO ===\n{state['cv_body']}"
        )
        resp = llm.invoke([SystemMessage(content=REVIEW_SYSTEM), HumanMessage(content=user)])
        review = parse_review(msg_text(resp))
        return {"issues": [] if review["ok"] else review["issues"]}

    # ---- DECISIÓN (arista condicional) -------------------------------------
    def decidir(state: CVState) -> str:
        """
        Función de enrutamiento: decide a qué nodo ir después de "revisar".

        Regla: si hay problemas Y todavía quedan reintentos -> volver a
        "redactar_cv". En cualquier otro caso -> terminar (END).
        `attempts` ya cuenta la redacción inicial, por eso se compara con <=.

        Devuelve:
            El nombre del siguiente nodo ("redactar_cv") o END.
        """
        if state["issues"] and state["attempts"] <= max_revisions:
            return "redactar_cv"
        return END

    # ---- ENSAMBLAJE DEL GRAFO ---------------------------------------------
    g = StateGraph(CVState)                        # grafo que usa CVState como estado
    g.add_node("analizar_oferta", analizar_oferta) # registra cada nodo con un nombre
    g.add_node("redactar_cv", redactar_cv)
    g.add_node("revisar", revisar)
    g.add_edge(START, "analizar_oferta")           # inicio -> análisis
    g.add_edge("analizar_oferta", "redactar_cv")   # análisis -> redacción
    g.add_edge("redactar_cv", "revisar")           # redacción -> revisión
    # Tras revisar, decidir() elige: volver a redactar_cv o terminar.
    g.add_conditional_edges("revisar", decidir, {"redactar_cv": "redactar_cv", END: END})
    return g.compile()                             # congela el grafo para poder ejecutarlo


# ----------------------------------------------------------------------------
# BASE DE DATOS SQLITE
# ----------------------------------------------------------------------------
def fetch_rows(db_path: str, table: str, query: str | None):
    """
    Lee las ofertas de la base de datos y las entrega UNA POR UNA (generador).

    Parámetros:
        db_path : ruta al archivo .db de SQLite.
        table   : nombre de la tabla a leer (se usa si no das `query`).
        query   : consulta SQL opcional (ej: "SELECT * FROM jobs WHERE country='CL'").
                  Si se entrega, reemplaza a `table`. Útil para filtrar ofertas.

    Devuelve (con yield):
        Un diccionario por fila: {nombre_columna: valor, ...}.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row   # permite convertir cada fila a diccionario
    try:
        sql = query or f'SELECT * FROM "{table}"'
        for row in conn.execute(sql):
            yield dict(row)          # entrega una fila y "pausa" hasta que se pida la siguiente
    finally:
        conn.close()                 # siempre cierra la conexión, aunque haya errores


def row_to_text(row: dict) -> str:
    """
    Convierte una fila (diccionario) en texto plano legible para el LLM,
    una línea por columna con formato "columna: valor". Omite valores vacíos.

    Parámetros:
        row : fila de la base de datos como diccionario.

    Devuelve:
        Texto multilínea, por ejemplo:
            title: Data Scientist
            company: Acme
            description: Buscamos...
    """
    return "\n".join(f"{k}: {v}" for k, v in row.items() if v not in (None, ""))


def slugify(text: str, max_len: int = 40) -> str:
    """
    Convierte un texto en algo seguro para usar como nombre de archivo:
    reemplaza espacios y símbolos por "_" y lo recorta.

    Parámetros:
        text    : texto original (ej: "Data Scientist Sr. (Remoto)").
        max_len : largo máximo del resultado (por defecto 40 caracteres).

    Devuelve:
        Texto limpio (ej: "Data_Scientist_Sr_Remoto") o "sin_titulo" si queda vacío.
    """
    text = re.sub(r"[^\w\-]+", "_", str(text), flags=re.UNICODE).strip("_")
    return text[:max_len] or "sin_titulo"


# ----------------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------------
def main():
    """
    Orquesta todo: lee parámetros, valida el CV base, crea el LLM y el grafo,
    y recorre la base de datos generando un archivo de CV por cada oferta.
    """

    # ---- 1. PARÁMETROS DE LÍNEA DE COMANDOS -------------------------------
    p = argparse.ArgumentParser(description="Genera un CV adaptado por cada oferta en SQLite.")

    # Elección del modelo
    p.add_argument("--provider", choices=["gemini", "ollama"], default="ollama",
                   help="Qué LLM usar: 'gemini' (API en la nube) u 'ollama' (local).")
    p.add_argument("--model",
                   help="Nombre del modelo. Ej: gemini-2.5-flash | gemma4. "
                        "Si se omite, se usa el valor por defecto del proveedor.")
    p.add_argument("--ollama-url", default="http://localhost:11434",
                   help="Dirección del servidor Ollama (solo para --provider ollama).")
    p.add_argument("--temperature", type=float, default=0.3,
                   help="Creatividad del modelo (0 = literal, 1 = muy creativo). "
                        "Se recomienda 0.2-0.4 para CVs.")

    # Entradas
    p.add_argument("--cv", default="cv_base.md",
                   help="Archivo con tu experiencia real (fuente de verdad del agente).")
    p.add_argument("--db", required=True,
                   help="OBLIGATORIO. Ruta a la base SQLite con las ofertas.")
    p.add_argument("--table", default="jobs",
                   help="Tabla de la base de datos donde están las ofertas.")
    p.add_argument("--query",
                   help="Consulta SQL personalizada; reemplaza a --table (para filtrar filas).")

    # Nombres de columnas (solo se usan para nombrar los archivos de salida)
    p.add_argument("--id-col", default="id",
                   help="Columna con el identificador único de la oferta.")
    p.add_argument("--title-col", default="title",
                   help="Columna con el título del cargo.")

    # Salida y comportamiento
    p.add_argument("--out", default="output",
                   help="Carpeta donde se guardan los CVs generados.")
    p.add_argument("--max-revisions", type=int, default=1,
                   help="Cuántas veces puede reescribirse un CV si el auditor "
                        "detecta datos inventados (0 = ninguna).")
    p.add_argument("--overwrite", action="store_true",
                   help="Si se incluye, regenera CVs que ya existen. Si no, los omite.")
    args = p.parse_args()

    # ---- 2. CARGA Y VALIDACIÓN DEL CV BASE --------------------------------
    base_cv = Path(args.cv).read_text(encoding="utf-8")

    # Seguro anti-invención: si quedan marcas [CONFIRMAR ...] (cifras sin validar),
    # el programa se detiene para que no lleguen a un CV real.
    pendientes = re.findall(r"\[CONFIRMAR[^\]]*\]", base_cv)
    if pendientes:
        sys.exit(
            f"Tu CV base tiene {len(pendientes)} cifras sin confirmar ({', '.join(pendientes[:3])}...). "
            "Confirma, ajusta o elimina cada marca [CONFIRMAR ...] antes de generar CVs."
        )

    # ---- 3. PREPARACIÓN ----------------------------------------------------
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)   # crea la carpeta de salida si no existe

    llm = build_llm(args.provider, args.model, args.temperature, args.ollama_url)
    graph = build_graph(llm, args.max_revisions)

    # Contadores para el resumen final
    ok = failed = skipped = 0

    # ---- 4. BUCLE PRINCIPAL: UNA OFERTA = UN CV ---------------------------
    for idx, row in enumerate(fetch_rows(args.db, args.table, args.query), start=1):
        job_id = row.get(args.id_col, idx)   # si no existe la columna id, usa el nº de fila
        title = row.get(args.title_col, "")
        path = out_dir / f"cv_{job_id}_{slugify(title)}.md"

        # Reanudación: si el archivo ya existe y no pediste --overwrite, se salta.
        if path.exists() and not args.overwrite:
            print(f"[{idx}] omitido (ya existe): {path.name}")
            skipped += 1
            continue

        print(f"[{idx}] generando CV para: {title or job_id} ...")
        try:
            # Ejecuta el grafo completo (analizar -> redactar -> revisar [-> redactar...])
            # pasando el estado inicial. Devuelve el estado final.
            result = graph.invoke(
                {
                    "base_cv": base_cv,
                    "job_text": row_to_text(row),
                    "analysis": "",
                    "cv_body": "",
                    "issues": [],
                    "attempts": 0,
                }
            )
            # Cabecera de contacto (fija) + cuerpo generado por el LLM.
            content = CONTACT_HEADER + "\n" + result["cv_body"] + "\n"

            # Si tras los reintentos el auditor aún ve problemas, se deja un
            # comentario HTML al final del archivo para que lo revises a mano.
            if result["issues"]:
                content += (
                    "\n<!-- REVISAR MANUALMENTE: el auditor detectó posibles datos no "
                    "respaldados:\n" + "\n".join(f"- {i}" for i in result["issues"]) + "\n-->\n"
                )
                print("     ⚠ con advertencias del auditor (ver comentario al final)")
            path.write_text(content, encoding="utf-8")
            print(f"     ✓ {path}")
            ok += 1
        except Exception as e:  # un fallo en una oferta no detiene el resto del lote
            print(f"     ✗ error: {e}")
            failed += 1

    # ---- 5. RESUMEN FINAL --------------------------------------------------
    print(f"\nListo. Generados: {ok} | Omitidos: {skipped} | Errores: {failed}")


# Este bloque hace que main() solo se ejecute al correr `python agent.py`,
# y no cuando otro archivo importe este módulo.
if __name__ == "__main__":
    main()
