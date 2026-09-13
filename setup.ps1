$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -e '.[test]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
New-Item -ItemType Directory -Path 'data' -Force | Out-Null
Push-Location (Join-Path $PSScriptRoot 'tools\rettiwt')
try {
    npm.cmd ci --ignore-scripts --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw 'Rettiwt installation failed. Install Node/npm first.' }
} finally { Pop-Location }
& '.\.venv\Scripts\python.exe' -m x_engine set-provider rettiwt
Write-Host 'Ready. Import your account files, add a target, then run .\start.ps1.'
