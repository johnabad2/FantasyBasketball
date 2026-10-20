# Refreshes data\ (CSVs + today's history snapshot) and pops Windows notifications for new alerts.
# Used by the "FantasyHoops" scheduled task; also fine to run by hand.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)  # project folder
$py = "$root\.venv\Scripts\python.exe"
$log = "$root\data\check.log"
New-Item -ItemType Directory -Force "$root\data" | Out-Null
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Out-File $log -Append -Encoding utf8
& $py "$root\app\fantasy.py" export *>&1 | Out-File $log -Append -Encoding utf8
& $py "$root\app\fantasy.py" alerts --notify *>&1 | Out-File $log -Append -Encoding utf8
