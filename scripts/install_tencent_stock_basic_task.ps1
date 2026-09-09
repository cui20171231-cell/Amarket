param(
    [string]$ProjectRoot = "D:\Amarket"
)

$ErrorActionPreference = 'Stop'
$taskName = 'TencentStockBasicCollector'

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$taskAction = New-ScheduledTaskAction `
    -Execute 'C:\Windows\py.exe' `
    -Argument '-3.11 -m app.hithink.cli tencent-stock-basic' `
    -WorkingDirectory $ProjectRoot
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '08:50'
$taskSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$systemPrincipal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $taskAction `
        -Trigger $dailyTrigger `
        -Settings $taskSettings `
        -Principal $systemPrincipal `
        -Description 'Independent daily 08:50 Tencent A-share total-shares and float-shares snapshot.' `
        -Force | Out-Null
}
catch [Microsoft.Management.Infrastructure.CimException] {
    if ($_.Exception.Message -notmatch 'Access is denied|拒绝访问') {
        throw
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $interactivePrincipal = New-ScheduledTaskPrincipal `
        -UserId $identity `
        -LogonType Interactive `
        -RunLevel Limited
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $taskAction `
        -Trigger $dailyTrigger `
        -Settings $taskSettings `
        -Principal $interactivePrincipal `
        -Description 'Independent daily 08:50 Tencent A-share total-shares and float-shares snapshot (runs after user sign-in).' `
        -Force | Out-Null
    Write-Warning 'Administrator rights were unavailable; installed for the current signed-in user.'
}

Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object NextRunTime, LastRunTime, LastTaskResult
