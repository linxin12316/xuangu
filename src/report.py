"""生成 Markdown 报告。"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

import pandas as pd

from .scoring import Score


DISCLAIMER = (
    "> ⚠️ **风险提示**：候选股仅供研究，不构成投资建议。\n"
    "> 量化策略胜率上限约 55%，请严格执行止损，单只仓位不超过总资金 20%。\n"
    "> 投资有风险，亏损自负。\n"
)


def render_pick_report(
    industries: pd.DataFrame,
    picks: Sequence[Score],
    streak_map: dict[str, int] | None = None,
    risk_score: int | None = None,
    risk_desc: str | None = None,
    north_flow: float | None = None,
    concept_fundflow: pd.DataFrame | None = None,
    industry_fundflow: pd.DataFrame | None = None,
    zt_pool: pd.DataFrame | None = None,
    lhb_detail: pd.DataFrame | None = None,
) -> str:
    streak_map = streak_map or {}
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 📈 选股报告 · {today}", "", DISCLAIMER, ""]

    # 大盘风险提示
    if risk_score is not None and risk_score <= 4:
        icon = "🟡" if risk_score >= 3 else "🔴"
        lines.append(f"> {icon} **大盘风险**：评级 {risk_score}/10 — {risk_desc or ''}")
        lines.append("")

    if industries.empty:
        lines.append("## 🔥 强势板块")
        lines.append("")
        lines.append("> ⚠️ 板块接口在海外机房不可用，已降级为全市场涨幅排序。")
        lines.append("")
    else:
        lines.append("## 🔥 强势板块 Top 5（近 5 日涨幅）")
        lines.append("")
        lines.append("| 排名 | 板块 | 近5日涨幅 |")
        lines.append("| --- | --- | --- |")
        for i, row in industries.iterrows():
            lines.append(f"| {i+1} | {row['板块名称']} | {row['近5日涨幅']:.2f}% |")
        lines.append("")

    if north_flow is not None:
        lines.append(f"**全市场北向资金**：{north_flow:+.1f} 亿")
        lines.append("")

    lines.append("## 🎯 候选个股（按综合分排序）")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 板块 | 综合 | 趋势 | 量能 | 动量 | 资金 | 安全 | 换手 | 涨停 | 估值 | 龙虎 | 财务 | 技术 | 因子 | 现价 | 止损 | 标记 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, s in enumerate(picks, 1):
        d = s.as_dict()
        streak = streak_map.get(s.code, 0)
        tag = f"🔁 连{streak}日" if streak >= 2 else ""
        lines.append(
            f"| {i} | {d['code']} | {d['name']} | {d['industry']} | "
            f"**{d['total']}** | {d['trend']} | {d['volume']} | {d['momentum']} | "
            f"{d['fund']} | {d['safety']} | {d['turnover']} | {d['limit_up']} | "
            f"{d['valuation']} | {d['longhu']} | {d['finance']} | "
            f"{d['technical_signal']} | {d['factor_score']} | "
            f"{d['last_close']} | {d['suggested_stop_loss']} | {tag} |"
        )
    lines.append("")

    lines.append("*总分满分 100（趋势18+量能14+动量10+资金8+安全6+换手4+涨停8+估值8+龙虎4+财务4+技术信号10+因子得分6）*")
    lines.append("**技术信号**=ADX趋势+布林带+RSI+OBV三维投票 | **因子得分**=截面Z-score相对排名")
    lines.append("*财务因子需 Tushare 2000 积分接口，当前免费版给中性 2.5 分*")
    lines.append("")

    # ---- 新模块: 今日热门概念 ----
    if concept_fundflow is not None and not concept_fundflow.empty:
        lines.append("## 💡 今日热门概念（资金净流入 Top 8）")
        lines.append("")
        lines.append("| 概念 | 涨幅 | 净流入(亿) | 领涨股 | 领涨股涨幅 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for _, r in concept_fundflow.head(8).iterrows():
            try:
                lines.append(
                    f"| {r['行业']} | {float(r.get('行业-涨跌幅', 0)):.2f}% | "
                    f"{float(r.get('净额', 0)):.2f} | {r.get('领涨股', '-')} | "
                    f"{float(r.get('领涨股-涨跌幅', 0)):.2f}% |"
                )
            except (ValueError, TypeError):
                continue
        lines.append("")

    # ---- 新模块: 行业资金流（候选池主力来源）----
    if industry_fundflow is not None and not industry_fundflow.empty:
        lines.append("## 💰 主力行业流向（资金净流入 Top 6）")
        lines.append("")
        lines.append("| 行业 | 涨幅 | 净流入(亿) | 领涨股 |")
        lines.append("| --- | --- | --- | --- |")
        for _, r in industry_fundflow.head(6).iterrows():
            try:
                lines.append(
                    f"| {r['行业']} | {float(r.get('行业-涨跌幅', 0)):.2f}% | "
                    f"{float(r.get('净额', 0)):.2f} | {r.get('领涨股', '-')} |"
                )
            except (ValueError, TypeError):
                continue
        lines.append("")

    # ---- 新模块: 涨停梯队 ----
    if zt_pool is not None and not zt_pool.empty:
        try:
            zt_pool_sorted = zt_pool.copy()
            zt_pool_sorted["连板数"] = pd.to_numeric(zt_pool_sorted.get("连板数", 0), errors="coerce").fillna(0).astype(int)
            high = zt_pool_sorted[zt_pool_sorted["连板数"] >= 2].sort_values("连板数", ascending=False)
            if not high.empty:
                lines.append(f"## 🚀 昨日连板梯队（≥2连，共 {len(high)} 只）")
                lines.append("")
                lines.append("| 代码 | 名称 | 连板 | 行业 | 换手率 |")
                lines.append("| --- | --- | --- | --- | --- |")
                for _, r in high.head(15).iterrows():
                    try:
                        lines.append(
                            f"| {r['代码']} | {r['名称']} | **{int(r['连板数'])}板** | "
                            f"{r.get('所属行业', '-')} | {float(r.get('换手率', 0)):.2f}% |"
                        )
                    except (ValueError, TypeError):
                        continue
                lines.append("")
        except Exception:
            pass

    # ---- 新模块: 龙虎榜净买 Top 10 ----
    if lhb_detail is not None and not lhb_detail.empty:
        try:
            lhb_sorted = lhb_detail.copy()
            lhb_sorted["龙虎榜净买额"] = pd.to_numeric(
                lhb_sorted.get("龙虎榜净买额", 0), errors="coerce"
            ).fillna(0)
            # 按代码聚合（同股可能因多个原因上榜多次）
            agg = lhb_sorted.groupby(["代码", "名称"], as_index=False).agg(
                净买额=("龙虎榜净买额", "sum"),
                解读=("解读", "first"),
            )
            agg = agg.sort_values("净买额", ascending=False).head(10)
            if not agg.empty and agg.iloc[0]["净买额"] > 0:
                lines.append("## 🐯 昨日龙虎榜净买 Top 10")
                lines.append("")
                lines.append("| 代码 | 名称 | 净买(万) | 解读 |")
                lines.append("| --- | --- | --- | --- |")
                for _, r in agg.iterrows():
                    nb_wan = r["净买额"] / 1e4
                    desc = str(r.get("解读", "-"))[:25]
                    lines.append(f"| {r['代码']} | {r['名称']} | {nb_wan:+.0f} | {desc} |")
                lines.append("")
        except Exception:
            pass

    lines.append("## 📋 操作建议")
    lines.append("")
    lines.append("- 开盘后观察竞价是否高开 < 3%，避免追高")
    lines.append("- 跌破建议止损价立即离场，不抗单")
    lines.append("- 综合分 70 以下的标的优先级降低")
    lines.append("- 同一板块最多同时持有 2 只，避免过度集中")
    lines.append("")
    lines.append(f"*数据来源：腾讯财经(实时行情/量比) + 同花顺(热点/题材) + 东财(资金流/龙虎榜) + Tushare(K线/北向/估值) + akshare(涨停池) *  \n*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def render_review_report(
    pick_date: str,
    picks: list[dict],
    today_quotes: dict[str, dict],
    market_summary: dict | None = None,
) -> str:
    """复盘报告。

    picks: 当日推送的候选股 dict 列表（来自 picks/YYYY-MM-DD.json）
    today_quotes: {code: {"name":..., "close": float, "chg_pct": float}}
    market_summary: 大盘信息字典
    """
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 🔍 选股复盘 · {today}", "", DISCLAIMER, ""]

    if market_summary:
        lines.append("## 📊 今日大盘")
        lines.append("")
        for k, v in market_summary.items():
            lines.append(f"- **{k}**：{v}")
        lines.append("")

    lines.append(f"## 🎯 {pick_date} 候选个股表现")
    lines.append("")
    lines.append("| 代码 | 名称 | 推送综合分 | 推送时收盘 | 今日收盘 | 涨跌幅 | 是否触止损 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    win = 0
    total = 0
    for p in picks:
        code = p["code"]
        name = p.get("name", "?")
        score = p.get("total", 0)
        old_close = p.get("last_close", 0)
        stop = p.get("suggested_stop_loss", 0)
        q = today_quotes.get(code)
        if q is None:
            lines.append(f"| {code} | {name} | {score} | {old_close} | - | (无数据) | - |")
            continue
        new_close = q["close"]
        chg = q["chg_pct"]
        if chg > 0:
            win += 1
        total += 1
        broke = "❌ 是" if new_close <= stop else "✅ 否"
        emoji = "🟢" if chg > 0 else ("🔴" if chg < 0 else "⚪")
        lines.append(
            f"| {code} | {name} | {score} | {old_close} | {new_close} | {emoji} {chg:+.2f}% | {broke} |"
        )

    lines.append("")
    if total > 0:
        lines.append(f"**当日命中率：{win}/{total} = {win/total*100:.1f}%**")
    else:
        lines.append("**当日无可对比的候选数据**")

    lines.append("")
    lines.append(f"*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


# ---------- 晚间复盘 (18:23) ----------


def render_evening_report(
    picks: Sequence[Score],
    industries: pd.DataFrame | None = None,
    concept_ff: pd.DataFrame | None = None,
    industry_ff: pd.DataFrame | None = None,
    zt_pool: pd.DataFrame | None = None,
    lhb_detail: pd.DataFrame | None = None,
    risk_score: int | None = None,
    risk_desc: str | None = None,
    north_flow: float | None = None,
    streak_map: dict[str, int] | None = None,
    anchor_date: str | None = None,
) -> str:
    """晚间深度复盘：今日盘面总结 + 明日 Top 3 候选（含买点/止损）。

    anchor_date: 数据所属交易日 (YYYY-MM-DD)。cron 延迟到次日凌晨触发时，
    此处传 zt_pool/lhb_detail 实际拉到的日期，避免标题写"今天"但内容是
    "昨天"的脱钩。None 时回退到 datetime.now()（与原行为一致）。
    """
    streak_map = streak_map or {}
    today = anchor_date or datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 🌙 晚间复盘 · {today}", "", DISCLAIMER, ""]

    # ---- 今日大盘 ----
    lines.append("## 📊 今日盘面")
    lines.append("")
    if risk_score is not None:
        if risk_score >= 7:
            icon = "🟢"
        elif risk_score >= 5:
            icon = "🟡"
        else:
            icon = "🔴"
        lines.append(f"- **大盘风险评分**：{icon} {risk_score}/10 — {risk_desc or ''}")
    if north_flow is not None:
        lines.append(f"- **北向资金净流入**：{north_flow:+.1f} 亿")
    lines.append("")

    # ---- 今日热门概念 Top 8 ----
    if concept_ff is not None and not concept_ff.empty:
        lines.append("## 💡 今日热门概念（资金净流入 Top 8）")
        lines.append("")
        lines.append("| 概念 | 涨幅 | 净流入(亿) | 领涨股 | 领涨涨幅 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for _, r in concept_ff.head(8).iterrows():
            try:
                lines.append(
                    f"| {r['行业']} | {float(r.get('行业-涨跌幅', 0)):.2f}% | "
                    f"{float(r.get('净额', 0)):.1f} | {r.get('领涨股', '-')} | "
                    f"{float(r.get('领涨股-涨跌幅', 0)):.2f}% |"
                )
            except (ValueError, TypeError):
                continue
        lines.append("")

    # ---- 主力行业流向 Top 6 ----
    if industry_ff is not None and not industry_ff.empty:
        lines.append("## 💰 主力行业流向（资金净流入 Top 6）")
        lines.append("")
        lines.append("| 行业 | 涨幅 | 净流入(亿) | 领涨股 |")
        lines.append("| --- | --- | --- | --- |")
        for _, r in industry_ff.head(6).iterrows():
            try:
                lines.append(
                    f"| {r['行业']} | {float(r.get('行业-涨跌幅', 0)):.2f}% | "
                    f"{float(r.get('净额', 0)):.1f} | {r.get('领涨股', '-')} |"
                )
            except (ValueError, TypeError):
                continue
        lines.append("")

    # ---- 今日连板梯队 ----
    if zt_pool is not None and not zt_pool.empty:
        try:
            zp = zt_pool.copy()
            zp["连板数"] = pd.to_numeric(zp.get("连板数", 0), errors="coerce").fillna(0).astype(int)
            high = zp[zp["连板数"] >= 2].sort_values("连板数", ascending=False)
            if not high.empty:
                lines.append(f"## 🚀 今日连板梯队（≥2连，共 {len(high)} 只）")
                lines.append("")
                lines.append("| 代码 | 名称 | 连板 | 行业 | 换手率 |")
                lines.append("| --- | --- | --- | --- | --- |")
                for _, r in high.head(15).iterrows():
                    try:
                        lines.append(
                            f"| {r['代码']} | {r['名称']} | **{int(r['连板数'])}板** | "
                            f"{r.get('所属行业', '-')} | {float(r.get('换手率', 0)):.2f}% |"
                        )
                    except (ValueError, TypeError):
                        continue
                lines.append("")
        except Exception:
            pass

    # ---- 今日龙虎榜净买 Top 10 ----
    if lhb_detail is not None and not lhb_detail.empty:
        try:
            l = lhb_detail.copy()
            l["龙虎榜净买额"] = pd.to_numeric(l.get("龙虎榜净买额", 0), errors="coerce").fillna(0)
            agg = l.groupby(["代码", "名称"], as_index=False).agg(
                净买额=("龙虎榜净买额", "sum"), 解读=("解读", "first"),
            )
            agg = agg.sort_values("净买额", ascending=False).head(10)
            if not agg.empty and agg.iloc[0]["净买额"] > 0:
                lines.append("## 🐯 今日龙虎榜净买 Top 10")
                lines.append("")
                lines.append("| 代码 | 名称 | 净买(万) | 解读 |")
                lines.append("| --- | --- | --- | --- |")
                for _, r in agg.iterrows():
                    nb_wan = r["净买额"] / 1e4
                    desc = str(r.get("解读", "-"))[:25]
                    lines.append(f"| {r['代码']} | {r['名称']} | {nb_wan:+.0f} | {desc} |")
                lines.append("")
        except Exception:
            pass

    # ---- 明日 Top N 候选（含买点/止损建议）----
    if picks:
        lines.append(f"## 🎯 明日 Top {len(picks)} 候选（含买点 / 止损）")
        lines.append("")
        for i, s in enumerate(picks, 1):
            d = s.as_dict()
            streak = streak_map.get(s.code, 0)
            tag = f" 🔁连{streak}日" if streak >= 2 else ""
            entry_price = d['last_close']
            stop = d['suggested_stop_loss']
            stop_pct = (stop - entry_price) / entry_price * 100
            # 买点：低开/平开 ≤2% → 直接买; 高开 2-5% → 等回踩 5 分钟均线; >5% → 不追
            buy_low = entry_price
            buy_high = entry_price * 1.02
            lines.append(f"### #{i} {d['name']} ({d['code']}) · {d['industry']}{tag}")
            lines.append("")
            lines.append(f"- **综合分**：**{d['total']}** （趋势{d['trend']} 量能{d['volume']} 动量{d['momentum']} 资金{d['fund']} 安全{d['safety']} 换手{d['turnover']} 涨停{d['limit_up']} 估值{d['valuation']} 龙虎{d['longhu']} 财务{d['finance']} 技术{d['technical_signal']} 因子{d['factor_score']}）")
            lines.append(f"- **今日收盘**：{entry_price}")
            lines.append(f"- **明日买点**：≤{buy_high:.2f}（高开 ≤2%）→ 直接买；高开 2-5% → 等回踩 5 分钟均线再买；**高开 >5% 不追**")
            lines.append(f"- **止损位**：{stop}（约 {stop_pct:+.1f}%，跌破立即离场）")
            lines.append("")

    # ---- 风险声明 ----
    lines.append("## ⚠️ 注意事项")
    lines.append("")
    lines.append("- 候选基于今日收盘后数据，**明日盘前可能出现新消息面**，请结合早盘新闻判断")
    lines.append("- 若大盘明天跳空低开 >1%，**所有候选先观察不要买**")
    lines.append("- 单只仓位不超过总资金 20%，跌破止损立刻走")
    lines.append("- 同板块最多持有 2 只，避免风险过度集中")
    lines.append("")
    lines.append(f"*数据来源：腾讯财经(实时行情/量比) + 同花顺(热点/题材) + 东财(资金流/龙虎榜) + Tushare(K线/北向/估值) + akshare(涨停池) *  ")
    lines.append(f"*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)
