$taskName = 'HithinkDailyPipeline'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Write-Output 'Trading-day daily-K and Monday adjustment events are owned by HithinkSnapshotCollector at 16:00; no separate task is installed.'
