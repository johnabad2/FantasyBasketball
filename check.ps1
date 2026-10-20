# Refreshes data\ (CSVs + today's history snapshot) and pops Windows notifications for new alerts.
# Used by the "FantasyHoops" scheduled task; also fine to run by hand.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = "$here\.venv\Scripts\python.exe"
$log = "$here\check.log"
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Out-File $log -Append -Encoding utf8
& $py "$here\fantasy.py" export *>&1 | Out-File $log -Append -Encoding utf8
& $py "$here\fantasy.py" alerts --notify *>&1 | Out-File $log -Append -Encoding utf8
