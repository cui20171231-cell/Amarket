$ErrorActionPreference = "Continue"

$recordFolder = "D:\Amarket\N100系统优化记录"
$runLog = Join-Path $recordFolder "管理员执行结果.log"
$msiLog = Join-Path $recordFolder "Intel-CIP-管理员卸载.log"

Start-Transcript -LiteralPath $runLog -Append | Out-Null
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $isAdministrator = $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if (-not $isAdministrator) {
        throw "没有管理员权限，未执行系统级改动。"
    }

    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 开始执行第一阶段系统级优化。"

    $productCode = "{F2D45F25-BE06-4324-AC99-426FAF9B2E63}"
    $installedProduct = Get-ItemProperty `
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*", `
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -eq $productCode }

    if ($null -ne $installedProduct) {
        $arguments = @(
            "/x", $productCode, "/qn", "/norestart",
            "/L*v", $msiLog
        )
        $uninstaller = Start-Process `
            -FilePath "msiexec.exe" `
            -ArgumentList $arguments `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        "Intel Computing Improvement Program 卸载返回码：$($uninstaller.ExitCode)"
    }
    else {
        "Intel Computing Improvement Program 已不存在，跳过卸载。"
    }

    $serviceNames = @(
        "IntelCollectorService",
        "IntelTelemetryAgent",
        "SystemUsageReportSvc_QUEENCREEK",
        "ESRV_SVC_QUEENCREEK",
        "IntelGraphicsSoftwareService",
        "PresentMonSharedService",
        "igccservice"
    )
    foreach ($serviceName in $serviceNames) {
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($null -eq $service) {
            "服务不存在，跳过：$serviceName"
            continue
        }

        Set-Service -Name $serviceName -StartupType Disabled -ErrorAction Stop
        if ($service.Status -ne "Stopped") {
            Stop-Service -Name $serviceName -Force -ErrorAction Stop
        }
        "已停止并禁用服务：$serviceName"
    }

    $runPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $startupNames = @(
        "GoogleChromeAutoLaunch_6176AED31A339E2675648C05938DD4E8",
        "MicrosoftEdgeAutoLaunch_1362827719ED1BB97C1375DFEAD437B8"
    )
    foreach ($startupName in $startupNames) {
        Remove-ItemProperty `
            -LiteralPath $runPath `
            -Name $startupName `
            -ErrorAction SilentlyContinue
    }

    Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessName -in @(
                "CrossDeviceResume", "Widgets", "WidgetService",
                "IntelGraphicsSoftware", "esrv"
            )
        } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 系统级操作执行完成，未请求重启。"
}
catch {
    "执行失败：$($_.Exception.Message)"
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
