$ErrorActionPreference = 'Stop'
$engineExecutable = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))
# Stop only this project's worker/dashboard, never unrelated Python processes.
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.ExecutablePath -eq $engineExecutable -and $_.CommandLine -match '-m\s+x_engine\s+(run|dashboard)(\s|$)'
} | ForEach-Object { Stop-Process -Id $_.ProcessId }
Write-Host 'Stopped this workspace''s X Engine services.'
