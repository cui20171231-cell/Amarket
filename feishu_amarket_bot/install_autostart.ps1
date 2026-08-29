$ErrorActionPreference = 'Stop'

$basePath = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonwPath = Join-Path $basePath '.venv\Scripts\pythonw.exe'
$listenerPath = Join-Path $basePath 'listener.py'
$taskName = 'Amarket Feishu Bot Listener'

if (-not (Test-Path -LiteralPath (Join-Path $basePath '.env'))) {
    throw 'The .env file is missing. Save APP_ID and APP_SECRET first.'
}
if (-not (Test-Path -LiteralPath $pythonwPath)) {
    throw 'The local Python environment is missing.'
}

$action = New-ScheduledTaskAction `
    -Execute $pythonwPath `
    -Argument ('"' + $listenerPath + '"') `
    -WorkingDirectory $basePath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Amarket Bot Feishu WebSocket message listener' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "Created and started the current-user logon task: $taskName"
