$ErrorActionPreference = 'Stop'
$taskName = 'Amarket Feishu Bot Listener'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed the autostart task: $taskName"
}
else {
    Write-Host 'No autostart task was found.'
}
