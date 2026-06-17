"""涨停接力候选 (Zhang-Ting Relay Picker)。

针对"昨日涨停 → 今日继续板"的接力交易场景，从 zt_pool（涨停池）筛出最有可能继续涨停的票。
和主十维打分体系正交：十维找的是"基本面+趋势"长线；这里找的是"情绪+人气"短线接力。

评分模型 (满分 100)：
  连板高度  30 — 1板=10  2板=22  3板=30  4+板=15（高位退潮风险）
  题材热度  25 — reason 命中热门概念=25 / 命中关键词=18 / 无=0
  资金强度  20 — 龙虎榜净买 + 封单/流通市值
  技术面    15 — 换手率健康度 + 炸板次数惩罚
  首板优势  10 — 连板=1 +10（情绪面易接力）

剔除规则：
  - ST / 创业板(300/301) / 科创板(688) / 北交所(8/4/92)
  - 总市值 < 30 亿 (流动性差) 或 > 1500 亿 (大票难板)
  - 炸板 ≥3 次（高度不稳）
  - 连板 ≥4 (高位风险，仅在另一栏"高位预警"列出，不入候选)
  - 涨幅 < 9.0% (异动而非真涨停)
"""
from __future__ import annotations
import re
from typing import Optional

import pandas as pd


# ---------- 配置 ----------

MAX_STREAK_FOR_RELAY = 3      # 4+板进入"高位预警"区，不入接力候选
MIN_MCAP_YI = 30.0            # 流通市值下限（亿）—— 太小流动性差
MAX_MCAP_YI = 1500.0          # 流通市值上限（亿）—— 太大难再板
MAX_BLOWUP = 2                # 最大炸板次数
MIN_PCT = 9.0                 # 真涨停最小涨幅（剔除大宗交易等异动）

EXCLUDE_PREFIXES = ("300", "301", "688", "689", "8", "4", "92")


def _safe_num(val, default: float = 0.0) -> float:
    """pd.to_numeric + NaN/None → default。专治 mock/真实数据混合时的字段缺失。"""
    try:
        n = pd.to_numeric(val, errors="coerce")
        if pd.isna(n):
            return default
        return float(n)
    except (ValueError, TypeError):
        return default


# ---------- 评分子函数 ----------

def _score_streak(streak: int) -> tuple[int, str]:
    """连板高度评分。3 板是接力甜点区，1 板风险最低，4+ 板是高位风险。"""
    if streak <= 0:
        return 0, "无"
    if streak == 1:
        return 10, "首板"
    if streak == 2:
        return 22, "2连"
    if streak == 3:
        return 30, "3连(甜点)"
    return 15, f"{streak}连(高位)"


def _score_theme(reason: str | None, hot_concepts: set[str]) -> tuple[int, str]:
    """题材热度评分。reason 字段直接命中今日热门概念名 = 满分。

    hot_concepts: 今日资金净流入概念榜的概念名集合。
    """
    if not reason:
        return 0, ""
    text = str(reason)
    # 1. 直接命中热门概念名
    matched = [c for c in hot_concepts if c in text]
    if matched:
        return 25, "+".join(matched[:2])
    # 2. 命中通用题材关键词（兜底，不如直接命中权重高）
    hot_kws = ("AI", "算力", "PCB", "光模块", "CPO", "存储", "芯片", "机器人",
               "低空", "固态", "可控核聚变", "稀土", "并购", "重组", "信创",
               "央国企改革", "华为", "AIDC", "HBM", "数据要素", "海洋经济")
    for kw in hot_kws:
        if kw in text:
            return 18, kw
    # 3. 有 reason 但都没命中
    return 8, "其它"


def _score_funds(row: pd.Series, lhb_lookup: dict | None) -> tuple[int, str]:
    """资金强度评分：龙虎榜净买 + 封单强度。"""
    code = str(row.get("代码", "")).zfill(6)
    score = 0
    notes = []

    # 龙虎榜净买
    if lhb_lookup and code in lhb_lookup:
        net = lhb_lookup[code]
        if net >= 5e8:
            score += 12
            notes.append("龙虎+大单")
        elif net >= 1e8:
            score += 8
            notes.append("龙虎+")
        elif net > 0:
            score += 5
            notes.append("龙虎+")
        elif net < -1e8:
            score -= 5  # 净卖出大幅惩罚
            notes.append("龙虎-")

    # 封单 / 流通市值（封板资金占比，越高越稳）
    seal = row.get("封板资金") or row.get("封单资金") or 0
    float_mcap = row.get("流通市值") or 0
    try:
        seal = float(seal); float_mcap = float(float_mcap)
        if float_mcap > 0:
            ratio = seal / float_mcap
            if ratio >= 0.05:
                score += 8
                notes.append("封单厚")
            elif ratio >= 0.02:
                score += 5
                notes.append("封单中")
            elif ratio > 0:
                score += 2
    except (ValueError, TypeError):
        pass

    return min(score, 20), "/".join(notes) if notes else "—"


def _score_tech(row: pd.Series) -> tuple[int, str]:
    """技术面评分：换手率健康度 + 炸板次数惩罚。"""
    score = 0
    notes = []
    try:
        turnover = float(row.get("换手率", 0) or 0)
        # 换手 5-15% 是健康接力区，<3 锁仓但流动性差，>20% 抢筹混乱
        if 5.0 <= turnover <= 15.0:
            score += 10
            notes.append(f"换手{turnover:.1f}%")
        elif 3.0 <= turnover < 5.0 or 15.0 < turnover <= 20.0:
            score += 7
            notes.append(f"换手{turnover:.1f}%")
        elif turnover > 20.0:
            score += 3
            notes.append(f"换手{turnover:.1f}%(高)")
        else:
            score += 5
            notes.append(f"换手{turnover:.1f}%(低)")
    except (ValueError, TypeError):
        pass

    # 炸板次数惩罚
    try:
        blowup = int(row.get("炸板次数", 0) or 0)
        if blowup == 0:
            score += 5
            notes.append("一封到底")
        elif blowup == 1:
            score += 3
            notes.append("1次炸板")
        elif blowup == 2:
            score += 1
            notes.append("2次炸板")
        # >=3 已被前置过滤
    except (ValueError, TypeError):
        pass

    return min(score, 15), "/".join(notes) if notes else "—"


# ---------- 主入口 ----------

def rank_zt_relay(
    zt_pool: Optional[pd.DataFrame],
    concept_ff: Optional[pd.DataFrame] = None,
    lhb_detail: Optional[pd.DataFrame] = None,
    top_n: int = 5,
) -> tuple[list[dict], list[dict]]:
    """对涨停池打分排序。

    返回: (relay_candidates, high_streak_warnings)
      relay_candidates: top_n 接力候选（连板 1-3）
      high_streak_warnings: 4+ 连板警示列表（不接力，仅展示）
    """
    if zt_pool is None or zt_pool.empty:
        return [], []

    # 1) 提取热门概念名集合
    hot_concepts: set[str] = set()
    if concept_ff is not None and not concept_ff.empty:
        col = "行业" if "行业" in concept_ff.columns else concept_ff.columns[0]
        # 取净流入 Top 15 的概念
        hot_concepts = set(concept_ff.head(15)[col].astype(str).tolist())

    # 2) 龙虎榜净买额查表
    lhb_lookup: dict[str, float] = {}
    if lhb_detail is not None and not lhb_detail.empty and "代码" in lhb_detail.columns:
        for _, r in lhb_detail.iterrows():
            try:
                code = str(r["代码"]).zfill(6)
                # 同一只可能多条记录(不同上榜原因)，取累加
                net = float(r.get("龙虎榜净买额") or r.get("净买额") or 0)
                lhb_lookup[code] = lhb_lookup.get(code, 0) + net
            except (ValueError, TypeError):
                continue

    df = zt_pool.copy()
    if "代码" not in df.columns:
        return [], []

    # 3) 硬过滤
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    df = df[~df["代码"].str.startswith(EXCLUDE_PREFIXES)]
    if "名称" in df.columns:
        df = df[~df["名称"].astype(str).str.contains("ST|退", regex=True, na=False)]
    if "炸板次数" in df.columns:
        df = df[pd.to_numeric(df["炸板次数"], errors="coerce").fillna(0) <= MAX_BLOWUP]
    if "涨跌幅" in df.columns:
        df = df[pd.to_numeric(df["涨跌幅"], errors="coerce").fillna(0) >= MIN_PCT]
    if "流通市值" in df.columns:
        mc = pd.to_numeric(df["流通市值"], errors="coerce").fillna(0)
        df = df[(mc >= MIN_MCAP_YI * 1e8) & (mc <= MAX_MCAP_YI * 1e8)]

    if df.empty:
        return [], []

    # 4) 拆分候选 / 高位警示
    df["__streak"] = pd.to_numeric(df.get("连板数", 0), errors="coerce").fillna(0).astype(int)
    relay_pool = df[df["__streak"] <= MAX_STREAK_FOR_RELAY].copy()
    warning_pool = df[df["__streak"] > MAX_STREAK_FOR_RELAY].copy()

    # 5) 打分
    candidates: list[dict] = []
    for _, row in relay_pool.iterrows():
        streak = int(row["__streak"])
        s_streak, n_streak = _score_streak(streak)
        s_theme, n_theme = _score_theme(row.get("所属行业") or row.get("行业") or row.get("题材"), hot_concepts)
        s_funds, n_funds = _score_funds(row, lhb_lookup)
        s_tech, n_tech = _score_tech(row)
        s_first = 10 if streak == 1 else 0  # 首板情绪溢价

        total = s_streak + s_theme + s_funds + s_tech + s_first

        candidates.append({
            "code": row["代码"],
            "name": str(row.get("名称", "")),
            "streak": streak,
            "industry": str(row.get("所属行业") or row.get("行业") or "—"),
            "turnover": _safe_num(row.get("换手率")),
            "amount_yi": _safe_num(row.get("成交额")) / 1e8,
            "float_mcap_yi": _safe_num(row.get("流通市值")) / 1e8,
            "blowup": int(_safe_num(row.get("炸板次数"))),
            "total": total,
            "scores": {
                "streak": s_streak, "theme": s_theme, "funds": s_funds,
                "tech": s_tech, "first": s_first,
            },
            "notes": {
                "streak": n_streak, "theme": n_theme, "funds": n_funds, "tech": n_tech,
            },
            # 接力建议价（次日开盘竞价的安全买点 = 收盘 ×1.02，止损 = 收盘 ×0.93）
            "close": _safe_num(row.get("最新价")),
        })

    candidates.sort(key=lambda x: x["total"], reverse=True)
    candidates = candidates[:top_n]

    # 6) 高位警示
    warnings: list[dict] = []
    for _, row in warning_pool.iterrows():
        warnings.append({
            "code": row["代码"],
            "name": str(row.get("名称", "")),
            "streak": int(row["__streak"]),
            "industry": str(row.get("所属行业") or row.get("行业") or "—"),
            "turnover": _safe_num(row.get("换手率")),
        })
    warnings.sort(key=lambda x: x["streak"], reverse=True)

    return candidates, warnings[:8]


def render_zt_relay_section(
    candidates: list[dict],
    warnings: list[dict],
    label: str = "明日",  # "明日" (evening) or "今日" (pick)
) -> str:
    """渲染涨停接力候选 Markdown 段。"""
    if not candidates and not warnings:
        return ""

    lines: list[str] = ["", "---", f"## 🎯 {label}涨停接力候选"]
    lines.append(f"> 基于今日涨停池（连板/题材/资金/换手）四维打分，独立于主十维候选。")
    lines.append(f"> 接力规则：次日 9:25 集合竞价不超 +3% 进场；不破当日开盘价或低于今日收盘 -3% 止损。")
    lines.append("")

    if candidates:
        lines.append(f"### ✅ 推荐接力 Top {len(candidates)}")
        lines.append("")
        lines.append("| # | 代码 | 名称 | 板高 | 行业 | 综合 | 换手 | 成交 | 流通 | 题材 | 资金 |")
        lines.append("|---|---|---|---|---|---:|---:|---:|---:|---|---|")
        for i, c in enumerate(candidates, 1):
            tag = " 🔥" if c["total"] >= 70 else (" ⭐" if c["total"] >= 55 else "")
            lines.append(
                f"| {i} | `{c['code']}` | **{c['name']}**{tag} | "
                f"{c['notes']['streak']} | {c['industry']} | "
                f"**{c['total']}** | {c['turnover']:.1f}% | "
                f"{c['amount_yi']:.1f}亿 | {c['float_mcap_yi']:.0f}亿 | "
                f"{c['notes']['theme'] or '—'} | {c['notes']['funds']} |"
            )
        lines.append("")

        # 详细买卖点（前 3 只）
        lines.append(f"### 📋 操作建议 (Top {min(3, len(candidates))})")
        lines.append("")
        for i, c in enumerate(candidates[:3], 1):
            buy = c["close"] * 1.02
            stop = c["close"] * 0.93
            lines.append(f"**{i}. {c['code']} {c['name']}** — {c['notes']['streak']}")
            lines.append(f"  - 接力买点：≤ **{buy:.2f}** （+2% 内）")
            lines.append(f"  - 止损：< **{stop:.2f}** （-7%）")
            lines.append(f"  - 加分项：{c['notes']['theme']} · {c['notes']['funds']} · {c['notes']['tech']}")
            lines.append("")

    if warnings:
        lines.append(f"### ⚠️ 高位风险（4+ 板，不建议接力）")
        lines.append("")
        names = [f"`{w['code']}` {w['name']} **{w['streak']}板** ({w['industry']})" for w in warnings]
        lines.append(" · ".join(names))
        lines.append("")

    lines.append("> ⚠️ 接力交易胜率约 50-60%，单只仓位 ≤10%，盘中破止损立即离场。仅供研究。")
    return "\n".join(lines)
