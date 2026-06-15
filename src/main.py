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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

from . import data_loader as dl
from . import filters as flt
from . import report as rpt
from .config import load_config, apply_blacklist, dedup_by_industry
from .notifier import send_to_wechat
from .scoring import score_one


PICKS_DIR = Path(__file__).resolve().parent.parent / "picks"


# ---------- pick ----------


def cmd_pick(dry_run: bool = False, top_n: int = 5, force: bool = False) -> int:
    if not dry_run and not force and not dl.is_trading_day():
        print("⏸️  今日非交易日，跳过推送")
        return 0

    cfg = load_config()
    print(f"📋 配置：黑名单 {len(cfg['blacklist']['codes'])} 代码 / "
          f"{len(cfg['blacklist']['name_keywords'])} 关键词，"
          f"同行业上限 {cfg['max_per_industry']}，Top {cfg['max_picks']}")
    top_n = cfg.get("max_picks", top_n)

    print("📥 拉取全市场快照…")
    spot = dl.fetch_spot(use_mock=dry_run)
    print(f"   全市场 {len(spot)} 只")

    spot = flt.apply_all(spot)
    print(f"   过滤地雷股后剩 {len(spot)} 只")

    spot = apply_blacklist(spot, cfg["blacklist"])
    print(f"   过滤黑名单后剩 {len(spot)} 只")

    # 行业映射（Tushare stock_basic，免费可用）
    industry_map = dl.fetch_industry_map(use_mock=dry_run)

    # 全市场近 5 日涨幅快照（Tushare daily by trade_date，免费可用）
    market_window = dl.fetch_market_window(days=6, use_mock=dry_run)

    print("📥 计算强势板块（行业平均5日涨幅）…")
    # 取 Top 8 热门行业(给后面留够个股筛选空间)
    industries = dl.fetch_industry_rank(top_n=8, use_mock=dry_run)
    if not industries.empty:
        members_col = "成员数" if "成员数" in industries.columns else None
        info = industries['板块名称'].tolist()
        if members_col:
            info = [f"{n}({c}只)" for n, c in zip(industries['板块名称'], industries[members_col])]
        print(f"   Top 8 热门行业: {info}")
    else:
        print("   ⚠️  板块接口不可用，降级为全市场涨幅排序")

    # 构建 spot_map + turnover_map
    name_col = "名称" if "名称" in spot.columns else spot.columns[1]
    spot_map = {str(r["代码"]).zfill(6): r[name_col] for _, r in spot.iterrows()}
    turn_col = next((c for c in spot.columns if "换手" in c), None)
    turnover_map: dict[str, float] = {}
    if turn_col:
        for _, r in spot.iterrows():
            code = str(r["代码"]).zfill(6)
            try:
                turnover_map[code] = float(r[turn_col])
            except (ValueError, TypeError):
                pass

    candidate_codes: dict[str, str] = {}  # code -> industry
    # 路径 A: 行业择强 + 个股趋势双重筛选
    if not industries.empty and market_window is not None and not market_window.empty:
        hot_industries = set(industries["板块名称"].tolist())
        # 1) 给所有股票打上行业标签
        df = market_window.copy()
        df["industry"] = df["code"].map(industry_map)
        # 2) 只保留热门行业的股票
        df = df[df["industry"].isin(hot_industries)]
        # 3) 股票必须在 spot_map 里(能拿到现价/换手率)且未被黑名单/地雷股过滤掉
        df = df[df["code"].isin(spot_map.keys())]
        # 4) 按个股 5 日涨幅 + 成交额加权排序,取 Top 200
        if not df.empty:
            df["__rank"] = (
                df["chg_5d"].fillna(0).rank(pct=True) * 0.6
                + df["amount_now"].fillna(0).rank(pct=True) * 0.4
            )
            df = df.sort_values("__rank", ascending=False).head(200)
            for _, r in df.iterrows():
                candidate_codes[r["code"]] = r["industry"]
            print(f"   ✅ 热门行业候选池 {len(candidate_codes)} 只 (行业 Top {len(hot_industries)} → 个股 Top 200)")

    # 降级路径
    if not candidate_codes:
        # 优先用 market_window 的真 5 日涨幅
        if market_window is not None and not market_window.empty:
            print("   📥 退化方案 A：全市场近5日涨幅+成交额 Top 200")
            df = market_window.copy()
            df = df[df["code"].isin(spot_map.keys())]
            if not df.empty:
                df["__rank"] = (
                    df["chg_5d"].fillna(0).rank(pct=True) * 0.6
                    + df["amount_now"].fillna(0).rank(pct=True) * 0.4
                )
                df = df.sort_values("__rank", ascending=False).head(200)
                for _, r in df.iterrows():
                    candidate_codes[r["code"]] = industry_map.get(r["code"], "全市场")

    if not candidate_codes:
        # 最差降级:用 spot 单日数据
        print("   📥 退化方案 B：用 spot 当日涨幅+成交额 Top 200 作为候选池")
        sorted_spot = spot.copy()
        if "涨跌幅" in sorted_spot.columns and "成交额" in sorted_spot.columns:
            sorted_spot["__rank"] = (
                sorted_spot["涨跌幅"].fillna(0).rank(pct=True) * 0.5
                + sorted_spot["成交额"].fillna(0).rank(pct=True) * 0.5
            )
            sorted_spot = sorted_spot.sort_values("__rank", ascending=False)
        for _, r in sorted_spot.head(200).iterrows():
            code = str(r["代码"]).zfill(6)
            # 用 Tushare 行业映射;查不到时回退"全市场"
            candidate_codes[code] = industry_map.get(code, "全市场")

    print(f"   候选池 {len(candidate_codes)} 只")

    # --- 大盘风险评级 ---
    risk_score, risk_desc = _market_risk(dry_run=dry_run)
    if risk_score <= 2:
        msg = f"⚠️ 今日大盘风险评分 {risk_score}/10：{risk_desc}，跳过选股推送"
        print(f"   {msg}")
        if not dry_run:
            send_to_wechat(f"⏸️ 选股暂停 {datetime.now().strftime('%m-%d')}", f"# {msg}\n\n大盘风险过高，今日不推送候选。")
        return 0

    # --- 资金维度（全市场北向净流入） ---
    north_market_flow = _fetch_north_market_flow(dry_run=dry_run)
    if north_market_flow is not None:
        print(f"   💰 北向资金净流入 {north_market_flow:+.1f} 亿")

    # --- 预热 Tushare 全市场缓存（每天调一次,用于新因子）---
    print("📥 预热 Tushare 全市场缓存（北向/估值/涨停）…")
    dl.fetch_hk_hold_market(use_mock=dry_run)
    dl.fetch_daily_basic_market(use_mock=dry_run)
    dl.fetch_limit_list_market(use_mock=dry_run)

    # --- 并发打分 ---
    print(f"📥 并发拉取日线 + 打分（{len(candidate_codes)} 只）…")
    scored = list(_score_candidates_concurrent(
        candidate_codes, spot_map, dry_run,
        north_market_flow=north_market_flow,
        turnover_map=turnover_map,
    ))

    # --- 连续上榜惩罚 ---
    streak_map = _compute_streak(scored)
    scored = _adjust_for_repeats(scored, streak_map)

    scored.sort(key=lambda x: x.total, reverse=True)
    # --- 同行业去重（Top 5 中同一行业最多保留 N 只）---
    deduped = dedup_by_industry(scored, max_per_industry=cfg["max_per_industry"])
    if len(deduped) < len(scored):
        print(f"   🧹 同行业去重：{len(scored)} → {len(deduped)} (上限 {cfg['max_per_industry']}/行业)")
    picks = deduped[:top_n]

    print(f"\n🏆 Top {len(picks)}:")
    for i, s in enumerate(picks, 1):
        print(f"   {i}. {s.code} {s.name} ({s.industry}) 综合 {s.total:.1f}")

    streak_map = _compute_streak(picks)

    md = rpt.render_pick_report(
        industries, picks, streak_map=streak_map,
        risk_score=risk_score, risk_desc=risk_desc,
        north_flow=north_market_flow,
    )

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


def _market_risk(dry_run: bool = False) -> tuple[int, str]:
    """大盘风险评级 0-10。

    基于沪深300的 20 日涨跌幅 + 年化波动率：
    - 8-10：强势市场
    - 5-7：中性
    - 3-4：偏弱，报告加 ⚠️
    - 0-2：弱势，跳过选股
    """
    if dry_run:
        return (7, "mock 数据，模拟中性市场")
    try:
        import akshare as ak

        df = ak.stock_zh_index_daily(symbol="sh000300")
        closes = df["close"].astype(float).tail(20)
        if len(closes) < 10:
            return (5, "沪深300数据不足")
        chg_20d = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] * 100
        returns = closes.pct_change().dropna()
        volatility = returns.std() * (252**0.5) * 100

        if chg_20d > 3 and volatility < 25:
            return (8, "市场趋势向上，波动可控")
        if chg_20d > 1:
            return (6, "市场温和上涨")
        if chg_20d > -2:
            return (4, "市场偏弱，注意风险")
        if chg_20d > -5:
            return (2, "市场明显下跌，建议谨慎")
        return (0, "市场大幅下跌，主动回避")
    except Exception as e:
        print(f"   ⚠️  市场风险评级失败: {e}")
        return (5, "接口异常，默认中性")


def _fetch_north_market_flow(dry_run: bool = False) -> float | None:
    """全市场北向资金净流入（亿元），失败返回 None。

    个股北向几乎拿不到时回退到该全市场数据。
    """
    if dry_run:
        return 38.5
    try:
        import akshare as ak

        hsgt = ak.stock_hsgt_fund_flow_summary_em()
        net = hsgt.iloc[-1]
        for k, v in net.items():
            if "净" in str(k) and "流入" in str(k):
                return float(v)
        return None
    except Exception:
        return None


def _adjust_for_repeats(scored: list, streak_map: dict[str, int]) -> list:
    """连续 3 天以上上榜则减 10 分，防止审美疲劳。"""
    out = []
    for s in scored:
        streak = streak_map.get(s.code, 0)
        if streak >= 3:
            s.total = max(0, s.total - 10)
        out.append(s)
    return out


def _score_candidates_concurrent(
    candidate_codes: dict[str, str],
    spot_map: dict[str, str],
    dry_run: bool,
    north_market_flow: float | None = None,
    turnover_map: dict[str, float] | None = None,
    max_workers: int = 8,
) -> list:
    """并发拉取日线并打分。"""
    def _work(code: str, industry: str):
        try:
            kline = dl.fetch_kline(code, days=80, use_mock=dry_run)
            north = dl.fetch_north_flow(code, use_mock=dry_run)
            to = turnover_map.get(code) if turnover_map else None
            factors = dl.get_stock_factors(code, use_mock=dry_run)
            # turnover 优先用 spot 当日值,缺失时回退 Tushare 上一交易日值
            if to is None and factors.get("turnover_rate") is not None:
                to = factors["turnover_rate"]
            s = score_one(
                code, spot_map[code], industry, kline, north,
                north_market_flow=north_market_flow,
                turnover_rate=to,
                limit_times_10d=factors.get("limit_times_10d", 0) or 0,
                max_streak=factors.get("max_streak", 0) or 0,
                pe_ttm=factors.get("pe_ttm"),
                pb=factors.get("pb"),
                longhu_active=None,  # 需 2000 积分,暂中性
                roe=None,             # 需 2000 积分,暂中性
                profit_growth=None,   # 需 2000 积分,暂中性
            )
            return s
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️  {code} 打分失败: {e}")
            return None

    total = len(candidate_codes)
    done = 0
    results: list = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {
            pool.submit(_work, code, ind): code
            for code, ind in candidate_codes.items()
        }
        for fut in as_completed(fut_map):
            done += 1
            if done % 10 == 0 or done == total:
                print(f"   进度 {done}/{total}")
            s = fut.result()
            if s and s.total > 0:
                results.append(s)
    return results


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
