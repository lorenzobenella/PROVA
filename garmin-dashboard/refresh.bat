@echo off
rem Passo 7: scarica i dati nuovi e ricostruisce la dashboard.
cd /d "%~dp0"
python pull.py %*
if errorlevel 1 exit /b 1
python build.py
if errorlevel 1 exit /b 1
echo Dashboard aggiornata: %cd%\dashboard.html
