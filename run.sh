#!/usr/bin/env bash
# Script para ejecutar el scraper usando el entorno virtual automáticamente

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activar entorno virtual
source "${SCRIPT_DIR}/venv/bin/activate"

# Ejecutar el scraper pasando los argumentos recibidos
python "${SCRIPT_DIR}/linkedin_scraper.py" "$@"

