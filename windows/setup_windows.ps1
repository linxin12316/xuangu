# setup_windows.ps1
# Windows 本地兜底 — 旧版（推荐改用 setup_daemon.ps1 获取实时推送）
#
# 新版 daemon 模式（推荐）：
#   powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1
#   - 连续运行，盘中每 2 分钟推送实时快讯
#   - 自动盘前选股 + 晚间复盘
#   - 登录/开机自动启动
#
# 旧版兜底模式（这份脚本）：
#   仅工作日 08:35/08:50 触发一次检查，无实时推送
#
# 用法：
#   # 1. 先填好 .env
#   Copy-Item .env.example .env
#   # 编辑 .env 填入 SCKEY、TUSHARE_TOKEN
#
#   # 2. 以管理员身份运行此脚本
#   powershell -ExecutionPolicy Bypass -File windows\setup_windows.ps1
#
#   # 3. 随时卸载
#   powershell -ExecutionPolicy Bypass -File windows\setup_windows.ps1 -Uninstall

param(
    [switch]$Uninstall,
    [switch]$DryRun
)

# ========== 推荐使用新版 daemon ==========
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  推荐使用新版 Daemon 模式（实时盘中推送）║" -ForegroundColor Cyan
Write-Host "║  windows\setup_daemon.ps1              ║" -ForegroundColor Cyan
Write-Host "║                                          ║" -ForegroundColor Cyan
Write-Host "║  ✓ 交易时段每 2 分钟推送实时快讯        ║" -ForegroundColor Cyan
Write-Host "║  ✓ 自动盘前选股 + 晚间复盘               ║" -ForegroundColor Cyan
Write-Host "║  ✓ 登录/开机自动启动                     ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
$useOld = Read-Host "是否继续使用旧版兜底（仅 08:35/08:50 检查）？[y/N]"
if ($useOld -ne "y" -and $useOld -ne "Y") {
    Write-Host "正在转到新版 daemon 安装脚本..." -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File "$PSScriptRoot\setup_daemon.ps1" @($MyInvocation.BoundParameters.GetEnumerator() | ForEach-Object { "-$($_.Key)", "$($_.Value)" })
    exit $LASTEXITCODE
}

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$TaskName = "XuanguStockPick"
$ScriptPath = "$RepoDir\scripts\run_pick_fallback.ps1"

# ---- 颜色输出 ----
function Write-Info  { Write-Host "ℹ️  $($args -join ' ')" -ForegroundColor Cyan }
function Write-Ok   { Write-Host "✅ $($args -join ' ')" -ForegroundColor Green }
function Write-Warn { Write-Host "⚠️  $($args -join ' ')" -ForegroundColor Yellow }
function Write-Err  { Write-Host "❌ $($args -join ' ')" -ForegroundColor Red }

# ---- 检查管理员权限 ----
function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ========== 卸载 ==========
if ($Uninstall) {
    Write-Info "开始卸载任务计划程序…"
    try {
        schtasks /End /TN $TaskName 2>$null
        schtasks /Delete /TN $TaskName /F 2>$null
        Write-Ok "任务计划程序 '$TaskName' 已删除"
    } catch {
        Write-Err "删除失败：$($_.Exception.Message)"
        exit 1
    }
    Write-Ok "Windows 本地兜底已卸载"
    exit 0
}

Write-Info "===== Xuangu Windows 本地兜底安装 ====="
Write-Info "项目目录: $RepoDir"

# ========== 检查管理员权限 ==========
if (-not (Test-Admin)) {
    Write-Warn "需要管理员权限才能创建任务计划程序。"
    Write-Warn "请以管理员身份重新运行："
    Write-Warn "  powershell -ExecutionPolicy Bypass -File $PSCommandPath"
    # 仍然允许继续，但提醒
    $continue = Read-Host "是否继续（否的话自己重启管理员终端再跑）？[y/N]"
    if ($continue -ne "y" -and $continue -ne "Y") {
        exit 1
    }
}

# ========== 检查 Python ==========
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
    Write-Info "下载: https://www.python.org/downloads/"
    exit 1
}
Write-Ok "Python 3: $python"

# ========== 检查 .env ==========
$envFile = "$RepoDir\.env"
if (-not (Test-Path $envFile)) {
    Write-Warn ".env 文件不存在！"
    Copy-Item "$RepoDir\.env.example" $envFile -ErrorAction SilentlyContinue
    Write-Info "已从 .env.example 复制，请编辑 $envFile 填写密钥后再运行此脚本"
    Write-Info "需要填写: SCKEY（Server酱）、TUSHARE_TOKEN（Tushare API Token）"
    exit 1
}
$hasSckey = Select-String -Path $envFile -Pattern "^SCKEY=" | Select-Object -First 1
$hasTushare = Select-String -Path $envFile -Pattern "^TUSHARE_TOKEN=" | Select-Object -First 1
if (-not $hasSckey) {
    Write-Err ".env 中未找到 SCKEY，请填写 Server 酱 SendKey"
    exit 1
}
if ($hasSckey -match "SCTxxxxx") {
    Write-Warn "SCKEY 仍是示例值（SCTxxxxx），请替换为真实 SendKey"
    $continue = Read-Host "是否继续？[y/N]"
    if ($continue -ne "y" -and $continue -ne "Y") { exit 1 }
}
Write-Ok ".env 配置检查通过"

# ========== 检查依赖 ==========
Write-Info "检查 Python 依赖…"
try {
    $pipCheck = & $python -c "import akshare, tushare, pandas, numpy, requests" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "部分 Python 依赖缺失，执行 pip install…"
        & $python -m pip install -r "$RepoDir\requirements.txt" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Err "pip install 失败，请手动运行：pip install -r requirements.txt"
            exit 1
        }
        Write-Ok "依赖安装完成"
    } else {
        Write-Ok "Python 依赖已安装"
    }
} catch {
    Write-Warn "依赖检查失败（$($_.Exception.Message)），跳过"
}

# ========== dry-run 测试 ==========
if ($DryRun) {
    Write-Info "dry-run 模式：仅检查配置，不注册任务"
    exit 0
}

# ========== 注册任务计划程序 ==========
Write-Info "注册任务计划程序 '$TaskName'…"

# schtasks /Create 参数说明：
#   /SC WEEKLY /D MON,TUE,WED,THU,FRI — 周一到周五
#   /TN 任务名称
#   /TR 要执行的命令
#   /ST 首次触发时间
#   /RI 1 — 每 1 分钟重复（不需要，我们使用多个 /ST 不现实，schtasks 只支持一个 /ST）
#
# schtasks 限制：Create 只能设置一个 start time，无法设置多 trigger。
# 因此改为用 XML 方式创建（功能更强大），或者分别注册两个 task。
#
# 方案 A: 每个时间点一个独立 trigger
#   注册 08:35 和 08:50 两个触发器
#   这是最简单兼容的方式

$PsExecPath = "powershell.exe"
$PsArgs = "-ExecutionPolicy Bypass -File `"$ScriptPath`""
$WorkingDir = $RepoDir

# 删除旧任务
schtasks /End /TN $TaskName 2>$null
schtasks /Delete /TN $TaskName /F 2>$null

# 创建两个触发器：08:35 和 08:50
$triggers = @(
    @{Hour=8; Minute=35},
    @{Hour=8; Minute=50}
)

$triggerCount = 0
foreach ($t in $triggers) {
    try {
        $taskParams = @(
            "/Create",
            "/SC", "WEEKLY",
            "/D", "MON,TUE,WED,THU,FRI",
            "/TN", "$TaskName",
            "/TR", "`"$PsExecPath`" -ExecutionPolicy Bypass -File `"$ScriptPath`"",
            "/ST", "$($t.Hour):$($t.Minute)",
            "/DU", "00:30",  # 最长运行 30 分钟
            "/F",
            "/IT"  # 仅在用户登录时运行（可交互）
        )
        # 首次创建任务，后续使用 /RU 添加同一任务的触发器
        if ($triggerCount -eq 0) {
            schtasks @taskParams 2>&1 | Out-Null
        } else {
            # 已有任务，添加新触发器
            schtasks /Change /TN $TaskName /ST "$($t.Hour):$($t.Minute)" 2>&1 | Out-Null
            # schtasks /Change 不支持添加多个 /ST。改用 XML 方式
        }
        $triggerCount++
    } catch {
        Write-Warn "创建触发器 $($t.Hour):$($t.Minute) 失败：$($_.Exception.Message)"
    }
}

# 由于 schtasks CLI 不支持多个 ST，改用 XML 方式创建任务
Write-Info "用 XML 方式创建任务（支持多触发时间）…"

# 构建 XML
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Xuangu 选股早盘兜底 — 工作日 08:35 / 08:50 检查 GitHub 是否已推送</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-06-19T08:35:00+08:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-06-19T08:50:00+08:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
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
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-ExecutionPolicy Bypass -File "$ScriptPath"</Arguments>
      <WorkingDirectory>$WorkingDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# 保存 XML 到临时文件
$xmlPath = "$env:TEMP\xuangu-pick-task.xml"
$xml | Out-File -FilePath $xmlPath -Encoding UTF8 -Force

# 通过 schtasks /Create /XML 导入
try {
    $result = schtasks /Create /TN $TaskName /XML $xmlPath /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "任务计划程序创建成功"
    } else {
        Write-Err "schtasks 创建失败：$result"
        Write-Info "尝试备选方案 — 使用 Register-ScheduledTask…"
        throw "schtasks failed"
    }
} catch {
    # 备选：用 PowerShell ScheduledTask 模块
    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" `
            -Argument "-ExecutionPolicy Bypass -File `"$ScriptPath`"" `
            -WorkingDirectory $WorkingDir
        $trigger1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek @(
            [DayOfWeek]::Monday, [DayOfWeek]::Tuesday, [DayOfWeek]::Wednesday,
            [DayOfWeek]::Thursday, [DayOfWeek]::Friday
        ) -At "08:35"
        $trigger2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek @(
            [DayOfWeek]::Monday, [DayOfWeek]::Tuesday, [DayOfWeek]::Wednesday,
            [DayOfWeek]::Thursday, [DayOfWeek]::Friday
        ) -At "08:50"
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount `
            -RunLevel Limited

        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger1, $trigger2) `
            -Settings $settings -Principal $principal -Force | Out-Null

        Write-Ok "通过 PowerShell ScheduledTask 模块创建成功"
    } catch {
        Write-Err "所有方式创建任务均失败：$($_.Exception.Message)"
        Write-Info "手动创建步骤："
        Write-Info "  1. 打开「任务计划程序」"
        Write-Info "  2. 右侧「创建任务…」"
        Write-Info "  3. 名称：$TaskName"
        Write-Info "  4. 触发器：新建 → 每周 → 周一到五 08:35，再添加一个 08:50"
        Write-Info "  5. 操作：新建 → powershell.exe"
        Write-Info "    参数：-ExecutionPolicy Bypass -File `"$ScriptPath`""
        Write-Info "    起始于：$WorkingDir"
        exit 1
    }
}

# 清理临时 XML
try { Remove-Item $xmlPath -Force } catch {}

Write-Ok "任务计划程序 '$TaskName' 已注册"

# 立即测试运行（可选）
$runNow = Read-Host "是否立即测试运行（dry-run 模式，不推送）？[y/N]"
if ($runNow -eq "y" -or $runNow -eq "Y") {
    Write-Info "启动测试运行（dry-run）…"
    & $PsExecPath -ExecutionPolicy Bypass -File $ScriptPath -DryRun
}

Write-Ok "===== 安装完成 ====="
Write-Info ""
Write-Info "接下来："
Write-Info "  1. 确认 .env 已填好 SCKEY 和 TUSHARE_TOKEN"
Write-Info "  2. 任务计划程序会在工作时间 08:35 / 08:50 自动触发"
Write-Info "  3. 查看日志：$LogDir\local-fallback.log"
Write-Info ""
Write-Info "卸载："
Write-Info "  powershell -ExecutionPolicy Bypass -File windows\setup_windows.ps1 -Uninstall"
