"""历史选股回测——读取 picks/*.json 对比真实涨跌幅，计算命中率。

用法：
  python -m src.backtest              # 分析最近 14 天，推送微信
  python -m src.backtest --dry-run    # 控制台预览
  python -m src.backtest --days 30    # 自定义回看窗口

数据源：通过 data_loader.fetch_kline 走 Tushare daily（海外稳）。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import data_loader as dl

PICKS_DIR = Path(__file__).resolve().parent.parent / "picks"
WINDOWS = (1, 3, 5)  # 分别看 1/3/5 个交易日后的表现


def _load_recent_picks(days_back: int = 14) -> list[dict]:
    """加载最近 days_back 天的 picks 记录。"""
    today = date.today()
    result = []
    if not PICKS_DIR.exists():
        return result
    for f in sorted(PICKS_DIR.glob("*.json"), reverse=True):
        try:
            d = datetime.strptime(f.stem, "%Y-%m-%d").date()
            if (today - d).days > days_back:
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            for p in data:
                p["_pick_date"] = f.stem
            result.extend(data)
        except Exception:
            continue
    return result


def _benchmark_chg(pick_date: str, days: int) -> Optional[float]:
    """沪深300同期涨幅（百分比），用于跑赢基准。"""
    try:
        pro = dl.get_tushare()
        if pro is None:
            return None
        start = (datetime.strptime(pick_date, "%Y-%m-%d")).strftime("%Y%m%d")
        end = (datetime.strptime(pick_date, "%Y-%m-%d") + timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
        df = pro.index_daily(ts_code="000300.SH", start_date=start, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date").reset_index(drop=True)
        if len(df) < 2:
            return None
        idx = min(days, len(df) - 1)
        return float((df["close"].iloc[idx] - df["close"].iloc[0]) / df["close"].iloc[0] * 100)
    except Exception:
        return None


def _price_after(
    code: str,
    pick_date: str,
    days: int,
) -> Optional[float]:
    """取 pick_date 之后第 days 个交易日的最新价。

    如果 days=0 则返回 pick_date 当日的最新价（最后一条）。
    """
    try:
        start_obj = datetime.strptime(pick_date, "%Y-%m-%d")
        # 向后多取一些确保覆盖 days 个交易日
        end_obj = start_obj + timedelta(days=days * 2 + 10)
        kline = dl.fetch_kline(
            code,
            days=days * 2 + 10,
            use_mock=False,
        )
        if kline is None or kline.empty:
            return None
        # 找到 pick_date 当天或之后的 K 线
        date_col = next(
            (c for c in kline.columns if "日期" in c or "date" in c.lower()),
            None,
        )
        if date_col is None:
            return None
        kline = kline.copy()
        kline["_date"] = pd.to_datetime(kline[date_col]).dt.date
        pick = datetime.strptime(pick_date, "%Y-%m-%d").date()
        after = kline[kline["_date"] >= pick]
        if after.empty:
            return None
        # 取第 days 条（0=当天，1=次日…）
        idx = min(days, len(after) - 1)
        close_col = next(
            (c for c in after.columns if "收盘" in c or c == "close"),
            None,
        )
        if close_col is None:
            return None
        return float(after.iloc[idx][close_col])
    except Exception:
        return None


def compute_stats(dry_run: bool = False, days_back: int = 14) -> dict:
    """分析近期 picks 表现。"""
    picks = _load_recent_picks(days_back=1 if dry_run else days_back)

    if not picks:
        return {"error": "没有找到历史 picks 记录", "picks": []}

    # 按 pick_date 分组统计
    by_date: dict[str, list] = {}
    for p in picks:
        by_date.setdefault(p["_pick_date"], []).append(p)

    # 同期沪深300基准（每个 pick_date 各算一次,避免重复请求）
    bench_cache: dict[tuple[str, int], Optional[float]] = {}

    results = []
    for pick_date, day_picks in by_date.items():
        for p in day_picks:
            entry = {
                "code": p.get("code", "?"),
                "name": p.get("name", "?"),
                "pick_date": pick_date,
                "entry_price": p.get("last_close", 0),
                "total_score": p.get("total", 0),
            }
            entry_price = entry["entry_price"]
            if entry_price <= 0:
                continue
            for w in WINDOWS:
                price = _price_after(p["code"], pick_date, w)
                if price and entry_price > 0:
                    chg = (price - entry_price) / entry_price * 100
                    entry[f"chg_{w}d"] = chg
                    # 跑赢基准
                    bk = bench_cache.get((pick_date, w))
                    if bk is None and (pick_date, w) not in bench_cache:
                        bk = _benchmark_chg(pick_date, w)
                        bench_cache[(pick_date, w)] = bk
                    if bk is not None:
                        entry[f"alpha_{w}d"] = chg - bk
                else:
                    entry[f"chg_{w}d"] = None
            results.append(entry)

    if not results:
        return {"error": "所有记录价格获取失败（可能是非交易日无数据）", "picks": []}

    # 统计
    stats = {
        "period": f"{results[0]['pick_date']} ~ {results[-1]['pick_date']}",
        "total_picks": len(results),
        "by_window": {},
    }
    for w in WINDOWS:
        valid = [r for r in results if r.get(f"chg_{w}d") is not None]
        if not valid:
            continue
        chgs = [r[f"chg_{w}d"] for r in valid]
        wins = sum(1 for c in chgs if c > 0)
        alphas = [r[f"alpha_{w}d"] for r in valid if r.get(f"alpha_{w}d") is not None]
        beat = sum(1 for a in alphas if a > 0) if alphas else None
        stats["by_window"][f"{w}d"] = {
            "count": len(valid),
            "win": wins,
            "win_rate": round(wins / len(valid) * 100, 1),
            "avg_return": round(sum(chgs) / len(chgs), 2),
            "max_gain": round(max(chgs), 2),
            "max_loss": round(min(chgs), 2),
            "alpha_avg": round(sum(alphas) / len(alphas), 2) if alphas else None,
            "beat_bench_rate": round(beat / len(alphas) * 100, 1) if alphas else None,
        }

    # 最佳/最差（按 5 日涨幅）
    if "5d" in stats["by_window"]:
        sorted_5d = sorted(
            [r for r in results if r.get("chg_5d") is not None],
            key=lambda r: r["chg_5d"],
            reverse=True,
        )
        if sorted_5d:
            best = sorted_5d[0]
            worst = sorted_5d[-1]
            stats["best"] = {
                "code": best["code"],
                "name": best["name"],
                "date": best["pick_date"],
                "chg_5d": best["chg_5d"],
            }
            stats["worst"] = {
                "code": worst["code"],
                "name": worst["name"],
                "date": worst["pick_date"],
                "chg_5d": worst["chg_5d"],
            }

    return stats


def render_report(stats: dict) -> str:
    """生成周报 markdown。"""
    if "error" in stats:
        return f"# ⚠️ 回测报告\n\n{stats['error']}"

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 📊 选股回测 · {today}", ""]
    lines.append(f"**分析周期**：{stats['period']}")
    lines.append(f"**推送候选总数**：{stats['total_picks']}")
    lines.append("")

    lines.append("## 🎯 命中率")
    lines.append("")
    lines.append("| 持有期 | 样本数 | 胜率 | 平均收益 | 跑赢沪深300 | 最大收益 | 最大回撤 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for w in WINDOWS:
        ws = stats["by_window"].get(f"{w}d")
        if ws:
            alpha_str = (
                f"{ws['beat_bench_rate']}% (α{ws['alpha_avg']:+.2f}%)"
                if ws.get("beat_bench_rate") is not None
                else "—"
            )
            lines.append(
                f"| {w} 日 | {ws['count']} | {ws['win_rate']}% | "
                f"{ws['avg_return']:+.2f}% | {alpha_str} | "
                f"{ws['max_gain']:+.2f}% | {ws['max_loss']:.2f}% |"
            )
    lines.append("")

    if "best" in stats:
        b = stats["best"]
        lines.append(f"## 🏆 本周最佳（5日涨幅）")
        lines.append(f"- **{b['name']}**（{b['code']}）{b['date']} 推送 → **{b['chg_5d']:+.2f}%**")
        lines.append("")

    if "worst" in stats:
        w = stats["worst"]
        lines.append(f"## 📉 本周最差（5日涨幅）")
        lines.append(f"- **{w['name']}**（{w['code']}）{w['date']} 推送 → {w['chg_5d']:.2f}%")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> ⚠️ 回测数据仅供参考，过去表现不代表未来收益。")
    lines.append(f"*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="A 股选股回测")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=14, help="回看窗口天数")
    args = parser.parse_args(argv)

    stats = compute_stats(dry_run=args.dry_run, days_back=args.days)
    md = render_report(stats)

    if args.dry_run:
        print(md)
        return 0

    from .notifier import send_to_wechat

    title = f"📊 选股回测 {datetime.now().strftime('%m-%d')}"
    ok = send_to_wechat(title, md)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
