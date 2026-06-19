"""daemon — Windows 主力部署调度器

连续运行，30 秒 tick 循环，调度盘前选股、盘中实时资讯、晚间复盘。

用法：
  python -m src.daemon              # 正常启动
  python -m src.daemon --dry-run    # 跑一轮调度后退出（不推送）
"""
from __future__ import annotations
import os
import sys
import time
import signal
from datetime import date, datetime, time as dtime
from pathlib import Path

# ---- 全局状态 ----
_morning_done = False
_evening_done = False
_last_date: date | None = None
_last_radar_ts: float = 0
_running = True

LOG_FILE: str | None = None


# ---- 工具 ----
def _beijing() -> datetime:
    """返回北京时间。"""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        return datetime.now(tz)
    except Exception:
        return datetime.now()  # fallback UTC+8 内建


def _is_trading_day() -> bool:
    """简单交易日判断（周末）。复杂判断交给 data_loader.is_trading_day。"""
    from .data_loader import is_trading_day
    return is_trading_day()


def _is_trading_hours(dt: datetime) -> bool:
    """判断是否在 A 股连续竞价时段。"""
    t = dt.time()
    return (dtime(9, 25) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(14, 57))


def _is_premarket(dt: datetime) -> bool:
    """判断是否在盘前时段。"""
    t = dt.time()
    return dtime(8, 0) <= t < dtime(9, 25)


def _is_aftermarket(dt: datetime) -> bool:
    """判断是否在盘后时段。"""
    t = dt.time()
    return dtime(15, 0) <= t < dtime(19, 0)


def log(msg: str) -> None:
    """带时间戳的输出。"""
    ts = _beijing().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def _load_dotenv(path: str) -> None:
    """加载 .env 文件到环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key:
                os.environ.setdefault(key, val)


def _single_instance() -> bool:
    """单例锁。返回 True = 已有实例在运行。"""
    lock = Path(__file__).resolve().parent.parent / ".daemon.lock"
    try:
        if lock.exists():
            pid = int(lock.read_text().strip())
            try:
                os.kill(pid, 0)  # 检查进程是否存在
                print(f"⚠️  daemon 已在运行（PID {pid}），退出")
                return True
            except OSError:
                pass  # 僵尸锁，覆盖
        lock.write_text(str(os.getpid()))
        return False
    except Exception as e:
        print(f"⚠️  单例锁异常: {e}")
        return False


def _cleanup_lock() -> None:
    lock = Path(__file__).resolve().parent.parent / ".daemon.lock"
    try:
        if lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink()
    except Exception:
        pass


# ---- 调度任务 ----
def _run_morning_pick(dry_run: bool) -> bool:
    """盘前选股。返回 True 表示成功。"""
    log("=== 盘前选股 ===")
    try:
        from .main import cmd_pick
        code = cmd_pick(dry_run=dry_run)
        ok = code == 0
        log(f"盘前选股 {'成功✅' if ok else f'失败(code={code})'}")
        return ok
    except Exception as e:
        log(f"盘前选股异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def _run_evening_review(dry_run: bool) -> bool:
    """晚间复盘。返回 True 表示成功。"""
    log("=== 晚间复盘 ===")
    try:
        from .main import cmd_evening
        code = cmd_evening(dry_run=dry_run)
        ok = code == 0
        log(f"晚间复盘 {'成功✅' if ok else f'失败(code={code})'}")
        return ok
    except Exception as e:
        log(f"晚间复盘异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def _run_radar(dry_run: bool) -> int:
    """运行一轮资讯雷达。返回命中条数。"""
    try:
        # 盘中密集轮询时不重复输出 "start at" 日志，间隔长时再打
        now = _beijing()
        t = now.time()
        is_trading = _is_trading_hours(now)
        if not is_trading:
            log("=== 资讯雷达（闲时轮询）===")
        from .radar_runner import run_radar
        return run_radar(dry_run=dry_run, skip_llm=not is_trading)
    except Exception as e:
        log(f"资讯雷达异常: {e}")
        import traceback
        traceback.print_exc()
        return 0


# ---- 主循环 ----
def run(dry_run: bool = False) -> int:
    """daemon 主循环。dry_run=True 跑一轮调度后退出。"""
    global _morning_done, _evening_done, _last_date, _last_radar_ts, LOG_FILE

    # 单例锁
    if not dry_run and _single_instance():
        return 1

    # 日志文件
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    LOG_FILE = str(log_dir / "daemon.log")
    log("=== Daemon 启动 ===")
    log(f"PID: {os.getpid()}")
    log(f"日志: {LOG_FILE}")
    log(f"持仓板块关键词: keywords.py {len(__import__('src.radar.keywords', fromlist=['']).KEYWORD_SECTOR_MAP)} 板块")
    log(f"DeepSeek: {'已配置✅' if os.environ.get('DEEPSEEK_API_KEY') else '未配置'}")
    log(f"SCKEY: {'已配置✅' if os.environ.get('SCKEY') else '未配置⚠️'}")

    # 设置信号处理
    def _handler(signum, frame):
        global _running
        log(f"收到信号 {signum}，优雅退出…")
        _running = False
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    tick_count = 0
    while _running:
        now = _beijing()
        today = now.date()

        # 每日复位
        if _last_date != today:
            _morning_done = False
            _evening_done = False
            _last_date = today

        # 如果不是交易日
        trading_day = _is_trading_day()

        # === 1. 盘前选股（08:25-08:55）===
        if (trading_day and not _morning_done
                and dtime(8, 25) <= now.time() <= dtime(8, 55)):
            _run_morning_pick(dry_run=dry_run)
            _morning_done = True

        # === 2. 晚间复盘（18:20-18:50）===
        if (trading_day and not _evening_done
                and dtime(18, 20) <= now.time() <= dtime(18, 55)):
            _run_evening_review(dry_run=dry_run)
            _evening_done = True

        # === 3. 资讯雷达（根据时段定间隔）===
        now_ts = time.time()
        if _is_trading_hours(now):
            interval = 120  # 盘中 2 分钟
        elif _is_premarket(now) or _is_aftermarket(now):
            interval = 180  # 盘前盘后 3 分钟
        else:
            interval = 600  # 空闲 10 分钟
        # 首次启动立即跑一轮
        if _last_radar_ts == 0 or (now_ts - _last_radar_ts) >= interval:
            _run_radar(dry_run=dry_run)
            _last_radar_ts = now_ts

        if dry_run:
            log("dry-run 模式：一轮完成，退出")
            break

        # === 4. sleep ===
        tick_count += 1
        time.sleep(30)  # 30 秒 tick

    _cleanup_lock()
    log("=== Daemon 已停止 ===")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    # 启动时自动加载 .env
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    _load_dotenv(env_path)
    sys.exit(run(dry_run=dry))
