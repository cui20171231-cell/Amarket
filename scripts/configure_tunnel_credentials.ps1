$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell window.'
}

$tunnelId = (Read-Host 'Control-plane tunnel ID').Trim()
$apiKey = Read-Host 'Control-plane API key' -AsSecureString
$apiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($apiKey)
try {
    $plainApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($apiKeyPointer)
    if ([string]::IsNullOrWhiteSpace($tunnelId) -or [string]::IsNullOrWhiteSpace($plainApiKey)) {
        throw 'Tunnel ID and API key are both required.'
    }
    [Environment]::SetEnvironmentVariable('CONTROL_PLANE_TUNNEL_ID', $tunnelId, 'Machine')
    [Environment]::SetEnvironmentVariable('CONTROL_PLANE_API_KEY', $plainApiKey, 'Machine')
} finally {
    if ($apiKeyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($apiKeyPointer)
    }
    $plainApiKey = $null
}

Write-Output 'Machine-level tunnel credentials were saved. Their values were not displayed.'
