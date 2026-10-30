$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'chAgent\Scripts\python.exe'
$node = (Get-Command node).Source
$vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
$runtime = Join-Path $root 'data\runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is occupied. Stop the existing service before starting."
    }
}
$priorRecord = Join-Path $runtime 'processes.json'
if (Test-Path $priorRecord) {
    $priorIds = Get-Content $priorRecord -Raw | ConvertFrom-Json
    if ($priorIds.worker -and (Get-Process -Id $priorIds.worker -ErrorAction SilentlyContinue)) { throw 'Recorded staffing worker is still running. Run scripts/stop.ps1 first.' }
}
$backend = Start-Process -FilePath $python -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8000' -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'backend.log') -RedirectStandardError (Join-Path $runtime 'backend.err.log') -PassThru
@{ backend = $backend.Id } | ConvertTo-Json | Set-Content $priorRecord
$healthy = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try { $response = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 1; if ($response.status -eq 'ok') { $healthy = $true; break } } catch {}
    Start-Sleep -Milliseconds 250
}
if (-not $healthy) { throw 'Backend did not become ready; inspect data/runtime/backend.err.log and run scripts/stop.ps1.' }
$worker = Start-Process -FilePath $python -ArgumentList '-m app.workers.staffing' -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'staffing-worker.log') -RedirectStandardError (Join-Path $runtime 'staffing-worker.err.log') -PassThru
$frontend = Start-Process -FilePath $node -ArgumentList @(('"' + $vite + '"'), '--host', '127.0.0.1', '--port', '5173', '--strictPort') -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'frontend.log') -RedirectStandardError (Join-Path $runtime 'frontend.err.log') -PassThru
@{ backend = $backend.Id; frontend = $frontend.Id; worker = $worker.Id } | ConvertTo-Json | Set-Content $priorRecord
Write-Host 'Frontend: http://127.0.0.1:5173'
Write-Host 'API docs: http://127.0.0.1:8000/docs'
Write-Host 'Staffing worker and local stdio MCP server: managed with scripts/stop.ps1'
Write-Host 'Local accounts: backend/.env; logs: data/runtime'
