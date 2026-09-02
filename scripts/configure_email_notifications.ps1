param(
    [string]$ProjectRoot = "D:\Amarket",
    [string]$DefaultSender = "amarket20260901@gmail.com"
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Security

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Amarket Email Notifications'
$form.Size = New-Object System.Drawing.Size(520, 330)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

function Add-Label([string]$text, [int]$top) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $text
    $label.Left = 24
    $label.Top = $top
    $label.Width = 450
    $form.Controls.Add($label)
}

function Add-TextBox([int]$top, [string]$value, [bool]$password) {
    $box = New-Object System.Windows.Forms.TextBox
    $box.Left = 24
    $box.Top = $top
    $box.Width = 450
    $box.Text = $value
    $box.UseSystemPasswordChar = $password
    $form.Controls.Add($box)
    return $box
}

Add-Label 'Sender Gmail address' 20
$senderBox = Add-TextBox 43 $DefaultSender $false
Add-Label 'Recipient email address' 82
$recipientBox = Add-TextBox 105 '' $false
Add-Label '16-character Google app password (hidden)' 144
$passwordBox = Add-TextBox 167 '' $true

$saveButton = New-Object System.Windows.Forms.Button
$saveButton.Text = 'Save and send test email'
$saveButton.Left = 250
$saveButton.Top = 220
$saveButton.Width = 224
$saveButton.Height = 34
$saveButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($saveButton)
$form.AcceptButton = $saveButton

$cancelButton = New-Object System.Windows.Forms.Button
$cancelButton.Text = 'Cancel'
$cancelButton.Left = 145
$cancelButton.Top = 220
$cancelButton.Width = 90
$cancelButton.Height = 34
$cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancelButton)
$form.CancelButton = $cancelButton

$result = $form.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    exit 2
}

$sender = $senderBox.Text.Trim()
$recipient = $recipientBox.Text.Trim()
$appPassword = $passwordBox.Text.Replace(' ', '')
if (-not $sender.EndsWith('@gmail.com') -or -not $recipient.Contains('@') -or $appPassword.Length -ne 16) {
    [System.Windows.Forms.MessageBox]::Show(
        'Check the sender, recipient, and 16-character app password.',
        'Configuration not saved',
        'OK',
        'Warning'
    ) | Out-Null
    exit 3
}

$configDirectory = Join-Path $env:ProgramData 'Amarket\secrets'
$stateDirectory = Join-Path $env:ProgramData 'Amarket\state'
$configPath = Join-Path $configDirectory 'email_notification.json'
New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

$clearBytes = [System.Text.Encoding]::UTF8.GetBytes($appPassword)
try {
    $protectedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
        $clearBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $protectedText = [Convert]::ToBase64String($protectedBytes)
} finally {
    [Array]::Clear($clearBytes, 0, $clearBytes.Length)
    $appPassword = $null
    $passwordBox.Text = ''
}

$payload = [ordered]@{
    version = 1
    sender = $sender
    recipients = @($recipient)
    smtp_host = 'smtp.gmail.com'
    smtp_port = 465
    app_password_dpapi = $protectedText
}
$json = $payload | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText(
    $configPath,
    $json,
    (New-Object System.Text.UTF8Encoding($false))
)

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($path in @($configDirectory, $stateDirectory)) {
    & icacls.exe $path /inheritance:r /grant:r `
        '*S-1-5-18:(OI)(CI)F' `
        '*S-1-5-32-544:(OI)(CI)F' `
        "*$currentUser`:(OI)(CI)F" | Out-Null
}
& icacls.exe $configPath /inheritance:r /grant:r `
    '*S-1-5-18:F' `
    '*S-1-5-32-544:F' `
    "*$currentUser`:F" | Out-Null

$python = 'C:\Windows\py.exe'
$testOutput = & $python -3.11 -m app.hithink.email_notifier send-test 2>&1
if ($LASTEXITCODE -ne 0) {
    [System.Windows.Forms.MessageBox]::Show(
        "Configuration was saved securely, but the test email failed.`r`n`r`n$($testOutput | Select-Object -Last 3)",
        'Test failed',
        'OK',
        'Error'
    ) | Out-Null
    exit 4
}

$installer = Join-Path $ProjectRoot 'scripts\install_email_notifier_task.ps1'
& $installer -ProjectRoot $ProjectRoot | Out-Null
[System.Windows.Forms.MessageBox]::Show(
    "Test email sent to: $recipient`r`n`r`nThe background task checks every five minutes and runs without sign-in after reboot.",
    'Amarket Email Notifications Enabled',
    'OK',
    'Information'
) | Out-Null
