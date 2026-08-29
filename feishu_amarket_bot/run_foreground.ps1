$ErrorActionPreference = 'Stop'
$basePath = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $basePath '.venv\Scripts\python.exe'
$listenerPath = Join-Path $basePath 'listener.py'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'The local Python environment is missing.'
}

Set-Location -LiteralPath $basePath
& $pythonPath $listenerPath
exit $LASTEXITCODE
