#!/usr/bin/env python3
"""
CONVERSOR MARKDOWN / DOCX  ->  HTML
====================================

Qué hace, en una frase:
    Convierte un CV en Markdown (o en .docx) a un documento HTML autónomo,
    con estilos de CV listos para imprimir (A4) o para ver en el navegador.

Se usa en dos lugares:
    1. `app.py` lo importa para servir la vista HTML de cada CV (endpoint
       `/api/cv/<id>/<archivo>?view=html`).
    2. Como script suelto, para generar los .html de toda la carpeta output:

           python md2html.py output/                       # todos los .md
           python md2html.py output/cv_1234_...md -o x.html
           python md2html.py salida.docx -o salida.html

No requiere dependencias: sólo la librería estándar de Python.
"""

from __future__ import annotations

import argparse
import html as html_mod
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# ----------------------------------------------------------------------------
# PARSEO DE MARKDOWN
# ----------------------------------------------------------------------------
FENCE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})\s*(?P<lang>[\w+#-]*)\s*$")
HEADING_RE = re.compile(r"^\s{0,3}(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
UL_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+])\s+(?P<text>.*)$")
OL_RE = re.compile(r"^(?P<indent>\s*)(?P<num>\d+[.)])\s+(?P<text>.*)$")
QUOTE_RE = re.compile(r"^\s{0,3}>\s?(?P<text>.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE_SPAN_RE = re.compile(r"(?P<ticks>`+)(?P<code>.+?)(?P=ticks)")
STRONG_RE = re.compile(r"(?P<fence>\*\*|__)(?P<text>.+?)(?P=fence)", re.DOTALL)
EM_RE = re.compile(r"(?<![\*\w])\*(?P<text>[^*\n]+)\*(?!\*)")
STRIKE_RE = re.compile(r"~~(?P<text>.+?)~~")
AUTOLINK_RE = re.compile(r"(?<![\"'>=])\bhttps?://[^\s<>\"')]+")
ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~>])")


def _inline(text: str) -> str:
    """Aplica el formato en línea (negrita, cursiva, código, enlaces, imágenes)."""
    # 1. Escapes -> placeholders para que no se interpreten los markdown.
    escapes: list[str] = []

    def _esc(m: re.Match) -> str:
        escapes.append(m.group(1))
        return f"\x00{len(escapes) - 1}\x00"

    text = ESCAPE_RE.sub(_esc, text)

    # 2. Código en línea primero: su contenido no se vuelve a interpretar.
    code_spans: list[str] = []

    def _code(m: re.Match) -> str:
        code_spans.append(m.group("code"))
        return f"\x01{len(code_spans) - 1}\x01"

    text = CODE_SPAN_RE.sub(_code, text)

    # 3. Imágenes y enlaces.
    def _img(m: re.Match) -> str:
        src = m.group("src")
        alt = html_mod.escape(m.group("alt"), quote=True)
        if src.startswith(("http://", "https://", "data:")):
            return f'<img src="{html_mod.escape(src, quote=True)}" alt="{alt}">'
        return f'<span class="missing-img">[{alt or "imagen"}]</span>'

    text = IMAGE_RE.sub(_img, text)
    text = LINK_RE.sub(
        lambda m: f'<a href="{html_mod.escape(m.group("href"), quote=True)}" '
                  f'target="_blank" rel="noopener">{html_mod.escape(m.group("text"))}</a>',
        text,
    )

    # 4. Negrita / cursiva / tachado.
    text = STRONG_RE.sub(lambda m: f"<strong>{m.group('text')}</strong>", text)
    text = EM_RE.sub(lambda m: f"<em>{m.group('text')}</em>", text)
    text = STRIKE_RE.sub(lambda m: f"<s>{m.group('text')}</s>", text)

    # 5. Texto plano: escapar el HTML.
    text = html_mod.escape(text, quote=False)

    # 6. URLs sueltas.
    text = AUTOLINK_RE.sub(
        lambda m: f'<a href="{m.group(0)}" target="_blank" rel="noopener">{m.group(0)}</a>', text
    )

    # 7. Restaurar código y escapes.
    for i, c in enumerate(code_spans):
        text = text.replace(f"\x01{i}\x01", f"<code>{html_mod.escape(c, quote=False)}</code>")
    for i, c in enumerate(escapes):
        text = text.replace(f"\x00{i}\x00", html_mod.escape(c, quote=False))
    return text


def _split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def markdown_to_html(md: str) -> str:
    """
    Convierte Markdown a un fragmento HTML (sin <html> ni <head>).

    Soporta lo habitual en un CV: encabezados, listas (anidadas), negrita,
    cursiva, código, enlaces, listas de tablas, citas, reglas y bloques de código.
    """
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    list_stack: list[str] = []   # pila de listas abiertas ("ul" / "ol")

    def close_lists(to_depth: int = 0):
        while len(list_stack) > to_depth:
            out.append(f"</{list_stack.pop()}>")

    def indent_level(indent: str) -> int:
        return len(indent.replace("\t", "    ")) // 2

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # --- bloque de código ---
        m = FENCE_RE.match(line)
        if m:
            close_lists()
            lang = m.group("lang") or ""
            i += 1
            buf = []
            while i < len(lines) and not FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html_mod.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html_mod.escape(chr(10).join(buf))}</code></pre>")
            continue

        # --- comentario HTML (p.ej. el aviso del auditor de agent.py) ---
        if stripped.startswith("<!--"):
            out.append(stripped)
            i += 1
            continue

        # --- tabla ---
        if "|" in line and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
            close_lists()
            header = _split_table_row(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            out.append("<table>")
            out.append("<thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in header) + "</tr></thead>")
            out.append("<tbody>")
            for r in rows:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue

        # --- línea en blanco ---
        if not stripped:
            # Si lo que sigue es otra lista del mismo tipo, se mantiene abierta
            # (así un bloque de viñetas con espacios en medio queda como una lista).
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = lines[j] if j < len(lines) else ""
            if not (UL_RE.match(nxt) or OL_RE.match(nxt)):
                close_lists()
            i += 1
            continue

        # --- regla horizontal ---
        if HR_RE.match(line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # --- encabezado ---
        m = HEADING_RE.match(line)
        if m:
            close_lists()
            level = len(m.group("hashes"))
            out.append(f"<h{level}>{_inline(m.group('text'))}</h{level}>")
            i += 1
            continue

        # --- cita ---
        m = QUOTE_RE.match(line)
        if m:
            close_lists()
            out.append(f"<blockquote>{_inline(m.group('text'))}</blockquote>")
            i += 1
            continue

        # --- listas ---
        m_ul = UL_RE.match(line)
        m_ol = OL_RE.match(line)
        if m_ul or m_ol:
            tag = "ul" if m_ul else "ol"
            m = m_ul or m_ol
            text = m.group("text")
            depth = indent_level(m.group("indent"))
            # Cierra las listas de niveles más profundos que el actual.
            while len(list_stack) > depth + 1:
                out.append(f"</{list_stack.pop()}>")
            # Si el tipo de lista cambia en este nivel, se cierra la anterior.
            if list_stack and list_stack[-1] != tag:
                close_lists(depth)
            if not list_stack or list_stack[-1] != tag:
                list_stack.append(tag)
                out.append(f"<{tag}>")
            out.append(f"<li>{_inline(text)}</li>")
            i += 1
            continue

        # --- párrafo ---
        close_lists()
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if (
                not nxt.strip()
                or HEADING_RE.match(nxt)
                or HR_RE.match(nxt)
                or UL_RE.match(nxt)
                or OL_RE.match(nxt)
                or QUOTE_RE.match(nxt)
                or FENCE_RE.match(nxt)
            ):
                break
            buf.append(nxt.strip())
            i += 1
        out.append(f"<p>{'<br>'.join(_inline(b) for b in buf)}</p>")

    close_lists()
    return "\n".join(out)


# ----------------------------------------------------------------------------
# CONVERSOR DOCX -> HTML  (sólo biblioteca estándar: zip + xml)
# ----------------------------------------------------------------------------
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


def _docx_paragraph_html(p) -> str:
    """Convierte un <w:p> en HTML, respetando negrita/cursiva y el estilo."""
    pPr = p.find(f"{W}pPr")
    style = ""
    is_list = False
    if pPr is not None:
        st = pPr.find(f"{W}pStyle")
        if st is not None:
            style = (st.get(f"{W}val") or "").lower()
        is_list = pPr.find(f"{W}numPr") is not None

    parts: list[str] = []
    for node in p.iter():
        tag = node.tag
        if tag == f"{W}t":
            txt = html_mod.escape(node.text or "", quote=False)
            parts.append(txt)
        elif tag == f"{W}tab":
            parts.append("&nbsp;&nbsp;")
        elif tag == f"{W}br":
            parts.append("<br>")
    text = "".join(parts).strip()

    # Negrita / cursiva: se detectan por los runs que envuelven el texto.
    runs_html: list[str] = []
    for r in p.findall(f"{W}r"):
        rPr = r.find(f"{W}rPr")
        bold = italic = False
        if rPr is not None:
            b = rPr.find(f"{W}b")
            i_ = rPr.find(f"{W}i")
            bold = b is not None and b.get(f"{W}val") not in ("0", "false")
            italic = i_ is not None and i_.get(f"{W}val") not in ("0", "false")
        rtxt: list[str] = []
        for node in r.iter():
            if node.tag == f"{W}t":
                rtxt.append(html_mod.escape(node.text or "", quote=False))
            elif node.tag == f"{W}tab":
                rtxt.append("&nbsp;&nbsp;")
            elif node.tag == f"{W}br":
                rtxt.append("<br>")
        t = "".join(rtxt)
        if not t:
            continue
        if bold:
            t = f"<strong>{t}</strong>"
        if italic:
            t = f"<em>{t}</em>"
        runs_html.append(t)
    if runs_html:
        text = "".join(runs_html).strip()

    if not text:
        return ""

    if style.startswith("heading"):
        try:
            level = min(int(style.replace("heading", "").strip() or 1), 6)
        except ValueError:
            level = 2
        return f"<h{level}>{text}</h{level}>"
    if "title" in style:
        return f"<h1>{text}</h1>"
    if "quote" in style:
        return f"<blockquote>{text}</blockquote>"
    if is_list or "list" in style:
        return f"<li>{text}</li>"
    return f"<p>{text}</p>"


def docx_to_html(path: Path) -> str:
    """Extrae el contenido de un .docx y lo devuelve como fragmento HTML."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(f"{W}body")
    if body is None:
        return ""

    chunks: list[str] = []
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            chunks.append("</ul>")
            in_ul = False
        if in_ol:
            chunks.append("</ol>")
            in_ol = False

    for node in list(body):
        if node.tag == f"{W}p":
            frag = _docx_paragraph_html(node)
            if not frag:
                continue
            if frag.startswith("<li>"):
                # Word no dice si la lista es ordenada: se usa el primer marcador.
                num = node.find(f"{W}pPr/{W}numPr/{W}numId")
                is_ordered = False
                if num is not None:
                    val = num.get(f"{W}val") or ""
                    is_ordered = val.isdigit() and int(val) > 0
                if is_ordered and not in_ol:
                    close_lists()
                    chunks.append("<ol>")
                    in_ol = True
                elif not is_ordered and not in_ul:
                    close_lists()
                    chunks.append("<ul>")
                    in_ul = True
                chunks.append(frag)
            else:
                close_lists()
                chunks.append(frag)
        elif node.tag == f"{W}tbl":
            close_lists()
            rows = []
            for tr in node.findall(f"{W}tr"):
                cells = []
                for tc in tr.findall(f"{W}tc"):
                    cell = " ".join(
                        _docx_paragraph_html(p) for p in tc.findall(f"{W}p")
                    )
                    cells.append(f"<td>{cell}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            chunks.append("<table>" + "".join(rows) + "</table>")

    close_lists()
    return "\n".join(c for c in chunks if c)


# ----------------------------------------------------------------------------
# DOCUMENTO HTML AUTÓNOMO (con estilos de CV, listo para imprimir)
# ----------------------------------------------------------------------------
PAGE_CSS = """
:root{
  --ink:#15181d; --soft:#4a5260; --line:#dfe3ea; --accent:#1f4fd8; --paper:#ffffff;
}
*{box-sizing:border-box}
body{
  margin:0; background:#eef1f6; color:var(--ink);
  font:15px/1.62 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  padding:28px 16px 60px;
}
.page{
  max-width:820px; margin:0 auto; background:var(--paper); padding:52px 58px 60px;
  border-radius:6px; box-shadow:0 10px 40px rgba(20,30,60,.12);
}
h1{font-size:24px; margin:.15em 0 .4em; letter-spacing:-.2px}
h2{font-size:18px; margin:1.5em 0 .5em; padding-bottom:6px; border-bottom:2px solid var(--line);
   text-transform:uppercase; letter-spacing:.6px; color:#222b38}
h3{font-size:15.5px; margin:1.1em 0 .35em; color:#1d2634}
h4,h5,h6{font-size:14px; margin:.9em 0 .3em; color:#333c4a}
p{margin:.55em 0}
ul,ol{margin:.5em 0 .7em 1.15em; padding:0}
li{margin:.24em 0}
hr{border:0; border-top:1px solid var(--line); margin:1.6em 0}
a{color:var(--accent)}
code{background:#f1f3f7; padding:1px 5px; border-radius:4px; font-size:.92em;
     font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:#f7f8fb; border:1px solid var(--line); border-radius:8px; padding:12px 14px;
    overflow-x:auto; font-size:12.5px; line-height:1.5}
blockquote{border-left:3px solid #c9d2e4; margin:.8em 0; padding:.35em 0 .35em 14px; color:var(--soft)}
table{border-collapse:collapse; margin:1em 0; width:100%}
th,td{border:1px solid var(--line); padding:6px 10px; text-align:left; font-size:13.5px}
th{background:#f4f6fa}
img{max-width:100%}
.missing-img{color:#a33}
.doc-title{
  display:flex; justify-content:space-between; align-items:flex-end; gap:16px;
  border-bottom:3px solid var(--ink); padding-bottom:12px; margin-bottom:6px;
}
.doc-title h1{margin:0}
.doc-title .meta{color:var(--soft); font-size:12.5px; text-align:right; white-space:nowrap}
.subtitle{color:var(--soft); font-size:14px; margin:.2em 0 1.2em}
@media print{
  body{background:#fff; padding:0}
  .page{box-shadow:none; border-radius:0; max-width:none; padding:0 12mm}
  @page{size:A4; margin:14mm}
  h2{page-break-after:avoid}
  li{page-break-inside:avoid}
}
"""

PRINT_BUTTON_JS = """
(function(){
  var b=document.getElementById('btn-print');
  if(b) b.addEventListener('click', function(){ window.print(); });
})();
"""


def render_document(
    body_html: str,
    title: str = "CV",
    subtitle: str = "",
    meta: str = "",
    include_toolbar: bool = True,
) -> str:
    """
    Envuelve un fragmento HTML en un documento autónomo con estilos de CV.

    Parámetros:
        body_html     : contenido ya convertido a HTML.
        title         : título del documento (ej. el cargo).
        subtitle      : línea secundaria (ej. la empresa).
        meta          : texto pequeño a la derecha (ej. la fecha o el id).
        include_toolbar: si True, agrega el botón "Imprimir / Guardar PDF".
    """
    toolbar = (
        '<div style="max-width:820px;margin:0 auto 14px;display:flex;gap:8px;justify-content:flex-end">'
        '<button id="btn-print" style="font:13px/1 sans-serif;padding:8px 14px;border-radius:8px;'
        'border:1px solid #c8d0dd;background:#fff;cursor:pointer">🖨 Imprimir / Guardar PDF</button>'
        "</div>"
    ) if include_toolbar else ""

    title_block = (
        '<div class="doc-title"><h1></h1>'
        f'<div class="meta">{html_mod.escape(meta)}</div></div>'
    ) if title or meta else ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_mod.escape(title)}</title>
<style>{PAGE_CSS}</style>
</head>
<body>
{toolbar}
<div class="page">
{title_block}
{f'<div class="subtitle">{html_mod.escape(subtitle)}</div>' if subtitle else ''}
{body_html}
</div>
<script>{PRINT_BUTTON_JS}</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# CLI: convertir archivos sueltos
# ----------------------------------------------------------------------------
def convert_file(src: Path, dst: Path | None = None, title: str = "", subtitle: str = "") -> Path:
    """Convierte un .md o .docx a .html y devuelve la ruta escrita."""
    if dst is None:
        dst = src.with_suffix(".html")

    if src.suffix.lower() == ".docx":
        body = docx_to_html(src)
    else:
        body = markdown_to_html(src.read_text(encoding="utf-8"))

    html = render_document(
        body,
        title=title or src.stem,
        subtitle=subtitle,
        meta=src.stem,
        include_toolbar=True,
    )
    dst.write_text(html, encoding="utf-8")
    return dst


def main():
    p = argparse.ArgumentParser(description="Convierte CVs en Markdown o .docx a HTML.")
    p.add_argument("input", nargs="+", help="Archivo(s) .md / .docx de entrada (o una carpeta).")
    p.add_argument("-o", "--output", help="Archivo .html de salida (solo con una entrada).")
    p.add_argument("--outdir", help="Carpeta donde escribir los .html (por defecto, junto al original).")
    p.add_argument("--title", default="", help="Título del documento.")
    p.add_argument("--subtitle", default="", help="Subtítulo (por ejemplo, la empresa).")
    args = p.parse_args()

    files: list[Path] = []
    for item in args.input:
        pth = Path(item)
        if pth.is_dir():
            files += sorted(list(pth.glob("*.md")) + list(pth.glob("*.docx")))
        else:
            files.append(pth)

    if not files:
        sys_exit("No se encontraron archivos para convertir.")
    if args.output and len(files) > 1:
        sys_exit("--output solo se puede usar con un único archivo de entrada.")

    outdir = Path(args.outdir) if args.outdir else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    for f in files:
        out = Path(args.output) if (args.output and len(files) == 1) else None
        if out is None and outdir is not None:
            out = outdir / (f.stem + ".html")
        dst = convert_file(f, out, title=args.title, subtitle=args.subtitle)
        print(f"✓ {f}  ->  {dst}")


def sys_exit(msg: str):
    import sys
    sys.exit(msg)


if __name__ == "__main__":
    main()
