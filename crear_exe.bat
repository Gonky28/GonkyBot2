@echo off
setlocal
cd /d "%~dp0"
echo Creando GonkyBot.exe...
python -m PyInstaller --noconfirm --clean --distpath new GonkyBot.spec
if errorlevel 1 (
  echo.
  echo Error creando el exe.
  pause
  exit /b 1
)
echo.
echo EXE portable creado en:
echo %~dp0new\GonkyBot\GonkyBot.exe
echo.
pause
