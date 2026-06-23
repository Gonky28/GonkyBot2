@echo off
taskkill /F /IM GonkyBot.exe 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq bot2_visual*" 2>nul
del /F /Q "%~dp0bot2_visual.lock" 2>nul
del /F /Q "%~dp0dist\GonkyBot\bot2_visual.lock" 2>nul
echo Cierre forzado completado.
pause
