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
) -> str:
    streak_map = streak_map or {}
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 📈 选股报告 · {today}", "", DISCLAIMER, ""]

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

    lines.append("## 🎯 候选个股（按综合分排序）")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 板块 | 综合分 | 趋势 | 量能 | 动量 | 资金 | 安全 | 现价 | 建议止损 | 标记 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, s in enumerate(picks, 1):
        d = s.as_dict()
        streak = streak_map.get(s.code, 0)
        tag = f"🔁 连续上榜{streak}日" if streak >= 2 else ""
        lines.append(
            f"| {i} | {d['code']} | {d['name']} | {d['industry']} | "
            f"**{d['total']}** | {d['trend']} | {d['volume']} | {d['momentum']} | "
            f"{d['fund']} | {d['safety']} | {d['last_close']} | "
            f"{d['suggested_stop_loss']} | {tag} |"
        )
    lines.append("")

    lines.append("## 📋 操作建议")
    lines.append("")
    lines.append("- 开盘后观察竞价是否高开 < 3%，避免追高")
    lines.append("- 跌破建议止损价立即离场，不抗单")
    lines.append("- 综合分 70 以下的标的优先级降低")
    lines.append("- 同一板块最多同时持有 2 只，避免过度集中")
    lines.append("")
    lines.append(f"*数据来源：东方财富 / 同花顺（akshare）*  \n*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

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
