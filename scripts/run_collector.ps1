$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\Amarket'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$desktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$env:DOCKER_CONFIG = Join-Path $projectRoot '.docker-client'
$env:DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'
New-Item -ItemType Directory -Force -Path $env:DOCKER_CONFIG | Out-Null

if (-not (Test-Path $docker)) {
    throw 'Docker Desktop is not installed.'
}

Start-Process -FilePath $desktop -WindowStyle Hidden
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

& py -3.11 -m app.hithink.cli serve
exit $LASTEXITCODE
