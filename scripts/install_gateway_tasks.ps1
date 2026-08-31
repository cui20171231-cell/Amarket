param(
    [string]$ProjectRoot = 'D:\Amarket'
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell is required.'
}

$gatewayTaskName = 'MarketOS-Gateway'
$tunnelTaskName = 'MarketOS-Tunnel'

foreach ($taskName in $gatewayTaskName, $tunnelTaskName) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

foreach ($port in 2091, 8080) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if (($port -eq 2091 -and $process.ProcessName -in @('python', 'pythonw')) -or
            ($port -eq 8080 -and $process.ProcessName -eq 'tunnel-client')) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
}

$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$recoveryTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$systemPrincipal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

$gatewayAction = New-ScheduledTaskAction `
    -Execute "$ProjectRoot\.ai_gateway_env\Scripts\python.exe" `
    -Argument '-m app.ai_gateway' `
    -WorkingDirectory $ProjectRoot
$tunnelAction = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ProjectRoot\START_AI_TUNNEL_UNATTENDED.ps1`"" `
    -WorkingDirectory $ProjectRoot

Register-ScheduledTask `
    -TaskName $gatewayTaskName `
    -Action $gatewayAction `
    -Trigger @($bootTrigger, $recoveryTrigger) `
    -Settings $settings `
    -Principal $systemPrincipal `
    -Description 'Local AI gateway: start at boot and recover every minute.' `
    -Force | Out-Null

Register-ScheduledTask `
    -TaskName $tunnelTaskName `
    -Action $tunnelAction `
    -Trigger @($bootTrigger, $recoveryTrigger) `
    -Settings $settings `
    -Principal $systemPrincipal `
    -Description 'External tunnel: wait for the gateway, start at boot, and recover every minute.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $gatewayTaskName
$gatewayDeadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    $gatewayListener = Get-NetTCPConnection -LocalPort 2091 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
} while (($null -eq $gatewayListener) -and ((Get-Date) -lt $gatewayDeadline))
if ($null -eq $gatewayListener) {
    throw 'Gateway task installed, but port 2091 did not start within 30 seconds.'
}

Start-ScheduledTask -TaskName $tunnelTaskName
$tunnelDeadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    $tunnelListener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
} while (($null -eq $tunnelListener) -and ((Get-Date) -lt $tunnelDeadline))
if ($null -eq $tunnelListener) {
    throw 'Tunnel task installed, but port 8080 did not start within 30 seconds.'
}

Get-ScheduledTask -TaskName $gatewayTaskName, $tunnelTaskName |
    Select-Object TaskName, State
