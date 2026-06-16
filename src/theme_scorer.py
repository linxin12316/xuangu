"""题材热度榜 — 与现有十维打分独立的题材层评估。

数据源：腾讯行情（量比 / 实时涨幅）+ Tushare daily（5 日涨幅）
打分（百分制）：
  - RPS 强度 40：5 日涨幅在题材内排名（题材中位数 vs 全部题材）
  - 量能 30   ：当日量比（>1.5 满分，>1.0 给 60 分）
  - 题材热度 30：题材内全部成员当日平均涨幅

输出：
  - rank_themes(): 返回 [(theme_name, score, leader_code, leader_quote, members)]，按分排序
  - render_theme_section(): 渲染成 Markdown，可直接拼到现有报告里

不依赖 scoring.py，不污染 picks 流程，可被 cmd_pick / cmd_evening 复用。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import data_loader as dl


THEMES_PATH = Path(__file__).resolve().parent.parent / "themes.json"


def load_themes() -> list[dict]:
    """读取 themes.json。文件缺失或解析失败时返回空列表（不阻塞主流程）。"""
    if not THEMES_PATH.exists():
        return []
    try:
        data = json.loads(THEMES_PATH.read_text(encoding="utf-8"))
        return list(data.get("themes") or [])
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  themes.json 解析失败: {e}")
        return []


def _calc_5d_chg(code: str) -> Optional[float]:
    """单只股票近 5 日涨幅 %。失败返回 None。"""
    try:
        df = dl.fetch_kline(code, days=10)
        if df is None or len(df) < 6:
            return None
        close = pd.to_numeric(df["收盘"], errors="coerce").dropna()
        if len(close) < 6:
            return None
        return (close.iloc[-1] / close.iloc[-6] - 1) * 100
    except Exception:
        return None


def rank_themes(use_mock: bool = False) -> list[dict]:
    """对所有题材打分排序。

    返回:
      [
        {
          "name": "AI 液冷",
          "desc": "...",
          "score": 78.3,
          "rps_score": 35,
          "vol_score": 28,
          "heat_score": 25,
          "avg_chg_today": 2.34,    # 题材成员当日平均涨幅
          "avg_chg_5d": 8.2,        # 题材成员 5 日平均涨幅
          "leader": {"code": "002837", "name": "英维克", "price": 25.6,
                     "change_pct": 3.2, "vol_ratio": 1.8, "pe_ttm": 35.2},
          "members": [
              {"code": "002837", "name": "英维克", "change_pct": 3.2,
               "chg_5d": 8.2, "vol_ratio": 1.8, "pe_ttm": 35.2}
          ]
        },
        ...
      ]
    """
    themes = load_themes()
    if not themes or use_mock:
        return []

    # 1) 一次性拉所有标的实时行情
    all_codes: list[str] = []
    for t in themes:
        all_codes.extend(t.get("codes", []))
    all_codes = list(dict.fromkeys(all_codes))  # 去重保序

    quotes = dl.get_tencent_quotes(all_codes) if all_codes else {}

    # 2) 逐题材计算
    results = []
    for t in themes:
        codes = t.get("codes", [])
        members = []
        for c in codes:
            q = quotes.get(c) or {}
            chg_5d = _calc_5d_chg(c)
            members.append({
                "code": c,
                "name": q.get("name", c),
                "price": q.get("price", 0),
                "change_pct": q.get("change_pct", 0),
                "vol_ratio": q.get("vol_ratio", 0),
                "pe_ttm": q.get("pe_ttm", 0),
                "chg_5d": chg_5d if chg_5d is not None else 0,
                "amount_wan": q.get("amount_wan", 0),
            })

        if not members:
            continue

        avg_today = sum(m["change_pct"] for m in members) / len(members)
        chg_5d_vals = [m["chg_5d"] for m in members if m["chg_5d"] != 0]
        avg_5d = sum(chg_5d_vals) / len(chg_5d_vals) if chg_5d_vals else 0
        vol_ratios = [m["vol_ratio"] for m in members if m["vol_ratio"] > 0]
        avg_vol_ratio = sum(vol_ratios) / len(vol_ratios) if vol_ratios else 0

        # 打分
        # RPS 强度 40：基于 5 日平均涨幅，> 10% 满分，0% 给 0 分，线性
        rps_score = max(0, min(40, avg_5d / 10 * 40))
        # 量能 30：量比 1.0 给 18，1.5+ 给 30，0.5 以下给 0
        if avg_vol_ratio >= 1.5:
            vol_score = 30
        elif avg_vol_ratio >= 1.0:
            vol_score = 18 + (avg_vol_ratio - 1.0) * 24
        elif avg_vol_ratio >= 0.5:
            vol_score = (avg_vol_ratio - 0.5) * 36
        else:
            vol_score = 0
        # 热度 30：当日平均涨幅，3% 满分，0% 给 10，-3% 给 0
        heat_score = max(0, min(30, (avg_today + 3) / 6 * 30))

        total = rps_score + vol_score + heat_score

        # 龙头：优先用 themes.json 标注的 leader，否则取当日涨幅最高
        leader_code = t.get("leader") or members[0]["code"]
        leader = next((m for m in members if m["code"] == leader_code), members[0])
        # 如果指定 leader 当日跌幅明显，改用涨得最好的
        if leader["change_pct"] < 0:
            top = max(members, key=lambda m: m["change_pct"])
            if top["change_pct"] > leader["change_pct"] + 2:
                leader = top

        results.append({
            "name": t["name"],
            "desc": t.get("desc", ""),
            "score": round(total, 1),
            "rps_score": round(rps_score, 1),
            "vol_score": round(vol_score, 1),
            "heat_score": round(heat_score, 1),
            "avg_chg_today": round(avg_today, 2),
            "avg_chg_5d": round(avg_5d, 2),
            "avg_vol_ratio": round(avg_vol_ratio, 2),
            "leader": leader,
            "members": members,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def render_theme_section(theme_ranks: list[dict], top_n: int = 10) -> str:
    """渲染题材热度榜成 Markdown。空 → 空字符串（调用方拼接时无副作用）。"""
    if not theme_ranks:
        return ""

    lines = []
    lines.append(f"## 📌 题材热度榜 Top {min(top_n, len(theme_ranks))}")
    lines.append("")
    lines.append("> 三因子打分（满分 100）：5 日 RPS 40 / 量能 30 / 当日热度 30")
    lines.append("")
    lines.append("| # | 题材 | 综合分 | 5日均涨 | 今日均涨 | 量比 | 主板龙头 | 龙头涨幅 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, t in enumerate(theme_ranks[:top_n], 1):
        ldr = t["leader"]
        lines.append(
            f"| {i} | **{t['name']}** | **{t['score']}** | "
            f"{t['avg_chg_5d']:+.2f}% | {t['avg_chg_today']:+.2f}% | "
            f"{t['avg_vol_ratio']:.2f} | {ldr['name']}({ldr['code']}) | "
            f"{ldr['change_pct']:+.2f}% |"
        )
    lines.append("")

    # 前 3 题材展开成员表
    lines.append("### 🥇 Top 3 题材成员明细")
    lines.append("")
    for t in theme_ranks[:3]:
        lines.append(f"**{t['name']}** — {t['desc']}")
        lines.append("")
        lines.append("| 代码 | 名称 | 现价 | 今日涨幅 | 5日涨幅 | 量比 | PE-TTM |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for m in sorted(t["members"], key=lambda x: x["chg_5d"], reverse=True):
            pe = m["pe_ttm"]
            pe_str = f"{pe:.1f}" if pe and pe > 0 else "亏损"
            lines.append(
                f"| {m['code']} | {m['name']} | {m['price']:.2f} | "
                f"{m['change_pct']:+.2f}% | {m['chg_5d']:+.2f}% | "
                f"{m['vol_ratio']:.2f} | {pe_str} |"
            )
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    # 本地调试：python3 -m src.theme_scorer
    ranks = rank_themes()
    print(f"共 {len(ranks)} 个题材")
    for r in ranks[:5]:
        print(f"  {r['name']:12s} 综合 {r['score']:5.1f}  5日 {r['avg_chg_5d']:+.2f}%  今日 {r['avg_chg_today']:+.2f}%  龙头 {r['leader']['name']}")
    print()
    print(render_theme_section(ranks))
