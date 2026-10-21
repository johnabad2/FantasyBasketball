@echo off
rem Double-click to open the new-look Fantasy Hoops app (still being built; the old window is "Fantasy Hoops.bat").
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\webapp.py" %*
