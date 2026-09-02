param(
    [string]$ProjectRoot = 'D:\Amarket',
    [string]$ClickHouseUser = 'hithink_snapshot',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$secretDirectory = Join-Path $ProjectRoot 'secrets'
$secretPath = Join-Path $secretDirectory 'collector.env'

if ((Test-Path -LiteralPath $secretPath) -and -not $Force) {
    throw "Credential file already exists: $secretPath. Use -Force only when you intend to replace it."
}

$apiKey = Read-Host 'Hithink Finance API key' -AsSecureString
$databasePassword = Read-Host 'ClickHouse password' -AsSecureString
$apiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($apiKey)
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($databasePassword)

try {
    $plainApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($apiKeyPointer)
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    if ([string]::IsNullOrWhiteSpace($plainApiKey) -or [string]::IsNullOrWhiteSpace($plainPassword)) {
        throw 'Both credentials are required.'
    }
    if ($plainApiKey.Contains("`r") -or $plainApiKey.Contains("`n") -or
        $plainPassword.Contains("`r") -or $plainPassword.Contains("`n")) {
        throw 'Credentials cannot contain line breaks.'
    }

    New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null
    $content = @(
        "HITHINK_FINANCE_API_KEY=$plainApiKey"
        'CLICKHOUSE_HOST=127.0.0.1'
        'CLICKHOUSE_PORT=8123'
        'CLICKHOUSE_DATABASE=market'
        "CLICKHOUSE_USERNAME=$ClickHouseUser"
        "CLICKHOUSE_PASSWORD=$plainPassword"
    ) -join "`r`n"
    [IO.File]::WriteAllText($secretPath, $content + "`r`n", (New-Object Text.UTF8Encoding($false)))
} finally {
    if ($apiKeyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($apiKeyPointer)
    }
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    $plainApiKey = $null
    $plainPassword = $null
}

$currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $secretDirectory /inheritance:r /grant:r `
    '*S-1-5-18:(OI)(CI)F' `
    '*S-1-5-32-544:(OI)(CI)F' `
    "*$currentUserSid`:(OI)(CI)F" | Out-Null
& icacls.exe $secretPath /inheritance:r /grant:r `
    '*S-1-5-18:F' `
    '*S-1-5-32-544:F' `
    "*$currentUserSid`:F" | Out-Null

Write-Output "Collector credential file created: $secretPath"
Write-Output 'Credential values were not displayed and this path is excluded from Git.'
