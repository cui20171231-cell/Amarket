$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\Amarket'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$env:DOCKER_CONFIG = Join-Path $projectRoot '.docker-client'
$env:DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'
New-Item -ItemType Directory -Force -Path $env:DOCKER_CONFIG | Out-Null
$markerDirectory = Join-Path $projectRoot 'data'
$marker = Join-Path $markerDirectory 'bootstrap.done'

if (Test-Path $marker) {
    exit 0
}

if (-not (Test-Path $docker)) {
    throw 'Docker Desktop is not installed.'
}

Start-Process -FilePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -WindowStyle Hidden
for ($attempt = 1; $attempt -le 60; $attempt++) {
    & $docker version --format '{{.Server.Version}}' 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    if ($attempt -eq 60) { throw 'Docker engine did not become ready within five minutes.' }
    Start-Sleep -Seconds 5
}

& $docker compose -f (Join-Path $projectRoot 'compose.yaml') up -d
if ($LASTEXITCODE -ne 0) { throw 'ClickHouse container failed to start.' }

for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8123/ping' | Out-Null
        break
    } catch {
        if ($attempt -eq 60) { throw 'ClickHouse did not become healthy within five minutes.' }
        Start-Sleep -Seconds 5
    }
}

& py -3.11 -m app.hithink.cli init-db
if ($LASTEXITCODE -ne 0) { throw 'New market database and tables were not created.' }

& powershell -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'scripts\install_collector_task.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Daily collector task was not registered.' }

New-Item -ItemType Directory -Force -Path $markerDirectory | Out-Null
New-Item -ItemType File -Force -Path $marker | Out-Null
Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$projectRoot\scripts\run_collector.ps1`"" -WorkingDirectory $projectRoot -WindowStyle Hidden
