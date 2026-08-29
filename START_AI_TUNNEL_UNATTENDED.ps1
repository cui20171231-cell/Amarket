$env:CONTROL_PLANE_TUNNEL_ID = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_TUNNEL_ID', 'Machine')
$env:CONTROL_PLANE_API_KEY = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY', 'Machine')

if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_TUNNEL_ID) -or [string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
    exit 2
}

$client = Join-Path $PSScriptRoot 'AI_Tunnel\tunnel-client.exe'
while ($true) {
    while (-not (Test-NetConnection 127.0.0.1 -Port 2091 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
        Start-Sleep -Seconds 2
    }
    & $client run --control-plane.tunnel-id $env:CONTROL_PLANE_TUNNEL_ID --mcp.server-url 'http://127.0.0.1:2091/mcp'
    Start-Sleep -Seconds 5
}
