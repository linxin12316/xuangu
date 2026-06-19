# run_pick_fallback.ps1
# Windows 本地兜底脚本 — 等价于 Mac 版 run_pick_fallback.sh
# 工作日 08:35 / 08:50 由任务计划程序触发：
#   如果 GitHub Actions 尚未成功推送，则本机执行选股+推微信
#   如果已推送（pushed flag 存在），跳过
#
# 用法（手动测试）：
#   .\scripts\run_pick_fallback.ps1
#   .\scripts\run_pick_fallback.ps1 -DryRun        # 离线 mock 跑

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$RepoDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$LockFile = "$env:TEMP\xuangu-pick.lock"
$LogDir  = "$RepoDir\logs"
$LogFile = "$LogDir\local-fallback.log"
$Python  = ""

# ---- 工具函数 ----
function Get-BeijingNow {
    try {
        return [System.TimeZoneInfo]::ConvertTime(
            [DateTime]::UtcNow,
            [System.TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
        )
    } catch {
        # fallback: 手动 +8
        return [DateTime]::UtcNow.AddHours(8)
    }
}

function Log($msg) {
    $ts = (Get-BeijingNow).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "$ts $msg"
    Write-Host $line
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch {
        Write-Host "   (写日志失败: $($_.Exception.Message))"
    }
}

function Find-Python {
    # 尝试常见路径：python 优先（Windows Store / 系统 PATH）
    foreach ($exe in @("python", "python3", "py")) {
        try {
            $ver = & $exe --version 2>&1
            if ($ver -match "Python 3\.\d+") {
                Log "   Python: $($ver.Trim()) → $exe"
                return $exe
            }
        } catch {
            continue
        }
    }
    # 尝试 Anaconda 常见安装路径
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "${env:ProgramFiles}\Python312\python.exe",
        "${env:ProgramFiles}\Python311\python.exe",
        "${env:ProgramFiles}\Python310\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "${env:ProgramData}\anaconda3\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            Log "   Python: $p"
            return $p
        }
    }
    return ""
}

# ---- 日志目录 ----
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
} catch {
    Write-Host "无法创建日志目录 $LogDir : $($_.Exception.Message)"
    exit 1
}

Log "===== Windows 本地兜底启动 ====="

# ---- 互斥锁（文件锁方式，等价于 mkdir） ----
if (Test-Path $LockFile) {
    try {
        $oldPid = [int](Get-Content $LockFile -Raw -Encoding UTF8).Trim()
        $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($proc) {
            Log "另一个 pick 进程正在运行（PID $oldPid），跳过本地兜底"
            exit 0
        }
    } catch {
        # 锁文件损坏，忽略
    }
}
try {
    $currentPid = [System.Diagnostics.Process]::GetCurrentProcess().Id
    [System.IO.File]::WriteAllText($LockFile, $currentPid.ToString(), [System.Text.Encoding]::UTF8)
} catch {
    Log "无法创建锁文件 $LockFile : $($_.Exception.Message)"
}

# ---- 清理锁（脚本退出时） ----
try {
    $null = Register-EngineEvent -SourceIdentifier "XuanguPick.Exiting" -SupportEvent -Action {
        $lf = "$env:TEMP\xuangu-pick.lock"
        if (Test-Path $lf) {
            try { Remove-Item $lf -Force } catch {}
        }
    } 2>$null
} catch {
    # 在某些受限环境下可能注册失败，不影响主逻辑
}

# ---- 进入项目目录 ----
try {
    Set-Location $RepoDir
} catch {
    Log "无法进入目录 $RepoDir : $($_.Exception.Message)"
    exit 1
}

# ---- git pull（可选，同 Mac 版） ----
if (Test-Path ".git") {
    try {
        $result = git pull --ff-only origin main 2>&1
        if ($LASTEXITCODE -ne 0) {
            Log "git pull 失败（$LASTEXITCODE），继续使用本地状态"
        }
    } catch {
        Log "git pull 异常，继续使用本地状态"
    }
} else {
    Log "当前目录不是 git 仓库，继续使用本地状态判断"
}

# ---- 北京时间 ----
$beijing = Get-BeijingNow
$today = $beijing.ToString("yyyy-MM-dd")
$env:TODAY = $today
$flagPath = "picks\$today-pushed.flag"

# ---- 检查 pushed flag ----
if (Test-Path $flagPath) {
    Log "$flagPath 已存在，说明今日已成功推送，本地兜底跳过"
    exit 0
}

# ---- 检查时间 ----
if ($beijing.Hour -ge 9) {
    Log "已到 09:00 或之后，本地兜底不再发送早盘策略（当前 $($beijing.ToString('HH:mm'))）"
    exit 0
}

# ---- 加载 .env ----
$envFile = "$RepoDir\.env"
if (Test-Path $envFile) {
    Log "加载 .env 配置…"
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $key = $matches[1]
            $val = $matches[2]
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
} else {
    Log "未找到 .env 文件，请复制 .env.example 为 .env 并填写密钥"
    exit 1
}

if (-not $env:SCKEY) {
    Log "未配置 SCKEY：请确保 .env 中的 SCKEY 已填写"
    exit 1
}

# ---- 查找 Python ----
$Python = Find-Python
if (-not $Python) {
    Log "未找到 Python 3，请确保已安装并加入 PATH"
    exit 1
}

# ---- 执行选股 ----
if ($DryRun) {
    Log "⚙️  dry-run 模式：离线 mock 跑一遍（不推送）"
    & $Python -m src.main pick --dry-run
    if ($LASTEXITCODE -eq 0) {
        Log "dry-run 成功 ✅"
    } else {
        Log "dry-run 失败，退出码 $LASTEXITCODE"
    }
    exit 0
}

Log "GitHub 尚未成功推送，启动本地早盘兜底"
& $Python -m src.main pick
$exitCode = $LASTEXITCODE

# ---- 检查结果 ----
if (Test-Path $flagPath) {
    Log "本地早盘兜底成功 ✅（pushed flag 已落盘）"

    # 上传 pushed flag 到 GitHub（防止后续 GitHub Actions 重复推送）
    if ($env:GITHUB_TOKEN) {
        try {
            $owner = "linxin12316"
            $repo = "xuangu"
            $branch = "main"
            $api = "https://api.github.com"

            # 上传 picks/<日期>.json 和 pushed flag
            foreach ($relPath in @("picks/$today.json", $flagPath)) {
                $fullPath = Join-Path $RepoDir $relPath
                if (-not (Test-Path $fullPath)) { continue }
                $bytes = [System.IO.File]::ReadAllBytes($fullPath)
                $b64 = [Convert]::ToBase64String($bytes)

                # 先获取当前文件的 SHA（如果存在）
                $getUrl = "$api/repos/$owner/$repo/contents/$($relPath -replace '\\','/')?ref=$branch"
                try {
                    $existing = Invoke-RestMethod -Uri $getUrl -Headers @{
                        "Authorization" = "Bearer $env:GITHUB_TOKEN"
                        "Accept" = "application/vnd.github+json"
                    } -Method Get -ContentType "application/json" -TimeoutSec 15
                    $sha = $existing.sha
                } catch {
                    $sha = $null
                }

                # PUT 上传
                $body = @{
                    message = "chore: local fallback pick $today [skip ci]"
                    content = $b64
                    branch  = $branch
                }
                if ($sha) { $body.sha = $sha }
                $jsonBody = ConvertTo-Json $body -Compress

                Invoke-RestMethod -Uri "$api/repos/$owner/$repo/contents/$($relPath -replace '\\','/')" -Headers @{
                    "Authorization" = "Bearer $env:GITHUB_TOKEN"
                    "Accept" = "application/vnd.github+json"
                } -Method Put -Body $jsonBody -ContentType "application/json" -TimeoutSec 30 | Out-Null
                Log "   已通过 GitHub API 上传 $relPath"
            }
            Log "GitHub API 同步完成 ✅"
        } catch {
            Log "GitHub API 上传失败（微信已推送，不影响）：$($_.Exception.Message)"
        }
    } else {
        Log "未配置 GITHUB_TOKEN，远端 picks 未同步（微信已推送）"
    }
} else {
    Log "pick 命令结束但未生成 $flagPath，退出码 $exitCode，请检查上方日志"
    exit 1
}

Log "===== Windows 本地兜底完成 ====="
