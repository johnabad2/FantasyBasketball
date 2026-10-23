@echo off
rem First-time setup: double-click once. Needs Python 3.11+ from python.org ("Add python.exe to PATH" ticked).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python isn't installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist .venv (echo Creating the Python environment... & python -m venv .venv || (pause & exit /b 1))
echo Installing what the app needs...
.venv\Scripts\python -m pip install --quiet --upgrade pip
.venv\Scripts\pip install --quiet -r config\requirements.txt || (pause & exit /b 1)
if not exist config\config.json (copy config\config.example.json config\config.json >nul & echo Made config\config.json)
echo.
echo Done. Next:
echo   1. Open config\config.json in Notepad and fill in espn_s2 and swid (see README, Setup step 4).
echo   2. Run this to find your team id, then put it in my_team_id:
echo        .venv\Scripts\python app\fantasy.py teams
echo   3. Double-click "Fantasy Hoops.bat".
pause
