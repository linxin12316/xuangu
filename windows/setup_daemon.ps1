# setup_daemon.ps1
# Windows Daemon 安装/卸载脚本
# 以管理员身份运行
#
# 用法：
#   # 安装（以管理员身份）
#   powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1
#
#   # 卸载
#   powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1 -Uninstall

param(
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$TaskName = "XuanguDaemon"
$ScriptPath = "$RepoDir\windows\run_daemon.ps1"
$LogDir = "$RepoDir\logs"

function Write-Info  { Write-Host "ℹ️  $($args -join ' ')" -ForegroundColor Cyan }
function Write-Ok   { Write-Host "✅ $($args -join ' ')" -ForegroundColor Green }
function Write-Warn { Write-Host "⚠️  $($args -join ' ')" -ForegroundColor Yellow }
function Write-Err  { Write-Host "❌ $($args -join ' ')" -ForegroundColor Red }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ========== 卸载 ==========
if ($Uninstall) {
    Write-Info "开始卸载 daemon 任务计划程序…"
    try {
        schtasks /End /TN $TaskName 2>$null | Out-Null
        schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
        Write-Ok "任务计划程序 '$TaskName' 已删除"
    } catch {
        Write-Err "删除失败：$($_.Exception.Message)"
        exit 1
    }
    # 停止当前运行的 daemon
    try {
        $lockFile = "$RepoDir\.daemon.lock"
        if (Test-Path $lockFile) {
            $pid = Get-Content $lockFile -Raw
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Remove-Item $lockFile -Force
            Write-Ok "正在运行的 daemon（PID $pid）已停止"
        }
    } catch {}
    Write-Ok "Daemon 已卸载"
    exit 0
}

# ========== 安装 ==========
Write-Info "===== Xuangu Daemon Windows 安装 ====="
Write-Info "项目目录: $RepoDir"

# 管理员检查
if (-not (Test-Admin)) {
    Write-Warn "需要管理员权限才能创建任务计划程序。"
    Write-Warn "请以管理员身份运行："
    Write-Warn "  powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1"
    $continue = Read-Host "是否继续？[y/N]"
    if ($continue -ne "y" -and $continue -ne "Y") { exit 1 }
}

# 检查 Python
$python = ""
foreach ($exe in @("python", "python3", "py")) {
    try {
        $ver = & $exe --version 2>&1
        if ($ver -match "Python 3\.\d+") {
            $python = (Get-Command $exe -ErrorAction Stop).Source
            break
        }
    } catch { continue }
}
if (-not $python) {
    Write-Err "未找到 Python 3！请先安装 Python 3.10+ 并加入 PATH"
    exit 1
}
Write-Ok "Python 3: $python"

# 检查 .env
$envFile = "$RepoDir\.env"
if (-not (Test-Path $envFile)) {
    Write-Warn ".env 文件不存在！"
    Copy-Item "$RepoDir\.env.example" $envFile -ErrorAction SilentlyContinue
    Write-Info "已从 .env.example 复制，请编辑 $envFile 填写密钥后再运行"
    exit 1
}
$hasSckey = Select-String -Path $envFile -Pattern "^SCKEY=" | Select-Object -First 1
$hasTushare = Select-String -Path $envFile -Pattern "^TUSHARE_TOKEN=" | Select-Object -First 1
if (-not $hasSckey) {
    Write-Err ".env 中未找到 SCKEY"
    exit 1
}
Write-Ok ".env 配置检查通过"

# 检查依赖
Write-Info "检查 Python 依赖…"
try {
    & $python -c "import akshare, tushare, pandas, numpy, requests" 2>&1 | Out-Null
    Write-Ok "Python 依赖已安装"
} catch {
    Write-Warn "部分依赖缺失，执行 pip install…"
    & $python -m pip install -r "$RepoDir\requirements.txt" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pip install 失败，请手动运行：pip install -r requirements.txt"
        exit 1
    }
    Write-Ok "依赖安装完成"
}

# Dry-run 测试
if ($DryRun) {
    Write-Info "dry-run 模式：仅检查配置，不注册任务"
    exit 0
}

# 注册任务计划程序 — 用户登录时启动
Write-Info "注册任务计划程序 '$TaskName'（用户登录时自动启动）…"

# 删除旧任务
schtasks /End /TN $TaskName 2>$null | Out-Null
schtasks /Delete /TN $TaskName /F 2>$null | Out-Null

# 用 XML 创建任务（登录时启动，即使未登录也运行）
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Xuangu 选股 + 资讯雷达 Daemon — 连续运行，盘中实时推送快讯</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </LogonTrigger>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT60S</Delay>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <Enabled>true</Enabled>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-ExecutionPolicy Bypass -File "$ScriptPath" -NoWindow</Arguments>
      <WorkingDirectory>$RepoDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = "$env:TEMP\xuangu-daemon-task.xml"
try {
    $xml | Out-File -FilePath $xmlPath -Encoding UTF8 -Force
    $result = schtasks /Create /TN $TaskName /XML $xmlPath /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "任务计划程序创建成功"
    } else {
        throw "schtasks failed: $result"
    }
} catch {
    Write-Warn "schtasks 创建失败，尝试 PowerShell API…"
    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-ExecutionPolicy Bypass -File `"$ScriptPath`" -NoWindow" `
            -WorkingDirectory $RepoDir
        $triggers = @(
            (New-ScheduledTaskTrigger -AtLogOn),
            (New-ScheduledTaskTrigger -AtStartup)
        )
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RunOnlyIfNetworkAvailable
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount `
            -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Ok "通过 PowerShell API 创建成功"
    } catch {
        Write-Err "所有方式均失败：$($_.Exception.Message)"
        Write-Info "手动创建：任务计划程序 → 创建任务 → 名称 XuanguDaemon"
        Write-Info "  触发器：登录时 + 系统启动时"
        Write-Info "  操作：powershell.exe -ExecutionPolicy Bypass -File `"$ScriptPath`" -NoWindow"
        Write-Info "  起始于：$RepoDir"
        exit 1
    }
}
try { Remove-Item $xmlPath -Force } catch {}

Write-Ok "任务计划程序 '$TaskName' 已注册"
Write-Info ""

# 询问是否立即启动
$startNow = Read-Host "是否立即启动 daemon？[y/N]"
if ($startNow -eq "y" -or $startNow -eq "Y") {
    Write-Info "启动 daemon（dry-run 测试一轮）…"
    & powershell -ExecutionPolicy Bypass -File "$ScriptPath" -DryRun

    Write-Info ""
    $startReal = Read-Host "dry-run 通过，是否启动真实 daemon？[y/N]"
    if ($startReal -eq "y" -or $startReal -eq "Y") {
        & powershell -ExecutionPolicy Bypass -File "$ScriptPath" -NoWindow
        Write-Ok "Daemon 已在后台启动"
    }
}

Write-Ok "===== 安装完成 ====="
Write-Info ""
Write-Info "Daemon 会在以下时机自动启动："
Write-Info "  - 每次你登录 Windows（延迟 30 秒）"
Write-Info "  - 每次系统启动（延迟 60 秒）"
Write-Info ""
Write-Info "调度时间表（北京）："
Write-Info "  08:25-08:55  盘前选股（1次）"
Write-Info "  09:25-11:30  资讯雷达（每 2 分钟）☀️ 盘中实时"
Write-Info "  13:00-15:00  资讯雷达（每 2 分钟）☀️ 盘中实时"
Write-Info "  18:20-18:50  晚间复盘（1次）"
Write-Info ""
Write-Info "日志：$LogDir\daemon.log"
Write-Info "卸载：powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1 -Uninstall"
