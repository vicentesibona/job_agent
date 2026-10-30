#!/usr/bin/env bash
# Muestra los tokens usados por Ollama, leídos del log del servicio.
#
#   ./ver_tokens_ollama.sh          # desde que arranque el servicio
#   ./ver_tokens_ollama.sh --hoy    # solo lo de hoy
#
# Cada petición a Ollama deja en el log una línea con el desglose:
#   prompt eval time =  208.13 ms /    20 tokens (...)
#   eval time        =  672.15 ms /    30 tokens (...)

set -e
if [ $# -gt 0 ]; then
  LOG=$(journalctl -u ollama --no-pager "$@" 2>/dev/null || true)
else
  LOG=$(journalctl -u ollama --no-pager 2>/dev/null || true)
fi

if [ -z "$LOG" ]; then
  echo "No se pudo leer el log de Ollama (¿el servicio está activo?)."
  exit 1
fi

PIN=$(echo "$LOG"  | grep -oP 'prompt eval time =.*?/\s+\K[0-9]+' | awk '{s+=$1} END{print s+0}')
POUT=$(echo "$LOG" | grep -oP '[^p]eval time =.*?/\s+\K[0-9]+' | awk '{s+=$1} END{print s+0}')
NP=$(echo "$LOG"   | grep -c 'print_timing' || true)

echo "──────────────────────────────────────────────────────────"
echo " Ollama · uso de tokens"
echo "──────────────────────────────────────────────────────────"
printf " Peticiones (respuestas)  : %s\n" "$NP"
printf " Tokens de entrada        : %s\n" "$PIN"
printf " Tokens de salida         : %s\n" "$POUT"
printf " TOTAL                    : %s\n" "$((PIN+POUT))"
echo "──────────────────────────────────────────────────────────"
