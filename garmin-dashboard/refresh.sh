#!/usr/bin/env sh
# Passo 7: scarica i dati nuovi e ricostruisce la dashboard.
#   ./refresh.sh          aggiornamento normale
#   ./refresh.sh --all    la prima volta, per scaricare tutto lo storico
set -e
cd "$(dirname "$0")"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
"$PY" pull.py "$@"
"$PY" build.py
echo "Dashboard aggiornata: $(pwd)/dashboard.html"
