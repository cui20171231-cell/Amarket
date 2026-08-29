function Start-AmarketClickHouse
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    $desktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    $runtimeDirectory = Join-Path $ProjectRoot '.runtime'
    $lockPath = Join-Path $runtimeDirectory 'docker-startup.lock'
    $lockStream = $null

    New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null

    for ($attempt = 1; $attempt -le 120; $attempt++) {
        try {
            $lockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            break
        } catch [System.IO.IOException] {
            if ($attempt -eq 120) { throw 'Timed out waiting for the Docker startup lock.' }
            Start-Sleep -Seconds 5
        }
    }

    try {
        $env:DOCKER_CONFIG = Join-Path $ProjectRoot '.docker-client'
        $env:DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        New-Item -ItemType Directory -Force -Path $env:DOCKER_CONFIG | Out-Null

        if (-not (Test-Path $docker)) {
            throw 'Docker Desktop is not installed.'
        }

        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'SilentlyContinue'
        & $docker version --format '{{.Server.Version}}' 2>$null | Out-Null
        $dockerReady = $LASTEXITCODE -eq 0
        $ErrorActionPreference = $previousErrorAction

        if (-not $dockerReady) {
            $desktopProcess = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
            if (-not $desktopProcess) {
                Start-Process -FilePath $desktop -WindowStyle Hidden
            }

            for ($attempt = 1; $attempt -le 120; $attempt++) {
                $previousErrorAction = $ErrorActionPreference
                $ErrorActionPreference = 'SilentlyContinue'
                & $docker version --format '{{.Server.Version}}' 2>$null | Out-Null
                $dockerReady = $LASTEXITCODE -eq 0
                $ErrorActionPreference = $previousErrorAction
                if ($dockerReady) { break }
                if ($attempt -eq 120) { throw 'Docker engine did not become ready within ten minutes.' }
                Start-Sleep -Seconds 5
            }
        }

        & $docker compose -f (Join-Path $ProjectRoot 'compose.yaml') up -d
        if ($LASTEXITCODE -ne 0) { throw 'ClickHouse failed to start.' }

        for ($attempt = 1; $attempt -le 60; $attempt++) {
            try {
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8123/ping' | Out-Null
                break
            } catch {
                if ($attempt -eq 60) { throw 'ClickHouse did not become healthy within five minutes.' }
                Start-Sleep -Seconds 5
            }
        }
    } finally {
        if ($null -ne $lockStream) {
            $lockStream.Dispose()
        }
    }
}
