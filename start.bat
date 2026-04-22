@echo off
title Robot 2026
cd /d "%~dp0"

echo.
echo  ========================================
echo   Robot 2026 — Automatizacion de Reportes
echo  ========================================
echo.

:: ---- Setup (solo si falta el venv) ----
if not exist ".venv\Scripts\python.exe" (
    echo  [Setup] Creando ambiente virtual Python...
    python -m venv .venv
    echo  [Setup] Instalando dependencias Python...
    .venv\Scripts\pip.exe install -r backend\requirements.txt
)

:: ---- Iniciar servidor ----
echo  Iniciando servidor...
echo  El navegador se abrira en http://localhost:8000
echo.
.venv\Scripts\python.exe server.py

pause
