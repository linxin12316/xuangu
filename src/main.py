"""主入口：盘前选股 / 盘后复盘。

用法：
  python -m src.main pick           # 真实选股 + 推送
  python -m src.main pick --dry-run # 离线 mock 跑一遍
  python -m src.main review         # 盘后复盘
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import data_loader as dl
from . import filters as flt
from . import report as rpt
from .notifier import send_to_wechat
from .scoring import score_one


PICKS_DIR = Path(__file__).resolve().parent.parent / "picks"


# ---------- pick ----------


def cmd_pick(dry_run: bool = False, top_n: int = 5, force: bool = False) -> int:
    if not dry_run and not force and not dl.is_trading_day():
        print("⏸️  今日非交易日，跳过推送")
        return 0

    print("📥 拉取全市场快照…")
    spot = dl.fetch_spot(use_mock=dry_run)
    print(f"   全市场 {len(spot)} 只")

    spot = flt.apply_all(spot)
    print(f"   过滤地雷股后剩 {len(spot)} 只")

    print("📥 计算强势板块…")
    industries = dl.fetch_industry_rank(top_n=5, use_mock=dry_run)
    print(f"   Top 5: {industries['板块名称'].tolist()}")

    candidate_codes: dict[str, str] = {}  # code -> industry
    for _, row in industries.iterrows():
        try:
            cons = dl.fetch_industry_cons(row["板块名称"], use_mock=dry_run)
            for _, c in cons.iterrows():
                code = str(c["代码"]).zfill(6)
                if code not in candidate_codes:
                    candidate_codes[code] = row["板块名称"]
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️  板块 {row['板块名称']} 成分股获取失败: {e}")

    # 与 spot 取交集（过滤掉 ST/小市值/北交所）
    name_col = "名称" if "名称" in spot.columns else spot.columns[1]
    spot_map = {str(r["代码"]).zfill(6): r[name_col] for _, r in spot.iterrows()}
    candidate_codes = {c: ind for c, ind in candidate_codes.items() if c in spot_map}
    print(f"   候选池 {len(candidate_codes)} 只")

    print("📥 拉日线 + 打分…")
    scored = []
    for i, (code, industry) in enumerate(candidate_codes.items(), 1):
        if i % 20 == 0:
            print(f"   进度 {i}/{len(candidate_codes)}")
        try:
            kline = dl.fetch_kline(code, days=80, use_mock=dry_run)
            north = dl.fetch_north_flow(code, use_mock=dry_run)
            s = score_one(code, spot_map[code], industry, kline, north)
            if s and s.total > 0:
                scored.append(s)
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️  {code} 打分失败: {e}")

    scored.sort(key=lambda x: x.total, reverse=True)
    picks = scored[:top_n]

    print(f"\n🏆 Top {len(picks)}:")
    for i, s in enumerate(picks, 1):
        print(f"   {i}. {s.code} {s.name} ({s.industry}) 综合 {s.total:.1f}")

    streak_map = _compute_streak(picks)

    md = rpt.render_pick_report(industries, picks, streak_map=streak_map)

    if dry_run:
        print("\n" + "=" * 60)
        print(md)
        print("=" * 60)
        return 0

    # 落盘候选清单（供盘后复盘使用）
    today = datetime.now().strftime("%Y-%m-%d")
    PICKS_DIR.mkdir(exist_ok=True)
    pick_file = PICKS_DIR / f"{today}.json"
    pick_file.write_text(
        json.dumps([s.as_dict() for s in picks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"💾 候选清单已保存到 {pick_file}")

    title = f"📈 选股报告 {today}"
    ok = send_to_wechat(title, md)
    return 0 if ok else 1


# ---------- review ----------


def cmd_review(dry_run: bool = False) -> int:
    if not dry_run and not dl.is_trading_day():
        print("⏸️  今日非交易日，跳过复盘")
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    pick_file = PICKS_DIR / f"{today}.json"
    if not pick_file.exists():
        print(f"⚠️  未找到今日候选清单 {pick_file}，跳过复盘")
        return 0

    picks = json.loads(pick_file.read_text(encoding="utf-8"))
    print(f"📥 加载今日候选 {len(picks)} 只")

    spot = dl.fetch_spot(use_mock=dry_run)
    name_col = "名称" if "名称" in spot.columns else spot.columns[1]
    spot_idx: dict[str, dict] = {}
    for _, r in spot.iterrows():
        code = str(r["代码"]).zfill(6)
        spot_idx[code] = {
            "name": r[name_col],
            "close": float(r.get("最新价", 0) or 0),
            "chg_pct": float(r.get("涨跌幅", 0) or 0),
        }

    today_quotes = {p["code"]: spot_idx[p["code"]] for p in picks if p["code"] in spot_idx}

    market_summary = _market_summary(dry_run=dry_run)

    md = rpt.render_review_report(today, picks, today_quotes, market_summary)

    if dry_run:
        print("\n" + "=" * 60)
        print(md)
        print("=" * 60)
        return 0

    title = f"🔍 复盘报告 {today}"
    ok = send_to_wechat(title, md)
    return 0 if ok else 1


# ---------- helpers ----------


def _compute_streak(picks) -> dict[str, int]:
    """计算每只候选连续上榜天数（基于 picks/*.json 历史）。"""
    if not PICKS_DIR.exists():
        return {}
    files = sorted(PICKS_DIR.glob("*.json"), reverse=True)
    streak = {s.code: 1 for s in picks}
    today_codes = {s.code for s in picks}
    yesterday = (datetime.now().date() - timedelta(days=1))
    for f in files[:10]:
        try:
            d = datetime.strptime(f.stem, "%Y-%m-%d").date()
            if d > yesterday:
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            day_codes = {p["code"] for p in data}
            for c in list(today_codes):
                if c in day_codes:
                    streak[c] += 1
                else:
                    today_codes.discard(c)
        except Exception:
            continue
    return streak


def _market_summary(dry_run: bool = False) -> dict:
    if dry_run:
        return {
            "上证指数": "3120.5 (+0.85%)",
            "深证成指": "10250.3 (+1.20%)",
            "创业板指": "2080.6 (+1.85%)",
            "北向资金净流入": "+38.5 亿",
        }
    try:
        import akshare as ak

        out = {}
        idx_df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        for key in ("上证指数", "深证成指", "创业板指"):
            row = idx_df[idx_df["名称"] == key]
            if not row.empty:
                price = float(row.iloc[0]["最新价"])
                chg = float(row.iloc[0]["涨跌幅"])
                out[key] = f"{price:.2f} ({chg:+.2f}%)"
        try:
            hsgt = ak.stock_hsgt_fund_flow_summary_em()
            net = hsgt.iloc[-1]
            net_val = next(
                (v for k, v in net.items() if "净" in str(k) and "流入" in str(k)), None
            )
            if net_val is not None:
                out["北向资金净流入"] = f"{float(net_val):+.1f} 亿"
        except Exception:
            pass
        return out
    except Exception:
        return {}


# ---------- entry ----------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A 股每日选股工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pick = sub.add_parser("pick", help="盘前选股")
    p_pick.add_argument("--dry-run", action="store_true", help="使用 mock 数据，不推送")
    p_pick.add_argument("--top", type=int, default=5)
    p_pick.add_argument("--force", action="store_true", help="非交易日也强制运行（验证用）")

    p_rev = sub.add_parser("review", help="盘后复盘")
    p_rev.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "pick":
            return cmd_pick(dry_run=args.dry_run, top_n=args.top, force=args.force)
        if args.cmd == "review":
            return cmd_review(dry_run=args.dry_run)
    except Exception as e:  # noqa: BLE001
        # 任何崩溃都推送一条微信提醒，避免静默失败
        import traceback
        tb = traceback.format_exc()
        print(tb)
        if not args.dry_run:
            md = (
                f"# ❌ 选股工具异常\n\n"
                f"**错误**: `{type(e).__name__}: {e}`\n\n"
                f"**说明**: 数据源接口不可用或网络故障。\n"
                f"常见原因：东方财富/新浪对 GitHub 海外机房限流。\n\n"
                f"**Action 日志**: 请到仓库 Actions 页面查看完整堆栈。\n"
            )
            send_to_wechat(f"❌ 选股异常 {datetime.now().strftime('%m-%d')}", md)
        return 2
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
