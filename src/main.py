"""主入口：盘前选股 / 盘后复盘 / 晚间复盘。

用法：
  python -m src.main pick           # 盘前选股 + 推送（08:27）
  python -m src.main pick --dry-run # 离线 mock 跑一遍
  python -m src.main review         # 盘后复盘（16:07，对比候选当日表现）
  python -m src.main evening        # 晚间深度复盘 + 明日 Top 3 候选（18:23）
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
from . import theme_scorer as ts
from . import zt_relay as ztr
from . import hot_leaders as hl
from .config import load_config, apply_blacklist, dedup_by_industry
from .notifier import send_to_wechat
from .scoring import score_one, compute_cross_sectional_factors
from .technical_signals import compute_technical_score


PICKS_DIR = Path(__file__).resolve().parent.parent / "picks"


# ---------- 共用：跑完整选股流程 ----------


def _run_full_pipeline(dry_run: bool, top_n: int, override_max_picks: bool = False):
    """完整跑一遍 spot → 行业/资金流 → 候选池 → 打分 → 同行业去重。

    返回 dict 含: picks, industries, concept_ff, industry_ff, zt_pool, lhb_detail,
    risk_score, risk_desc, north_market_flow, streak_map, cfg。
    供 cmd_pick / cmd_evening 共用。

    override_max_picks=True 时，忽略 cfg["max_picks"]，强制用入参 top_n（evening 用）。
    """
    cfg = load_config()
    print(f"📋 配置：黑名单 {len(cfg['blacklist']['codes'])} 代码 / "
          f"{len(cfg['blacklist']['name_keywords'])} 关键词，"
          f"同行业上限 {cfg['max_per_industry']}，Top {cfg['max_picks']}")
    if not override_max_picks:
        top_n = cfg.get("max_picks", top_n)

    print("📥 拉取全市场快照…")
    spot = dl.fetch_spot(use_mock=dry_run)
    print(f"   全市场 {len(spot)} 只")

    spot = flt.apply_all(spot)
    print(f"   过滤地雷股后剩 {len(spot)} 只")

    spot = apply_blacklist(spot, cfg["blacklist"])
    print(f"   过滤黑名单后剩 {len(spot)} 只")

    industry_map = dl.fetch_industry_map(use_mock=dry_run)
    market_window = dl.fetch_market_window(days=6, use_mock=dry_run)

    print("📥 计算强势板块（行业平均5日涨幅）…")
    industries = dl.fetch_industry_rank(top_n=8, use_mock=dry_run)

    print("📥 拉取同花顺资金流（行业 + 概念）…")
    industry_ff = dl.fetch_industry_fundflow(use_mock=dry_run)
    concept_ff = dl.fetch_concept_fundflow(use_mock=dry_run)
    fundflow_top_industries: set[str] = set()
    if industry_ff is not None and not industry_ff.empty:
        fundflow_top_industries = set(industry_ff.head(6)["行业"].tolist())

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

    candidate_codes: dict[str, str] = {}
    trend_industries = set(industries["板块名称"].tolist()) if not industries.empty else set()
    hot_industries = trend_industries | fundflow_top_industries
    if hot_industries and market_window is not None and not market_window.empty:
        df = market_window.copy()
        df["industry"] = df["code"].map(industry_map)
        df = df[df["industry"].isin(hot_industries)]
        df = df[df["code"].isin(spot_map.keys())]
        if not df.empty:
            df["__rank"] = (
                df["chg_5d"].fillna(0).rank(pct=True) * 0.6
                + df["amount_now"].fillna(0).rank(pct=True) * 0.4
            )
            df = df.sort_values("__rank", ascending=False).head(200)
            for _, r in df.iterrows():
                candidate_codes[r["code"]] = r["industry"]

    if not candidate_codes:
        if market_window is not None and not market_window.empty:
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
        sorted_spot = spot.copy()
        if "涨跌幅" in sorted_spot.columns and "成交额" in sorted_spot.columns:
            sorted_spot["__rank"] = (
                sorted_spot["涨跌幅"].fillna(0).rank(pct=True) * 0.5
                + sorted_spot["成交额"].fillna(0).rank(pct=True) * 0.5
            )
            sorted_spot = sorted_spot.sort_values("__rank", ascending=False)
        for _, r in sorted_spot.head(200).iterrows():
            code = str(r["代码"]).zfill(6)
            candidate_codes[code] = industry_map.get(code, "全市场")

    print(f"   候选池 {len(candidate_codes)} 只")

    # 题材池硬过滤：候选池 ∩ (themes.json 全部代码 ∪ 今日强势股命中的代码)
    # 用户偏好：只推 AI/资源主线相关的票，不推券商保险公用事业
    theme_pool = _build_theme_pool()
    if theme_pool:
        before = len(candidate_codes)
        filtered = {c: ind for c, ind in candidate_codes.items() if c in theme_pool}
        if filtered:
            candidate_codes = filtered
            print(f"   🎯 题材池过滤：{before} → {len(candidate_codes)} (themes.json + 强势股命中)")
        else:
            print(f"   ⚠️  题材池过滤后候选为空（{before} → 0），保留原候选池")

    risk_score, risk_desc = _market_risk(dry_run=dry_run)

    north_market_flow = _fetch_north_market_flow(dry_run=dry_run)
    if north_market_flow is not None:
        print(f"   💰 北向资金净流入 {north_market_flow:+.1f} 亿")

    print("📥 预热 Tushare 全市场缓存（北向/估值/涨停）…")
    dl.fetch_hk_hold_market(use_mock=dry_run)
    dl.fetch_daily_basic_market(use_mock=dry_run)
    dl.fetch_limit_list_market(use_mock=dry_run)

    print("📥 预热涨停池 + 龙虎榜缓存…")
    dl.fetch_zt_pool(use_mock=dry_run)
    dl.fetch_lhb_detail(use_mock=dry_run)

    print(f"📥 并发拉取日线 + 打分（{len(candidate_codes)} 只）…")
    scored = list(_score_candidates_concurrent(
        candidate_codes, spot_map, dry_run,
        north_market_flow=north_market_flow,
        turnover_map=turnover_map,
    ))

    streak_map = _compute_streak(scored)
    scored = _adjust_for_repeats(scored, streak_map)
    scored.sort(key=lambda x: x.total, reverse=True)

    deduped = dedup_by_industry(scored, max_per_industry=cfg["max_per_industry"])
    if len(deduped) < len(scored):
        print(f"   🧹 同行业去重：{len(scored)} → {len(deduped)} (上限 {cfg['max_per_industry']}/行业)")
    picks = deduped[:top_n]
    streak_map = _compute_streak(picks)

    return {
        "picks": picks,
        "industries": industries,
        "concept_ff": concept_ff,
        "industry_ff": industry_ff,
        "zt_pool": dl.fetch_zt_pool(use_mock=dry_run),
        "lhb_detail": dl.fetch_lhb_detail(use_mock=dry_run),
        "risk_score": risk_score,
        "risk_desc": risk_desc,
        "north_market_flow": north_market_flow,
        "streak_map": streak_map,
        "cfg": cfg,
        "spot_map": spot_map,
    }


# ---------- pick ----------


def cmd_pick(dry_run: bool = False, top_n: int = 5, force: bool = False) -> int:
    if not dry_run and not force and not dl.is_trading_day():
        print("⏸️  今日非交易日，跳过推送")
        return 0

    ctx = _run_full_pipeline(dry_run=dry_run, top_n=top_n)
    picks = ctx["picks"]
    risk_score = ctx["risk_score"]

    if risk_score <= 2:
        msg = f"⚠️ 今日大盘风险评分 {risk_score}/10：{ctx['risk_desc']}，跳过选股推送"
        print(f"   {msg}")
        if not dry_run:
            send_to_wechat(f"⏸️ 选股暂停 {datetime.now().strftime('%m-%d')}", f"# {msg}\n\n大盘风险过高，今日不推送候选。")
        return 0

    print(f"\n🏆 Top {len(picks)}:")
    for i, s in enumerate(picks, 1):
        print(f"   {i}. {s.code} {s.name} ({s.industry}) 综合 {s.total:.1f}")

    md = rpt.render_pick_report(
        ctx["industries"], picks, streak_map=ctx["streak_map"],
        risk_score=risk_score, risk_desc=ctx["risk_desc"],
        north_flow=ctx["north_market_flow"],
        concept_fundflow=ctx["concept_ff"],
        industry_fundflow=ctx["industry_ff"],
        zt_pool=ctx["zt_pool"],
        lhb_detail=ctx["lhb_detail"],
    )

    # 题材热度榜（独立模块，失败不影响主推送）
    try:
        theme_ranks = ts.rank_themes()
        theme_md = ts.render_theme_section(theme_ranks, top_n=10)
        if theme_md:
            md = md + "\n\n" + theme_md
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  题材热度榜计算失败: {e}")

    # ⭐ 涨停接力 + 热点领头：插到「主候选 Top」前，作为优先级最高的两栏
    priority_blocks = []
    try:
        zt_picks, zt_warns = ztr.rank_zt_relay(
            zt_pool=ctx["zt_pool"],
            concept_ff=ctx["concept_ff"],
            lhb_detail=ctx["lhb_detail"],
            top_n=5,
        )
        zt_md = ztr.render_zt_relay_section(zt_picks, zt_warns, label="今日")
        if zt_md:
            priority_blocks.append(zt_md)
            print(f"   ✅ 涨停接力候选 {len(zt_picks)} 只 + 高位预警 {len(zt_warns)} 只")
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  涨停接力计算失败: {e}")

    try:
        leaders = hl.rank_hot_leaders(
            concept_ff=ctx["concept_ff"],
            top_n=5,
            use_mock=dry_run,
        )
        leaders_md = hl.render_hot_leaders_section(leaders, label="今日")
        if leaders_md:
            priority_blocks.append(leaders_md)
            print(f"   ✅ 今日热点领头 {len(leaders)} 只")
        else:
            print(f"   ℹ️  今日热点领头：0 命中（概念 × 强势股交集为空）")
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"   ⚠️  今日热点领头计算失败: {e}")
        traceback.print_exc()

    if priority_blocks:
        # render_pick_report 的主候选段是 "## 🎯 候选个股"
        anchors = ["## 🎯 候选个股", "## 🎯"]
        inserted = False
        for a in anchors:
            if a in md:
                md = md.replace(a, "\n\n".join(priority_blocks) + "\n\n" + a, 1)
                inserted = True
                break
        if not inserted:
            md = md + "\n\n" + "\n\n".join(priority_blocks)

    if dry_run:
        print("\n" + "=" * 60)
        print(md)
        print("=" * 60)
        return 0

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


# ---------- evening (晚间深度复盘 + 明日候选) ----------


def cmd_evening(dry_run: bool = False, force: bool = False) -> int:
    """盘后 18:23 全数据已稳定时跑：

    内容包含：今日大盘 / 热门概念 / 行业资金流 / 涨停梯队 / 龙虎榜净买
    + 明日 Top 3 候选 (含买点止损)。

    与 daily-pick 的区别：
    - 时间：18:23 而非 8:27 → 数据是收盘后稳定版
    - 内容：明日候选 + 今日深度复盘合一
    - 不写 picks/ 文件（不参与回测，避免和盘前 picks 混淆）
    """
    if not dry_run and not force and not dl.is_trading_day():
        print("⏸️  今日非交易日，跳过晚间复盘")
        return 0

    # 凌晨触发守卫：GitHub schedule 经常拖到次日凌晨才执行（已观测 6/16 18:43
    # 主 cron 拖到 6/17 05:35），此时 datetime.now() 已跨日，再发"今日复盘"
    # 会出现标题日期与盘后数据(zt_pool/lhb 自动回退到上一交易日)脱钩。
    # 北京时间 < 16:00 视为延迟触发：直接跳过 + 发警告，--force 可越过。
    if not dry_run and not force:
        now_h = datetime.now().hour
        if now_h < 16:
            warn_title = "⚠️ 晚间复盘延迟触发，已跳过"
            warn_body = (
                f"# 晚间复盘任务在 {datetime.now().strftime('%Y-%m-%d %H:%M')} "
                f"被触发\n\n"
                "这是 GitHub Actions schedule 跨日延迟的兜底拒绝：当前已是次日凌晨，"
                "原本属于上一交易日的盘后数据若按 datetime.now() 渲染会写成"
                "次日日期，造成日期与数据脱钩。\n\n"
                "**未发送复盘报告**。如需补发，请手动 dispatch 时勾选 force。"
            )
            print(f"⏸️  {warn_title}")
            send_to_wechat(warn_title, warn_body)
            return 0

    ctx = _run_full_pipeline(dry_run=dry_run, top_n=3, override_max_picks=True)
    picks = ctx["picks"]

    if not picks:
        if not dry_run:
            send_to_wechat(
                f"⚠️ 晚间复盘 {datetime.now().strftime('%m-%d')}",
                "# 今日候选为空\n\n所有数据源均未返回有效候选，请检查 Actions 日志。",
            )
        return 1

    print(f"\n🏆 明日 Top {len(picks)}:")
    for i, s in enumerate(picks, 1):
        print(f"   {i}. {s.code} {s.name} ({s.industry}) 综合 {s.total:.1f}")

    md = rpt.render_evening_report(
        picks=picks,
        industries=ctx["industries"],
        concept_ff=ctx["concept_ff"],
        industry_ff=ctx["industry_ff"],
        zt_pool=ctx["zt_pool"],
        lhb_detail=ctx["lhb_detail"],
        risk_score=ctx["risk_score"],
        risk_desc=ctx["risk_desc"],
        north_flow=ctx["north_market_flow"],
        streak_map=ctx["streak_map"],
        anchor_date=dl.get_anchor_date(),
    )

    # 题材热度榜（独立模块，失败不影响主推送）
    try:
        theme_ranks = ts.rank_themes()
        theme_md = ts.render_theme_section(theme_ranks, top_n=10)
        if theme_md:
            md = md + "\n\n" + theme_md
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  题材热度榜计算失败: {e}")

    # ⭐ 涨停接力 + 热点领头：插到「明日 Top」前，作为优先级最高的两栏
    priority_blocks = []
    try:
        zt_picks, zt_warns = ztr.rank_zt_relay(
            zt_pool=ctx["zt_pool"],
            concept_ff=ctx["concept_ff"],
            lhb_detail=ctx["lhb_detail"],
            top_n=5,
        )
        zt_md = ztr.render_zt_relay_section(zt_picks, zt_warns, label="明日")
        if zt_md:
            priority_blocks.append(zt_md)
            print(f"   ✅ 涨停接力候选 {len(zt_picks)} 只 + 高位预警 {len(zt_warns)} 只")
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  涨停接力计算失败: {e}")

    try:
        leaders = hl.rank_hot_leaders(
            concept_ff=ctx["concept_ff"],
            top_n=5,
            use_mock=dry_run,
        )
        leaders_md = hl.render_hot_leaders_section(leaders, label="今日")
        if leaders_md:
            priority_blocks.append(leaders_md)
            print(f"   ✅ 今日热点领头 {len(leaders)} 只")
        else:
            print(f"   ℹ️  今日热点领头：0 命中（概念 × 强势股交集为空）")
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"   ⚠️  今日热点领头计算失败: {e}")
        traceback.print_exc()

    if priority_blocks:
        # render_evening_report 的「明日 Top N 候选」段是 "## 🎯 明日 Top"
        anchors = ["## 🎯 明日 Top", "## 🎯"]
        inserted = False
        for a in anchors:
            if a in md:
                md = md.replace(a, "\n\n".join(priority_blocks) + "\n\n" + a, 1)
                inserted = True
                break
        if not inserted:
            md = md + "\n\n" + "\n\n".join(priority_blocks)

    if dry_run:
        print("\n" + "=" * 60)
        print(md)
        print("=" * 60)
        return 0

    today = dl.get_anchor_date() or datetime.now().strftime("%Y-%m-%d")
    title = f"🌙 晚间复盘 {today}"
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


def _build_theme_pool() -> set[str]:
    """构建题材白名单池：themes.json 全部代码 ∪ 今日同花顺强势股 reason 命中题材 keyword 的代码。

    返回 set of 6 位代码字符串。失败/为空时返回 set()，调用方需做兜底。
    """
    pool: set[str] = set()

    # 1) themes.json 静态池
    themes = ts.load_themes()
    for t in themes:
        for c in t.get("codes", []):
            pool.add(str(c).zfill(6))

    # 2) 同花顺今日强势股里命中题材 keyword 的代码（动态扩充）
    try:
        strong_index = ts._build_strong_index()
        for t in themes:
            keywords = t.get("keywords") or []
            _, hits = ts._theme_hit_score(t["name"], strong_index, keywords=keywords)
            for code, _, _ in hits:
                pool.add(str(code).zfill(6))
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  动态扩充强势股池失败：{e}（仅用 themes.json 静态池）")

    return pool


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
    """并发拉取日线并打分。

    新增：腾讯行情批量预拉（量比/实时PE/实时换手）
          + 东财资金流批量预拉（主力净流入）
          + **技术信号**（ADX/布林带/RSI/OBV 三维投票）
          + **截面因子得分**（动量/波动率/量比 Z-score）
    从股票行情分析项目 + Iwencai 技能移入的直连数据源。
    """
    code_list = list(candidate_codes.keys())

    # 批量预拉腾讯实时行情（海外友好，不封IP）
    print("   📡 批量拉取腾讯实时行情（量比/PE/市值）…")
    tencent_enrich: dict = {}
    if not dry_run:
        try:
            tencent_enrich = dl.enrich_with_tencent(code_list)
            if tencent_enrich:
                print(f"      ✅ 腾讯行情 {len(tencent_enrich)} 只")
        except Exception:
            pass

    # 批量预拉东财资金流（主力净流入，节流防封）
    print("   💰 批量拉取东财资金流（主力净流入）…")
    fundflow_enrich: dict = {}
    if not dry_run:
        try:
            fundflow_enrich = dl.enrich_with_fundflow(code_list[:60])  # 只查前60只节省配额
            if fundflow_enrich:
                print(f"      ✅ 资金流 {len(fundflow_enrich)} 只")
        except Exception:
            pass

    # 预拉所有 K 线（用于技术信号 + 截面因子计算）
    print(f"   📥 预拉 {len(code_list)} 只 K 线（用于技术信号+截面因子）…")
    kline_map: dict[str, pd.DataFrame] = {}
    for code in code_list:
        try:
            kline_map[code] = dl.fetch_kline(code, days=80, use_mock=dry_run)
        except Exception:
            pass

    # 计算截面因子得分（动量/波动率/量比 Z-score 标准化）
    print("   📊 计算截面因子得分…")
    factor_zscore_map = compute_cross_sectional_factors(kline_map, code_list)
    print(f"      ✅ 截面因子计算完成")

    # 计算技术信号（ADX/布林带/RSI/OBV 三维投票）
    print("   📈 计算技术信号…")
    tech_signal_map: dict[str, dict] = {}
    for code in code_list:
        kline = kline_map.get(code)
        if kline is not None and not kline.empty:
            tech_signal_map[code] = compute_technical_score(kline)
    print(f"      ✅ 技术信号计算完成 ({len(tech_signal_map)} 只)")

    def _work(code: str, industry: str):
        try:
            kline = kline_map.get(code)
            if kline is None or kline.empty:
                return None
            north = dl.fetch_north_flow(code, use_mock=dry_run)
            to = turnover_map.get(code) if turnover_map else None
            factors = dl.get_stock_factors(code, use_mock=dry_run)
            signals = dl.get_stock_market_signals(code, use_mock=dry_run)

            # turnover 优先用 spot 当日值,缺失时回退 Tushare 上一交易日值
            if to is None and factors.get("turnover_rate") is not None:
                to = factors["turnover_rate"]
            # 再回退腾讯实时换手率
            if to is None and code in tencent_enrich:
                to = tencent_enrich[code].get("turnover_pct")

            # PE/PB 优先用腾讯实时值（比 Tushare daily_basic 更新鲜）
            pe = factors.get("pe_ttm")
            pb = factors.get("pb")
            if code in tencent_enrich:
                tq = tencent_enrich[code]
                if tq.get("pe_ttm", 0) > 0:
                    pe = tq["pe_ttm"]
                if tq.get("pb", 0) > 0:
                    pb = tq["pb"]

            s = score_one(
                code, spot_map[code], industry, kline, north,
                north_market_flow=north_market_flow,
                turnover_rate=to,
                zt_streak=signals.get("zt_streak", 0),
                limit_times_10d=factors.get("limit_times_10d", 0) or 0,
                pe_ttm=pe,
                pb=pb,
                lhb_net_buy=signals.get("lhb_net_buy"),
                roe=None,             # 需 2000 积分,暂中性
                profit_growth=None,   # 需 2000 积分,暂中性
                tech_signal=tech_signal_map.get(code),       # 新增：技术信号
                factor_zscore=factor_zscore_map.get(code),   # 新增：截面因子 Z-score
            )
            # 将资金流数据挂到 Score 对象上供报告使用
            if s and code in fundflow_enrich:
                s.fund_flow = fundflow_enrich[code]
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

    p_rev = sub.add_parser("review", help="盘后复盘（对比候选当日表现）")
    p_rev.add_argument("--dry-run", action="store_true")

    p_eve = sub.add_parser("evening", help="晚间深度复盘 + 明日 Top 3 候选")
    p_eve.add_argument("--dry-run", action="store_true")
    p_eve.add_argument("--force", action="store_true", help="非交易日也强制运行")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "pick":
            return cmd_pick(dry_run=args.dry_run, top_n=args.top, force=args.force)
        if args.cmd == "review":
            return cmd_review(dry_run=args.dry_run)
        if args.cmd == "evening":
            return cmd_evening(dry_run=args.dry_run, force=args.force)
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
