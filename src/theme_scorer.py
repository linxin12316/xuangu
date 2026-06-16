"""题材热度榜 — 与现有十维打分独立的题材层评估。

数据源：腾讯行情（量比 / 实时涨幅）+ Tushare daily（5 日涨幅）+ 同花顺强势股归因（验证层）
打分（百分制）：
  - RPS 强度 32：5 日涨幅在题材内排名（题材中位数 vs 全部题材）
  - 量能 24    ：当日量比（>1.5 满分，>1.0 给 60%）
  - 题材热度 24：题材内全部成员当日平均涨幅
  - 强势股命中 20：题材名出现在同花顺今日强势股 reason 中的次数（市场真实验证）

输出：
  - rank_themes(): 返回排序的题材列表
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
    """单只股票近 5 日涨幅 %。失败返回 None。

    优先复用主流程的 market_window 缓存（Tushare 一次性全市场，0 额外开销）。
    缓存未命中时单只 fetch_kline 兜底。
    """
    # 通道 1: 复用 main pipeline 已经拉过的全市场 5 日涨幅缓存
    try:
        window = dl.fetch_market_window(days=6, use_mock=False)
        if window is not None and not window.empty:
            row = window[window["code"] == str(code).zfill(6)]
            if not row.empty:
                v = row.iloc[0].get("chg_5d")
                if pd.notna(v):
                    return float(v)
    except Exception:
        pass

    # 通道 2: 单只 K 线
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


def _build_strong_index() -> dict:
    """用同花顺强势股 reason 字段建立 {题材短语: [(code, name), ...]} 索引。

    剔除黑名单（300/301/688/8/4/92/ST/退）。
    返回:
      {
        'AI算力': [('600183', '生益科技'), ...],
        '覆铜板': [...],
      }
    主流程失败时返回 {}（不阻塞）。
    """
    try:
        rows, _, err = dl.get_hot_stocks()
        if err or not rows:
            return {}
    except Exception:
        return {}

    index = {}
    for r in rows:
        code = str(r.get("code") or "").zfill(6)
        name = r.get("name") or ""
        # 过滤黑名单
        if code.startswith(("300", "301", "688", "8", "4", "92")):
            continue
        if "ST" in name or "退" in name:
            continue
        reason = r.get("reason") or ""
        # 题材分隔符：+ / 中文加号 / 顿号
        for token in reason.replace("、", "+").split("+"):
            tm = token.strip()
            if tm:
                index.setdefault(tm, []).append((code, name))
    return index


def _theme_hit_score(theme_name: str, strong_index: dict, keywords: list = None) -> tuple[int, list]:
    """题材命中强势股的次数 + 命中股列表。

    匹配规则（防误匹配）：
      - 用题材关键词（keywords 优先，否则用题材名清理后的字符串）
      - 关键词必须出现在 reason token 里（即 token 包含 keyword，不允许反向）
      - 单字关键词必须严格相等
    例：keyword='AI算力电源' 能命中 token='AI算力电源'（精确）但不会命中 token='AI算力'。
    """
    if not strong_index:
        return 0, []

    if keywords:
        match_terms = [k.strip() for k in keywords if k and k.strip()]
    else:
        theme_clean = theme_name.replace(" ", "").replace("（", "(").replace("）", ")")
        if "(" in theme_clean:
            theme_clean = theme_clean.split("(")[0]
        match_terms = [theme_clean]

    hits = []
    seen_codes = set()
    for term in match_terms:
        for token, stocks in strong_index.items():
            token_clean = token.replace(" ", "")
            if len(term) == 1:
                matched = token_clean == term
            else:
                # term 必须是 token 的子串（题材词出现在强势股 reason 里）
                matched = term in token_clean
            if matched:
                for code, name in stocks:
                    if code not in seen_codes:
                        hits.append((code, name, token))
                        seen_codes.add(code)
    return len(hits), hits


def rank_themes(use_mock: bool = False) -> list[dict]:
    """对所有题材打分排序。

    新版打分（满分 100）：
      - RPS 强度 32：5 日涨幅
      - 量能 24    ：量比
      - 当日热度 24：成员平均涨幅
      - 强势股命中 20：题材在同花顺强势股 reason 出现次数

    返回每题材含 hits 字段（命中的强势股列表）。
    """
    themes = load_themes()
    if not themes or use_mock:
        return []

    # 一次性拉所有标的实时行情
    all_codes: list[str] = []
    for t in themes:
        all_codes.extend(t.get("codes", []))
    all_codes = list(dict.fromkeys(all_codes))

    quotes = dl.get_tencent_quotes(all_codes) if all_codes else {}

    # 拉今日强势股，建索引（一次拉取，所有题材复用）
    strong_index = _build_strong_index()
    if strong_index:
        print(f"   ✅ 同花顺强势股索引：{sum(len(v) for v in strong_index.values())} 只股票，"
              f"{len(strong_index)} 个题材短语")
    else:
        print(f"   ⚠️  同花顺强势股索引为空（接口失败或非交易日），强势股命中分恒为 0")

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

        # 打分（满分 100）
        # 1) RPS 强度 32：5 日涨幅 ≥ 10% 满分
        rps_score = max(0, min(32, avg_5d / 10 * 32))

        # 2) 量能 24：量比 ≥ 1.5 满分
        if avg_vol_ratio >= 1.5:
            vol_score = 24
        elif avg_vol_ratio >= 1.0:
            vol_score = 14.4 + (avg_vol_ratio - 1.0) * 19.2
        elif avg_vol_ratio >= 0.5:
            vol_score = (avg_vol_ratio - 0.5) * 28.8
        else:
            vol_score = 0

        # 3) 当日热度 24：均涨幅 3% 给满分
        heat_score = max(0, min(24, (avg_today + 3) / 6 * 24))

        # 4) 强势股命中 20：每命中一只 +5 分（4 只封顶 20）
        keywords = t.get("keywords")
        hit_count, hit_list = _theme_hit_score(t["name"], strong_index, keywords=keywords)
        hit_score = min(20, hit_count * 5)

        total = rps_score + vol_score + heat_score + hit_score

        # 龙头：优先 themes.json 标注的，跌幅明显时改用涨得最好的
        leader_code = t.get("leader") or members[0]["code"]
        leader = next((m for m in members if m["code"] == leader_code), members[0])
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
            "hit_score": hit_score,
            "hit_count": hit_count,
            "hits": hit_list,
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
    lines.append("> 四因子打分（满分 100）：5 日 RPS 32 / 量能 24 / 当日热度 24 / 强势股命中 20")
    lines.append("")
    lines.append("| # | 题材 | 综合分 | 5日均涨 | 今日均涨 | 量比 | 强势股 | 主板龙头 | 龙头涨幅 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, t in enumerate(theme_ranks[:top_n], 1):
        ldr = t["leader"]
        hit_str = f"🔥{t['hit_count']}" if t.get("hit_count", 0) > 0 else "-"
        lines.append(
            f"| {i} | **{t['name']}** | **{t['score']}** | "
            f"{t['avg_chg_5d']:+.2f}% | {t['avg_chg_today']:+.2f}% | "
            f"{t['avg_vol_ratio']:.2f} | {hit_str} | {ldr['name']}({ldr['code']}) | "
            f"{ldr['change_pct']:+.2f}% |"
        )
    lines.append("")

    # 命中过强势股的题材，单独列出强势股名单
    hit_themes = [t for t in theme_ranks[:top_n] if t.get("hit_count", 0) > 0]
    if hit_themes:
        lines.append("### 🔥 今日强势股命中明细（题材市场真实验证）")
        lines.append("")
        for t in hit_themes:
            stocks_str = "、".join(
                f"{name}({code})" for code, name, _ in t["hits"][:6]
            )
            extra = f" 等 {len(t['hits'])} 只" if len(t["hits"]) > 6 else ""
            lines.append(f"- **{t['name']}** ({t['hit_count']} 只): {stocks_str}{extra}")
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
