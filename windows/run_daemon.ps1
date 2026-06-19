# run_daemon.ps1
# Windows Daemon 启动器 — 启动 xuangu 连续调度器
# 由任务计划程序在用户登录时自动启动
#
# 用法：
#   .\windows\run_daemon.ps1                    # 正常启动（显示控制台窗口）
#   .\windows\run_daemon.ps1 -NoWindow           # 后台无窗口运行（pythonw）
#   .\windows\run_daemon.ps1 -DryRun             # 跑一轮后退出

param(
    [switch]$NoWindow,
    [switch]$DryRun
)

$RepoDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$LogDir  = "$RepoDir\logs"
$DaemonLog = "$LogDir\daemon-startup.log"

# 创建日志目录
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    try { Add-Content -Path $DaemonLog -Value $line -Encoding UTF8 } catch {}
}

# 查找 Python
$Python = ""
foreach ($exe in @("python", "python3", "py")) {
    try {
        $ver = & $exe --version 2>&1
        if ($ver -match "Python 3\.\d+") {
            $Python = (Get-Command $exe -ErrorAction Stop).Source
            break
        }
    } catch { continue }
}
if (-not $Python) {
    Log "❌ 未找到 Python 3！请安装 Python 3.10+ 并加入 PATH"
    Start-Sleep -Seconds 10
    exit 1
}

Log "Python: $Python"
Log "工作目录: $RepoDir"

# 切换工作目录
Set-Location $RepoDir

# 构建参数
$args = "-m src.daemon"
if ($DryRun) { $args += " --dry-run" }

if ($NoWindow) {
    # 后台无窗口运行
    $logFile = "$LogDir\daemon.log"
    Log "启动 daemon（后台无窗口）..."
    Log "日志: $logFile"
    Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $RepoDir `
        -WindowStyle Hidden -NoNewWindow
    Log "Daemon 已在后台启动（PID 请查阅任务管理器）"
} else {
    # 前台控制台窗口
    Log "启动 daemon（前台控制台）..."
    Log "按 Ctrl+C 停止"
    & $Python $args
    Log "Daemon 已停止"
}
