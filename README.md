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
