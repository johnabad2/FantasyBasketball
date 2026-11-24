# Pre-lock reminder: tonight's best streams as a Windows notification (only when a move is worth it).
# Used by the "FantasyHoopsTonight" scheduled task (see README); also fine to run by hand.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)  # project folder
$py = "$root\.venv\Scripts\python.exe"
$log = "$root\data\check.log"
New-Item -ItemType Directory -Force "$root\data" | Out-Null
$env:PYTHONIOENCODING = "utf-8"
"=== tonight $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Out-File $log -Append -Encoding utf8
& $py "$root\app\fantasy.py" tonight --notify *>&1 | Out-File $log -Append -Encoding utf8
