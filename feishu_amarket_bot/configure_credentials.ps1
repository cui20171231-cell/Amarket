$ErrorActionPreference = 'Stop'

$basePath = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $basePath '.env'

Write-Host 'Enter Feishu credentials locally. APP_SECRET will not be displayed.'
$appId = (Read-Host 'APP_ID').Trim()
if (-not $appId.StartsWith('cli_')) {
    throw 'APP_ID is invalid. It should normally begin with cli_.'
}
if ($appId.Contains("`r") -or $appId.Contains("`n")) {
    throw 'APP_ID must not contain a newline.'
}

$secureSecret = Read-Host 'APP_SECRET' -AsSecureString
$secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
try {
    $appSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
    if ([string]::IsNullOrWhiteSpace($appSecret)) {
        throw 'APP_SECRET must not be empty.'
    }
    if ($appSecret.Contains("`r") -or $appSecret.Contains("`n")) {
        throw 'APP_SECRET must not contain a newline.'
    }

    $lines = [string[]]@(
        "FEISHU_APP_ID=$appId",
        "FEISHU_APP_SECRET=$appSecret"
    )
    [IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))

    $currentAccount = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $envPath '/inheritance:r' '/grant:r' "${currentAccount}:(F)" 'SYSTEM:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not restrict access to the .env file.'
    }
}
finally {
    if ($secretPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
    }
    $appSecret = $null
    $secureSecret = $null
}

Write-Host 'Credentials saved locally in .env. Their values were not printed.'
