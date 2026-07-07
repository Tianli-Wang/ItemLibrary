@echo off
chcp 65001 >nul
echo Starting Item Library server...
cd /d "%~dp0"

:: open the default browser shortly after launch
start "" "http://127.0.0.1:5000"

:: run the unified Flask server (index.html + BOM + API)
python app.py

pause
