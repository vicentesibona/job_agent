#!/usr/bin/env bash
# Lanzador de la app web (revisor de ofertas vs CVs).
# Usa el entorno virtual del proyecto para disponer de `markdown` (render.py).

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activar entorno virtual
source "${SCRIPT_DIR}/venv/bin/activate"

# Ejecutar la app pasando los argumentos recibidos
python "${SCRIPT_DIR}/app.py" "$@"
