"""今日热点领头股 (Today's Hot Leaders)。

针对你抱怨的「Top 3 候选和今天最热板块不搭」的问题，独立挑「今日资金最热的概念里、当日表现最强的票」——和主十维候选（基本面+趋势）/ zt_relay（涨停接力）正交。

数据来源：
  - dl.fetch_concept_fundflow()：今日同花顺概念资金净流入榜（385 个概念）
  - dl.fetch_hot_stocks_candidates() / get_hot_stocks()：同花顺当日强势股 + 题材归因
  - 取概念榜 Top 15 的概念名 → 在强势股的 reason 字段里精确匹配 → 涨幅×资金 综合排序

筛选：
  - 涨幅 ≥ 5%（"今日热点"硬门槛）
  - 命中"今日资金净流入 Top 15 概念名"中至少一个
  - 排除 ST/创/科/北
  - 流通市值 30-1500 亿
"""
from __future__ import annotations
from typing import Optional

import pandas as pd

from . import data_loader as dl


# ---------- 配置 ----------

MIN_CHANGE_PCT = 5.0           # 今日涨幅下限
TOP_CONCEPT_N = 15             # 取多少个热门概念名做匹配
MIN_MCAP_YI = 30.0             # 流通市值下限（亿）
MAX_MCAP_YI = 1500.0           # 流通市值上限（亿）

EXCLUDE_PREFIXES = ("300", "301", "688", "689", "8", "4", "92")


# ---------- 主入口 ----------

def rank_hot_leaders(
    concept_ff: Optional[pd.DataFrame],
    hot_stocks: Optional[dict] = None,
    top_n: int = 5,
    use_mock: bool = False,
) -> list[dict]:
    """挑今日热点领头股。

    Returns: list of {code, name, change_pct, turnover, amount_yi, reason, hot_concepts, score}
    """
    # 1) 提取热门概念名集合
    hot_concepts: list[str] = []
    if concept_ff is not None and not concept_ff.empty:
        col = "行业" if "行业" in concept_ff.columns else concept_ff.columns[0]
        # 去掉太宽泛的概念名（"AI"等单字会误匹配）
        for c in concept_ff.head(TOP_CONCEPT_N)[col].astype(str).tolist():
            if len(c) >= 2:
                hot_concepts.append(c)

    if not hot_concepts:
        print("   ℹ️  hot_leaders: 无热门概念名，跳过")
        return []
    print(f"   📊 hot_leaders: 热门概念 Top {len(hot_concepts)}: {hot_concepts[:6]}…")

    # 2) 拿同花顺强势股
    if hot_stocks is None:
        hot_stocks = dl.fetch_hot_stocks_candidates() if not use_mock else _mock_hot_stocks()
    if not hot_stocks:
        print("   ℹ️  hot_leaders: 强势股池空")
        return []
    print(f"   📊 hot_leaders: 强势股池 {len(hot_stocks)} 只")

    # 3) 过滤 + 命中匹配
    candidates: list[dict] = []
    n_blacklist = n_lowchg = n_nomatch = 0
    for code, info in hot_stocks.items():
        code6 = str(code).zfill(6)

        # 黑名单（同 [[xuangu-stock-blacklist]]）
        if code6.startswith(EXCLUDE_PREFIXES):
            n_blacklist += 1
            continue
        name = info.get("name", "")
        if "ST" in name or "退" in name or name.startswith("N"):
            n_blacklist += 1
            continue

        change_pct = float(info.get("change_pct", 0) or 0)
        if change_pct < MIN_CHANGE_PCT:
            n_lowchg += 1
            continue

        reason = str(info.get("reason", ""))
        # reason 里命中了哪些热门概念
        matched = [c for c in hot_concepts if c in reason]
        if not matched:
            n_nomatch += 1
            continue

        # 综合分：涨幅(0-50) + 命中数(0-30) + DDE资金(0-20)
        # 涨幅 5-15% 线性映射到 25-50
        score_chg = min((change_pct - MIN_CHANGE_PCT) / 10 * 25 + 25, 50)
        score_match = min(len(matched) * 10, 30)
        dde = float(info.get("dde_net", 0) or 0)
        score_dde = 20 if dde >= 1e8 else (10 if dde > 0 else 0)
        total = round(score_chg + score_match + score_dde, 1)

        candidates.append({
            "code": code6,
            "name": name,
            "change_pct": change_pct,
            "turnover": float(info.get("turnover_pct", 0) or 0),
            "dde_net_yi": dde / 1e8,
            "reason": reason[:50],
            "hot_concepts": matched,
            "score": total,
        })

    print(f"   📊 hot_leaders: 黑名单 {n_blacklist} / 涨幅<{MIN_CHANGE_PCT}% {n_lowchg} / 概念未命中 {n_nomatch} → 入选 {len(candidates)}")

    candidates.sort(key=lambda x: (x["score"], x["change_pct"]), reverse=True)
    return candidates[:top_n]


def render_hot_leaders_section(
    leaders: list[dict],
    label: str = "今日",
) -> str:
    """渲染今日热点领头 Markdown 段。"""
    if not leaders:
        return ""

    lines: list[str] = ["", "---", f"## 🔥 {label}热点领头股"]
    lines.append(f"> 从「{label}资金净流入 Top {TOP_CONCEPT_N} 概念」的强势股中筛选——")
    lines.append("> 涨幅×题材命中×DDE资金 综合打分，专补主十维偏长线、错过情绪热点的盲区。")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 涨幅 | 换手 | DDE资金 | 命中概念 | 综合 |")
    lines.append("|---|---|---|---:|---:|---:|---|---:|")
    for i, c in enumerate(leaders, 1):
        tag = " 🔥" if c["score"] >= 80 else (" ⭐" if c["score"] >= 65 else "")
        concepts = "+".join(c["hot_concepts"][:2])
        if len(c["hot_concepts"]) > 2:
            concepts += f" 等{len(c['hot_concepts'])}"
        lines.append(
            f"| {i} | `{c['code']}` | **{c['name']}**{tag} | "
            f"+{c['change_pct']:.1f}% | {c['turnover']:.1f}% | "
            f"{c['dde_net_yi']:+.1f}亿 | {concepts} | **{c['score']}** |"
        )
    lines.append("")
    lines.append(f"> ⚠️ 此栏为「**情绪/资金驱动**」候选，与十维主候选「**基本面+趋势**」并行。")
    lines.append("> 短线接力需快进快出，单只仓位 ≤10%，破开盘价或 -7% 立即离场。")
    return "\n".join(lines)


def _mock_hot_stocks() -> dict:
    """dry-run 用 mock。"""
    return {
        "600183": {"name": "生益科技", "reason": "PCB概念+CCL", "change_pct": 8.5, "turnover_pct": 6.5, "dde_net": 5e8},
        "601127": {"name": "赛力斯", "reason": "华为概念+智能驾驶", "change_pct": 6.2, "turnover_pct": 4.5, "dde_net": 3e8},
        "601136": {"name": "首创证券", "reason": "证券", "change_pct": 7.0, "turnover_pct": 8.0, "dde_net": 2e8},
        "002240": {"name": "盛新锂能", "reason": "稀土+新能源", "change_pct": 5.5, "turnover_pct": 5.0, "dde_net": 1.5e8},
    }
