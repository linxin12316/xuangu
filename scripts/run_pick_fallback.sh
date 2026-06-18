#!/bin/bash
set -euo pipefail

REPO_DIR="/Users/jiuyueshenfeng/xuangu"
LOCK_DIR="/tmp/xuangu-pick.lock"
LOG_FILE="$REPO_DIR/logs/local-fallback.log"

mkdir -p "$REPO_DIR/logs"

log() {
  echo "$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "另一个 pick 进程正在运行，跳过本地兜底"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

cd "$REPO_DIR"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git pull --ff-only origin main >/dev/null 2>&1 || log "git pull 失败，继续使用本地状态判断"
else
  log "当前目录不是 git 仓库，继续使用本地状态判断"
fi

TODAY=$(TZ=Asia/Shanghai date +%Y-%m-%d)
export TODAY
FLAG="picks/${TODAY}-pushed.flag"

if [ -f "$FLAG" ]; then
  log "${FLAG} 已存在，说明今日已成功推送，本地兜底跳过"
  exit 0
fi

HOUR=$(TZ=Asia/Shanghai date +%H)
if [ "$HOUR" -ge 9 ]; then
  log "已到 09:00 或之后，本地兜底不再发送早盘策略"
  exit 0
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${SCKEY:-}" ]; then
  log "未配置 SCKEY：请复制 .env.example 为 .env 并填写 Server 酱 SendKey"
  exit 1
fi

export TZ=Asia/Shanghai
log "GitHub 尚未成功推送，启动本地早盘兜底"
python3 -m src.main pick

if [ -f "$FLAG" ]; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git add picks/
    if ! git diff --cached --quiet; then
      git config user.name "xuangu-local-fallback"
      git config user.email "xuangu-local-fallback@local"
      git commit -m "chore: local fallback pick ${TODAY} [skip ci]"
      git push origin main || log "git push 失败；微信已推送，本地 flag 已保留"
    fi
  elif [ -n "${GITHUB_TOKEN:-}" ]; then
    python3 - <<'PY'
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

owner = "linxin12316"
repo = "xuangu"
branch = "main"
today = os.environ["TODAY"]
token = os.environ["GITHUB_TOKEN"]
paths = [Path("picks") / f"{today}.json", Path("picks") / f"{today}-pushed.flag"]


def request(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return None
        raise

for path in paths:
    if not path.exists():
        continue
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.as_posix()}"
    current = request("GET", f"{api}?ref={branch}")
    payload = {
        "message": f"chore: local fallback pick {today} [skip ci]",
        "content": base64.b64encode(path.read_bytes()).decode(),
        "branch": branch,
    }
    if current and current.get("sha"):
        payload["sha"] = current["sha"]
    request("PUT", api, payload)
PY
    log "已通过 GitHub API 上传 picks 和 pushed flag"
  else
    log "当前不是 git 仓库且未配置 GITHUB_TOKEN；微信已推送，但远端 flag 未同步，可能无法阻止 GitHub 延迟重复推送"
  fi
  log "本地早盘兜底完成"
else
  log "pick 命令结束但未生成 ${FLAG}，请检查上方日志"
  exit 1
fi
