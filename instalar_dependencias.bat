@echo off
setlocal
cd /d "%~dp0"
echo Instalando dependencias...
pip install -r requirements.txt
echo.
echo Listo.
pause
