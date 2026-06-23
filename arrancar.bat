@echo off
setlocal
cd /d "%~dp0"
python bot2_visual.py
if errorlevel 1 pause
