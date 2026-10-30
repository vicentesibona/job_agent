"""
RENDER: convierte el CV en Markdown a un documento con diseño (HTML y/o PDF).

Idea: el LLM escribe contenido con una estructura Markdown fija y este módulo le
da el diseño. Así el modelo se concentra en el texto y el diseño es siempre
consistente. El diseño es de UNA columna, sin tablas ni imágenes (compatible con ATS).

Estructura Markdown esperada (la exige WRITE_RULES en agent.py):
    # Nombre
    línea de contacto
    ---
    ## Sección
    ### Cargo — Empresa
    *Fechas | Ciudad*
    - viñeta
    - **Categoría:** habilidad 1, habilidad 2     (se muestra como tabla de habilidades)
"""
import re
from pathlib import Path

import markdown

ACCENT = "#1d4e89"   # color de acento (títulos y líneas). Cámbialo a tu gusto.
INK = "#1b1f24"      # color del texto principal
MUTED = "#5b6573"    # color de textos secundarios (fechas, contacto)

CSS = f"""
@page {{ size: A4; margin: 13mm 16mm; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: "Inter", "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 9.6pt; line-height: 1.42; color: {INK}; margin: 0;
}}
@media screen {{ body {{ max-width: 820px; margin: 0 auto; padding: 28px 32px; }} }}

/* Nombre y contacto */
h1 {{ font-size: 25pt; line-height: 1.1; margin: 0; letter-spacing: .01em; color: {ACCENT}; font-weight: 700; }}
h1 + p {{ margin: 5pt 0 0; font-size: 9pt; color: {MUTED}; }}
hr {{ border: 0; border-top: 2.2pt solid {ACCENT}; margin: 9pt 0 2pt; }}

/* Secciones */
h2 {{
  font-size: 10pt; text-transform: uppercase; letter-spacing: .14em; color: {ACCENT};
  border-bottom: .8pt solid #c9d3e0; padding-bottom: 2.5pt; margin: 13pt 0 6pt; font-weight: 700;
  page-break-after: avoid;
}}
h2 + p {{ margin: 0; font-size: 10pt; }}

/* Cargo + fechas en la misma línea */
.role {{ display: flex; justify-content: space-between; align-items: baseline; margin-top: 8pt; page-break-after: avoid; }}
.role h3 {{ font-size: 10.4pt; margin: 0; font-weight: 700; flex: 1; }}
.role .meta {{ font-size: 8.8pt; color: {MUTED}; white-space: nowrap; padding-left: 12pt; }}
.sub {{ font-size: 8.8pt; color: {MUTED}; margin: 1pt 0 0; page-break-after: avoid; }}
h3 {{ font-size: 10.4pt; margin: 8pt 0 0; }}

/* Viñetas */
ul {{ margin: 3pt 0 0; padding-left: 13pt; }}
li {{ margin: 0 0 2.2pt; page-break-inside: avoid; }}
li::marker {{ color: {ACCENT}; }}

/* Habilidades: etiqueta | lista */
ul.skills {{ list-style: none; padding-left: 0; }}
ul.skills li {{ display: flex; margin-bottom: 3pt; }}
ul.skills li strong {{ flex: 0 0 41mm; color: {ACCENT}; font-weight: 700; }}
ul.skills li span {{ flex: 1; }}

p {{ margin: 3pt 0; }}
em {{ font-style: italic; }}
"""



# ----------------------------------------------------------------------------
# NORMALIZADOR: repara la estructura cuando el LLM no respeta el formato Markdown
# ----------------------------------------------------------------------------
# Títulos de sección conocidos (inglés y español) -> tipo de sección.
SECTION_TITLES = {
    "summary": ["professional summary", "summary", "profile", "resumen profesional", "resumen", "perfil profesional", "perfil"],
    "achievements": ["selected achievements", "key achievements", "achievements", "highlights", "logros destacados", "logros"],
    "skills": ["key skills", "skills", "technical skills", "core skills", "habilidades clave", "habilidades técnicas", "habilidades"],
    "experience": ["professional experience", "work experience", "experience", "experiencia profesional", "experiencia laboral", "experiencia"],
    "projects": ["personal projects", "projects", "proyectos personales", "proyectos"],
    "education": ["education", "educación", "educacion", "formación académica", "formación"],
    "languages": ["languages", "idiomas"],
    "certifications": ["certifications", "certificaciones"],
}
_TITLE_TO_KIND = {t: k for k, ts in SECTION_TITLES.items() for t in ts}
# Una línea con estas pistas se considera "fechas" (años, presente, proyectos puntuales...).
_DATE_HINT = re.compile(r"(19|20)\d{2}|present|presente|actualidad|project-based|proyectos? puntuales|varias fechas", re.I)


def _clean_title(line: str) -> str:
    return re.sub(r"^[#*\s]+|[*:\s]+$", "", line).strip().lower()


def normalize_markdown(md_text: str) -> str:
    """
    Deja el Markdown en la estructura que espera el diseño, aunque el LLM no la respete:
      - títulos conocidos ("Professional Summary", "Habilidades"...) -> "## Título"
      - línea de cargo + línea de fechas -> "### Cargo" + "*fechas*"
      - viñetas "Etiqueta: a, b" en Habilidades/Idiomas -> "- **Etiqueta:** a, b"
    Es idempotente: si el texto ya está bien estructurado, no lo cambia.
    """
    lines = md_text.replace("\r\n", "\n").split("\n")
    out, kind, i = [], None, 0

    def blank():
        if out and out[-1] != "":
            out.append("")

    while i < len(lines):
        s = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not s:
            blank(); i += 1; continue
        # Cabecera (nombre, contacto, línea) se deja tal cual
        if re.match(r"^#\s", s) or s == "---":
            blank(); out.append(s); blank(); i += 1; continue
        # Título de sección (con o sin "##")
        key = _clean_title(s)
        if not s.startswith("-") and len(s) < 60 and (key in _TITLE_TO_KIND):
            kind = _TITLE_TO_KIND[key]
            blank(); out.append("## " + re.sub(r"^[#*\s]+|[*:\s]+$", "", s)); blank(); i += 1; continue
        if s.startswith("## "):
            kind = _TITLE_TO_KIND.get(_clean_title(s))
            blank(); out.append(s); blank(); i += 1; continue
        # "### Cargo" ya marcado: asegurar que la línea siguiente de fechas quede en cursiva
        if s.startswith("### "):
            blank(); out.append(s); i += 1
            n = lines[i].strip() if i < len(lines) else ""
            if n.startswith("*") and n.endswith("*") and not n.startswith("**"):
                out.append(n); i += 1                    # fechas ya en cursiva
            elif n and not n.startswith(("-", "*", "#")) and _DATE_HINT.search(n):
                out.append("*" + n + "*"); i += 1
            blank(); continue
        # Cargo/título sin marcar seguido de línea de fechas
        if (not s.startswith(("-", "*", "#")) and nxt and not nxt.startswith(("-", "#"))
                and _DATE_HINT.search(nxt) and not _DATE_HINT.fullmatch(s) and len(s) < 160):
            blank(); out.append("### " + s); out.append("*" + nxt.strip("* ") + "*"); blank(); i += 2; continue
        # Viñetas
        if s.startswith("- "):
            text = s[2:].strip()
            if kind in ("skills", "languages") and ":" in text and not text.startswith("**"):
                label, rest = text.split(":", 1)
                text = f"**{label.strip()}:** {rest.strip()}"
            if out and out[-1] != "" and not out[-1].startswith("- "):
                blank()
            out.append("- " + text); i += 1; continue
        out.append(s); i += 1
    return "\n".join(out).strip() + "\n"


def _style_skills(html: str) -> str:
    """Si todas las viñetas de una lista son '**Etiqueta:** texto', la muestra como tabla de habilidades."""
    def repl(m):
        block = m.group(1)
        items = re.findall(r"<li>(.*?)</li>", block, flags=re.S)
        if items and all(re.match(r"<strong>[^<]*:</strong>", i) for i in items):
            rows = "".join(
                "<li>" + re.sub(r"^(<strong>[^<]*</strong>)\s*(.*)$", r"\1<span>\2</span>", i, flags=re.S) + "</li>"
                for i in items
            )
            return f'<ul class="skills">{rows}</ul>'
        return m.group(0)
    return re.sub(r"<ul>(.*?)</ul>", repl, html, flags=re.S)


def md_to_html(md_text: str, title: str = "CV") -> str:
    """Markdown -> HTML completo con estilos incrustados (un solo archivo)."""
    body = markdown.markdown(normalize_markdown(md_text), extensions=["sane_lists"])
    # "### Cargo" seguido de "*fechas*"  ->  fila con cargo a la izquierda y fechas a la derecha
    def _role(m):
        """Título a la izquierda, SOLO la fecha a la derecha; el resto (ciudad, distinción,
        tecnologías) va debajo en una línea gris a ancho completo."""
        title, meta = m.group(1), m.group(2)
        parts = [p.strip() for p in meta.split("|")]
        date = parts[0] if (_DATE_HINT.search(parts[0]) and len(parts[0]) <= 40) else ""
        rest = [p for p in (parts[1:] if date else parts) if p]
        sub = f'<div class="sub">{" | ".join(rest)}</div>' if rest else ""
        return f'<div class="role"><h3>{title}</h3><span class="meta">{date}</span></div>{sub}'

    body = re.sub(r"<h3>(.*?)</h3>\s*<p><em>(.*?)</em></p>", _role, body, flags=re.S)
    body = _style_skills(body)
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )


def html_to_pdf(html: str, pdf_path: Path) -> None:
    """HTML -> PDF con WeasyPrint. En Windows requiere GTK (ver DOCUMENTACION.md)."""
    try:
        from weasyprint import HTML
    except Exception as e:  # ImportError u OSError por librerías del sistema
        raise RuntimeError(
            "No se pudo cargar WeasyPrint. Usa --formats md,html y abre el HTML en el "
            "navegador -> Imprimir -> Guardar como PDF. Detalle: " + str(e)
        )
    HTML(string=html).write_pdf(str(pdf_path))


def render(md_text: str, base_path: Path, formats: set) -> list:
    """
    Genera los formatos pedidos junto al .md.

    Parámetros:
        md_text   : CV completo en Markdown (cabecera de contacto incluida).
        base_path : ruta SIN extensión (ej: output/cv_1_Data_Scientist).
        formats   : subconjunto de {"html", "pdf"} ("md" lo guarda agent.py).

    Devuelve: lista de rutas creadas.
    """
    created = []
    if not formats & {"html", "pdf"}:
        return created
    html = md_to_html(md_text, title=base_path.name)
    if "html" in formats:
        p = base_path.with_suffix(".html")
        p.write_text(html, encoding="utf-8")
        created.append(p)
    if "pdf" in formats:
        p = base_path.with_suffix(".pdf")
        html_to_pdf(html, p)
        created.append(p)
    return created
