# LinkedIn Job Scraper - Data Science & Business Intelligence (Santiago, Chile)

Este script en Python busca y extrae automáticamente ofertas de empleo desde LinkedIn (sin requerir credenciales ni iniciar sesión), enfocado en el mercado laboral de Santiago de Chile para las áreas de:
- **Científico de Datos**
- **Data Scientist**
- **Business Intelligence (BI)**
- **Inteligencia de Negocios**

Los datos se guardan de forma **incremental en una Base de Datos local SQLite**, y adicionalmente se exportan a **CSV** y **JSON**.

---

## 📌 Datos extraídos por cada empleo

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `identificador` | `TEXT (PK)` | ID único de la vacante en LinkedIn (ej. `4479227139`). |
| `empleo` | `TEXT` | Título del cargo o publicación. |
| `empresa` | `TEXT` | Nombre de la empresa u organización contratante. |
| `ubicacion` | `TEXT` | Ubicación geográfica (ej. *Gran Santiago, Región Metropolitana*). |
| `url` | `TEXT` | Enlace directo a la oferta de trabajo en LinkedIn. |
| `fecha_creacion` | `TEXT (ISO)` | **Fecha de publicación o creación del empleo** en formato estándar `YYYY-MM-DD` (ej. `2026-10-14`), ideal para ordenamiento y consultas temporales. |
| `fecha_publicacion_texto` | `TEXT` | Texto original relativo mostrado por LinkedIn (ej. *Hace 16 horas* o *1 week ago*). |
| `responsabilidades` | `TEXT` | Funciones, tareas y responsabilidades detectadas automáticamente. |
| `requisitos` | `TEXT` | Calificaciones, tecnologías y requisitos detectados. |
| `descripcion` | `TEXT` | Texto completo y limpio de la oferta laboral. |
| `nivel_experiencia` | `TEXT` | Nivel de experiencia (*Sin experiencia*, *Entry level*, *Mid-Senior*, etc.). |
| `tipo_empleo` | `TEXT` | Tipo de contrato o jornada (*Jornada completa*, *Contrato*, etc.). |
| `funcion_laboral` | `TEXT` | Área o función laboral declarada. |
| `industrias` | `TEXT` | Sector industrial de la empresa. |
| `num_postulantes` | `TEXT` | Número aproximado de solicitudes registradas. |
| `criterio_busqueda` | `TEXT` | Palabra clave con la que fue localizado el puesto. |
| `fecha_extraccion` | `TEXT` | Timestamp del momento de extracción (`YYYY-MM-DD HH:MM:SS`). |

---

## 🗄️ Base de Datos Local (SQLite)

El script utiliza SQLite (incluido por defecto en Python) guardando los datos en `empleos_santiago.db`:

- **Guardado Incremental e Inmediato**: Cada empleo se guarda al momento de ser descargado. Si detienes el script (con `Ctrl+C`) o hay un fallo de red, **no pierdes nada**.
- **Deduplicación automática (UPSERT)**: Al usar `identificador` como clave primaria, si ejecutas el script varias veces no se duplicarán ofertas; simplemente se actualizarán con los datos más recientes.
- **Consultas SQL directas**: Puedes consultar la base de datos desde Python, DBeaver, SQLite Viewer o la terminal:

```bash
# Ejemplo: Ver las 10 ofertas más recientes creadas en octubre
sqlite3 empleos_santiago.db "SELECT fecha_creacion, empleo, empresa FROM empleos ORDER BY fecha_creacion DESC LIMIT 10;"
```

---

## 🚀 Entorno Virtual e Instalación

El entorno virtual ya se encuentra creado y configurado en el directorio `venv/` con todas las dependencias instaladas.

Si alguna vez necesitas recrearlo manualmente:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

---

## 💻 Formas de Ejecución

### Opción 1: Usando el script lanzador directo (Recomendado)
Puedes ejecutar directamente `./run.sh` sin necesidad de activar el entorno a mano:

```bash
# Ejecución por defecto (100 ofertas)
./run.sh

# Pasando argumentos personalizados
./run.sh --limit 50
./run.sh --stats
```

### Opción 2: Activando el entorno virtual en la terminal
```bash
source venv/bin/activate
python linkedin_scraper.py
```

Esto creará automáticamente:
1. `empleos_santiago.db` (Base de datos SQLite local)
2. `empleos_santiago.csv` (CSV con UTF-8 BOM para Excel)
3. `empleos_santiago.json` (JSON estructurado)

### 2. Ver estadísticas de la base de datos
Para inspeccionar rápidamente las ofertas ya almacenadas sin volver a scrapear:
```bash
python3 linkedin_scraper.py --stats
```

### 3. Opciones de línea de comandos (CLI)

```bash
# Recolectar 50 empleos
python3 linkedin_scraper.py --limit 50

# Cambiar nombre base de los archivos generados
python3 linkedin_scraper.py --limit 100 --output mis_empleos

# Especificar una ruta o nombre personalizado para la base de datos SQLite
python3 linkedin_scraper.py --db mi_base_datos.db

# Guardar ÚNICAMENTE en la base de datos (sin generar CSV ni JSON)
python3 linkedin_scraper.py --no-csv --no-json

# Ajustar el retardo entre peticiones para mayor precaución
python3 linkedin_scraper.py --delay 2.0
```

### Tabla de Parámetros

| Parámetro | Abreviatura | Valor por defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `--limit` | `-l` | `100` | Número total de ofertas únicas a recopilar. |
| `--output` | `-o` | `"empleos_santiago"` | Nombre base de los archivos generados (.db, .csv, .json). |
| `--db` | | `"<output>.db"` | Ruta específica para el archivo SQLite. |
| `--location` | | `"Santiago, Chile"` | Ubicación geográfica de la búsqueda. |
| `--delay` | `-d` | `1.5` | Segundos base de pausa entre peticiones. |
| `--keywords` | `-k` | 4 términos solicitados | Lista de palabras clave a consultar. |
| `--stats` | | `False` | Muestra resumen de la base de datos y finaliza. |
| `--no-csv` | | `False` | Desactiva exportación a CSV. |
| `--no-json` | | `False` | Desactiva exportación a JSON. |

---

## 🖥️ Revisor de Ofertas vs CVs Generados (`app.py`)

App web local para revisar **en una misma pantalla** la oferta original (desde
`empleos_santiago.db`) y el **CV generado** por el agente (carpeta `output/`),
emparejados por `identificador`.

```bash
./run_app.sh                  # http://localhost:8000  (usa el venv: render.py + PDF)
./run_app.sh --port 8080 --open

python app.py                 # también funciona con el Python del sistema
```

- **Panel izquierdo**: lista de todas las ofertas, con búsqueda por cargo/empresa/id,
  filtros "solo con CV" / "ocultar sin CV" y aviso `CV ✓` / `sin CV`.
- **Panel derecho**: dos columnas — *Oferta original* (metadatos: **empresa**,
  ubicación, fechas, seniority, tipo de empleo, link a LinkedIn; más
  responsabilidades, requisitos y descripción) y *CV generado* (Markdown renderizado,
  con el aviso del auditor del agente destacado arriba si lo hay).
- **Vistas del CV**: conmutador `Markdown` / `HTML`. La vista HTML muestra el CV como
  documento web con el diseño de `render.py` (A4, una columna, estilos de CV) y
  funciona con CVs en `.md` **y en `.docx`** (el `.docx` se convierte al vuelo).
  Botones: *Abrir HTML ↗* (documento suelto en pestaña nueva), *Descargar HTML*,
  *PDF* (si está WeasyPrint instalado) y *Original* (el `.md`/`.docx` tal cual).
- Teclas: `/` busca, `j`/`k` navegan entre ofertas, `Esc` sale de la búsqueda.
- No modifica `agent.py` ni `linkedin_scraper.py`: solo los lee.
- Sin dependencias obligatorias: si `render.py` o `markdown` no están disponibles,
  la app usa `md2html` (render propio, sólo biblioteca estándar).

Para que los CVs aparezcan, `agent.py` debe correr con los nombres de columna de esta BD:

```bash
python agent.py --db empleos_santiago.db --table empleos \
  --id-col identificador --title-col empleo --out output
```

| Parámetro | Valor por defecto | Descripción |
| :--- | :--- | :--- |
| `--db` | `empleos_santiago.db` | Base SQLite con la tabla `empleos`. |
| `--output` | `output` | Carpeta con los CVs generados. |
| `--host` | `127.0.0.1` | `0.0.0.0` para exponer en la red local. |
| `--port` | `8000` | Puerto HTTP (si está ocupado se usa el siguiente libre). |
| `--open` | `False` | Abre el navegador automáticamente. |

### API interna

| Endpoint | Devuelve |
| :--- | :--- |
| `GET /api/jobs?q=&only_cv=` | Lista de ofertas + estadísticas. |
| `GET /api/job/<id>` | Oferta completa (metadatos, secciones, CVs, si hay PDF). |
| `GET /api/cv/<id>/<archivo>` | El CV en texto crudo. |
| `GET /api/cv/<id>/<archivo>?view=html` | El CV como documento HTML. |
| `GET /api/cv/<id>/<archivo>?view=pdf` | El CV como PDF (requiere WeasyPrint). |
| `GET /api/cv/<id>/<archivo>?download=1` | Descarga el archivo original. |

---

## 🧮 Contar tokens de Ollama

Dos formas de medirlo, según lo que necesites.

### 1. Por corrida del agente (`contador_tokens.py`)

Mide los tokens de **cada oferta**, sin modificar `agent.py` (lo envuelve en
memoria: intercepta `build_llm` y `fetch_rows`).

```bash
./venv/bin/python contador_tokens.py --provider ollama --model gemma4:12b \
  --db empleos_santiago.db --table empleos \
  --id-col identificador --title-col empleo --out output --overwrite

./venv/bin/python contador_tokens.py --reporte     # historial acumulado
```

Salida al terminar:

```
📊 RESUMEN DE TOKENS
Modelo        : gemma4:12b
Llamadas LLM  : 5
Tokens entrada: 16,381
Tokens salida : 42,204
Tokens TOTALES: 58,585
id            archivo                        calls   entrada   salida
4479254003    cv_4479254003_Data_Scientist.md     5    16,381   42,204
```

El historial queda en `logs/tokens.jsonl` (una línea JSON por corrida) y la app lo
muestra: pastilla 🧮 con los tokens de cada CV + total en la barra superior.

### 2. Uso total de Ollama en la máquina (`ver_tokens_ollama.sh`)

Lee el log del servicio `ollama`, donde cada petición deja su desglose de tokens.

```bash
./ver_tokens_ollama.sh              # desde que arrancó el servicio
./ver_tokens_ollama.sh --since today
```

```
 Peticiones (respuestas)  : 3389
 Tokens de entrada        : 112364
 Tokens de salida         : 619335
 TOTAL                    : 731699
```

También sirve a mano: `curl -s localhost:11434/api/chat -d '{...}'` devuelve
`prompt_eval_count` (entrada) y `eval_count` (salida) en cada respuesta.

---

## 📄 CVs en HTML (`md2html.py`)

Conversor independiente (sólo biblioteca estándar) que transforma un CV en Markdown
o `.docx` a un **documento HTML autónomo**, con estilos de CV listos para imprimir
(A4) o guardar como PDF.

```bash
python md2html.py salida.docx -o salida.html      # un archivo
python md2html.py output/ --outdir output_html/    # toda la carpeta
python md2html.py output/cv_1234_Python.md --title "Data Scientist" --subtitle "Acme"
```

- `.md` → HTML con encabezados, listas anidadas, negrita/cursiva, código, tablas,
  enlaces e imágenes.
- `.docx` → HTML con párrafos, negrita/cursiva, listas ordenadas y tablas
  (se lee el `word/document.xml` con `zipfile` + `xml`, sin `python-docx`).
- La app usa este mismo módulo para servir la vista HTML de cada CV
  (`/api/cv/<id>/<archivo>?view=html`).
