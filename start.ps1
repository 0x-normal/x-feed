param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$enginePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $enginePython)) {
    throw 'Run setup.ps1 first.'
}
New-Item -ItemType Directory -Path 'data' -Force | Out-Null
$engineWorker = Start-Process -FilePath $enginePython -ArgumentList @('-m', 'x_engine', 'run') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\worker.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data\worker-error.log')
try {
    Write-Host "X Feed running. Open http://127.0.0.1:$Port . Press Ctrl+C to stop."
    & $enginePython -m x_engine dashboard --port $Port
} finally {
    if (-not $engineWorker.HasExited) { Stop-Process -Id $engineWorker.Id }
}
