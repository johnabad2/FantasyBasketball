# Runs the alert check and pops Windows notifications for anything new.
# Used by the scheduled task; also fine to run by hand.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$here\.venv\Scripts\python.exe" "$here\fantasy.py" alerts --notify *>> "$here\check.log"
