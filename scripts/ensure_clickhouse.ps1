function Start-AmarketClickHouse
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $runtimeDirectory = Join-Path $ProjectRoot '.runtime'
    $lockPath = Join-Path $runtimeDirectory 'linux-clickhouse-startup.lock'
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
            if ($attempt -eq 120) { throw 'Timed out waiting for the Linux ClickHouse startup lock.' }
            Start-Sleep -Seconds 5
        }
    }

    try {
        try {
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8123/ping' | Out-Null
            return
        } catch {
            # Continue into the Linux recovery path.
        }

        if (-not (Test-Path 'C:\Windows\System32\wsl.exe')) {
            throw 'Windows Subsystem for Linux is not installed.'
        }

        $root = [System.IO.Path]::GetPathRoot($ProjectRoot)
        $drive = $root.Substring(0, 1).ToLowerInvariant()
        $relativeProject = $ProjectRoot.Substring($root.Length).Replace('\', '/')
        $linuxProjectRoot = "/mnt/$drive/$relativeProject"
        $linuxCompose = "$linuxProjectRoot/compose.linux.yaml"

        & 'C:\Windows\System32\wsl.exe' -d Ubuntu --user root -- `
            docker compose -f $linuxCompose up -d
        if ($LASTEXITCODE -ne 0) { throw 'Linux ClickHouse failed to start.' }

        for ($attempt = 1; $attempt -le 60; $attempt++) {
            try {
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8123/ping' | Out-Null
                break
            } catch {
                if ($attempt -eq 60) { throw 'Linux ClickHouse did not become healthy within five minutes.' }
                Start-Sleep -Seconds 5
            }
        }
    } finally {
        if ($null -ne $lockStream) {
            $lockStream.Dispose()
        }
    }
}
