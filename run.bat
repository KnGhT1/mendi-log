@echo off
REM ============================================
REM  mendi.log - arranque rapido para Windows
REM ============================================

cd /d "%~dp0"

if not exist ".venv" (
    echo [setup] Creando entorno virtual...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [setup] Instalando dependencias...
pip install -q -r requirements.txt

echo [setup] Descargando fuentes y librerias locales (solo la 1a vez)...
python scripts\download_assets.py

echo.
echo ============================================
echo   Abre http://127.0.0.1:8000  en tu navegador
echo ============================================
echo.
REM IMPORTANTE: --workers 1 (implicito). El cache de la vista analisis vive en
REM memoria del proceso y no se comparte entre workers.
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --workers 1
