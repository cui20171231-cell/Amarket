$taskName = 'HithinkSectorMappingSync'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Write-Output 'Sector mapping is owned by HithinkSnapshotCollector after the 08:50 trading-day confirmation; no separate task is installed.'
