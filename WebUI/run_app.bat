@echo off
echo Starting ItemLibrary Server...
cd /d "%~dp0"

:: Start the default browser in 2 seconds to give the server a moment to initialize
start "" "http://127.0.0.1:5000"

:: Run the Flask server
python DataUI.py

pause
