@echo off
rem Double-click to open the Fantasy Hoops window (no console stays open).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\main.py" %*
