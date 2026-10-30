$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$record = Join-Path $root 'data\runtime\processes.json'
if (-not (Test-Path $record)) { Write-Host 'No recorded services.'; return }
$ids = Get-Content $record -Raw | ConvertFrom-Json
$python = Join-Path $root 'chAgent\Scripts\python.exe'
$workerRecord = Join-Path $root 'data\runtime\staffing-worker.json'
if (Test-Path $workerRecord) {
    $workerInfo = Get-Content $workerRecord -Raw | ConvertFrom-Json
    $workerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($workerInfo.pid)" -ErrorAction SilentlyContinue
    if ($workerProcess -and $workerProcess.CommandLine -like '*-m app.workers.staffing*' -and $workerInfo.executable -eq $python -and ($workerInfo.pid -eq $ids.worker -or $workerInfo.parent_pid -eq $ids.worker)) {
        $mcpRecord = Join-Path $root "data\runtime\staffing-mcp-$($workerInfo.pid).json"
        if (Test-Path $mcpRecord) {
            $mcpInfo = Get-Content $mcpRecord -Raw | ConvertFrom-Json
            $mcpProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($mcpInfo.pid)" -ErrorAction SilentlyContinue
            $mcpLauncher = Get-CimInstance Win32_Process -Filter "ProcessId=$($mcpInfo.parent_pid)" -ErrorAction SilentlyContinue
            $mcpOwned = $mcpInfo.parent_pid -eq $workerInfo.pid -or ($mcpLauncher -and $mcpLauncher.ParentProcessId -eq $workerInfo.pid -and $mcpLauncher.ExecutablePath -eq $python -and $mcpLauncher.CommandLine -like '*-m app.mcp.staffing_server*')
            if ($mcpProcess -and $mcpProcess.CommandLine -like '*-m app.mcp.staffing_server*' -and $mcpOwned) {
                Stop-Process -Id $mcpInfo.pid -Force -ErrorAction SilentlyContinue
                if ($mcpLauncher -and $mcpLauncher.ParentProcessId -eq $workerInfo.pid -and $mcpLauncher.ExecutablePath -eq $python) { Stop-Process -Id $mcpLauncher.ProcessId -Force -ErrorAction SilentlyContinue }
            }
            Remove-Item -LiteralPath $mcpRecord
        }
        Stop-Process -Id $workerInfo.pid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $workerRecord
}
foreach ($processId in @($ids.worker, $ids.backend, $ids.frontend)) {
    if (-not $processId) { continue }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    if ($process -and ((($process.CommandLine -like '*uvicorn app.main:app*' -or $process.CommandLine -like '*-m app.workers.staffing*') -and $process.ExecutablePath -eq $python) -or $process.CommandLine.Contains((Join-Path $root 'frontend\node_modules\vite\bin\vite.js')))) {
        if ($process.ExecutablePath -eq $python) {
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$processId" | Where-Object { $_.CommandLine -match 'uvicorn app\.main:app|app\.workers\.staffing' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item -LiteralPath $record
Write-Host 'Recorded services stopped.'
