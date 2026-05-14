#!/usr/bin/env bash
# ============================================
#  mendi.log - arranque rápido para Linux/Mac
# ============================================
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[setup] Creando entorno virtual..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[setup] Instalando dependencias..."
pip install -q -r requirements.txt

echo "[setup] Descargando fuentes y librerías locales (solo la 1ª vez)..."
python scripts/download_assets.py || true

echo ""
echo "============================================"
echo "  Abre http://127.0.0.1:8000  en tu navegador"
echo "============================================"
echo ""
# IMPORTANTE: --workers 1 (implícito sin la opción). El cache de la vista
# análisis vive en memoria del proceso (ver app/analisis.py:_CACHE) y no se
# comparte entre workers. Si subes a más workers tendrás cache stale en unos
# y fresh en otros tras cualquier escritura.
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --workers 1
