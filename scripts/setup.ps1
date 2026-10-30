$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root 'chAgent\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    python -m venv (Join-Path $root 'chAgent')
}

& $venvPython -m pip install -r (Join-Path $root 'backend\requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }

$envFile = Join-Path $root 'backend\.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root 'backend\.env.example') $envFile
    Write-Host 'Created backend/.env from the demo template. Add your model key and endpoint before using Agent features.'
}

Push-Location (Join-Path $root 'frontend')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
} finally { Pop-Location }

Write-Host 'Environment ready. Start with .\scripts\dev.ps1'
