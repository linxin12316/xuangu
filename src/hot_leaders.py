"""今日热点领头股 (Today's Hot Leaders)。

针对你抱怨的「Top 3 候选和今天最热板块不搭」的问题，独立挑「今日资金最热的板块里、当日表现最强的票」——和主十维候选（基本面+趋势）/ zt_relay（涨停接力）正交。

数据来源（盘后稳定可用）：
  - dl.fetch_industry_fundflow()：今日同花顺行业资金净流入榜（90 个一级行业，主板覆盖好）
  - dl.fetch_concept_fundflow()：今日同花顺概念资金净流入榜（385 个，含创业板/科创板次新）
  - dl.fetch_spot()：腾讯/新浪全市场快照，按名称查代码
  - 双源融合：行业榜 + 概念榜的领涨股 ∪ 取并集，主板优先

为什么这样设计？
  - 已踩坑：同花顺强势股接口在非交易时段 change_pct 字段全为 0%（见 [[news-radar]] 笔记）
  - 已踩坑：概念榜 Top N 领涨股大量集中在创业板/科创板（10/16 +20% 等次新票）
  - 行业榜（申万一级）天然偏主板，与概念榜互补，融合后能保证主板候选不被抹掉

筛选：
  - 概念榜 + 行业榜各取 Top N → 取领涨股
  - 排除 ST/创/科/北
  - 涨幅 ≥ 3%
"""
from __future__ import annotations
from typing import Optional

import pandas as pd

from . import data_loader as dl


# ---------- 配置 ----------

MIN_CHANGE_PCT = 3.0           # 领涨股涨幅下限
TOP_INDUSTRY_N = 15            # 行业榜扫描深度（90 个一级行业，前 15 已经够覆盖主板热点）
TOP_CONCEPT_N = 50             # 概念榜扫描深度（创业板/科创板多，需扩大才能筛出主板）
EXCLUDE_PREFIXES = ("300", "301", "688", "689", "8", "4", "92")


# ---------- 主入口 ----------

def rank_hot_leaders(
    concept_ff: Optional[pd.DataFrame],
    industry_ff: Optional[pd.DataFrame] = None,
    hot_stocks: Optional[dict] = None,  # 兼容参数，未使用
    spot_df: Optional[pd.DataFrame] = None,
    top_n: int = 5,
    use_mock: bool = False,
) -> list[dict]:
    """挑今日热点领头股 —— 行业榜 + 概念榜领涨股双源融合。

    Returns: list of {code, name, change_pct, source, sector, net_flow_yi, score}
    """
    if use_mock:
        return _mock_leaders()

    if (concept_ff is None or concept_ff.empty) and (industry_ff is None or industry_ff.empty):
        print("   ℹ️  hot_leaders: concept_ff 和 industry_ff 都为空")
        return []

    # 拉全市场快照用于「领涨股名称 → 代码」反查
    if spot_df is None:
        try:
            spot_df = dl.fetch_spot()
        except Exception as e:
            print(f"   ⚠️  hot_leaders: fetch_spot 失败 {e}")
            spot_df = None

    name_to_code: dict[str, str] = {}
    if spot_df is not None and not spot_df.empty:
        name_col = "名称" if "名称" in spot_df.columns else spot_df.columns[1]
        code_col = "代码" if "代码" in spot_df.columns else spot_df.columns[0]
        for _, r in spot_df.iterrows():
            name_to_code[str(r[name_col])] = str(r[code_col]).zfill(6)

    candidates: list[dict] = []
    seen_codes: set[str] = set()
    stat = {"blacklist": 0, "lowchg": 0, "no_code": 0, "dup_merge": 0}

    def _scan(df: pd.DataFrame, top: int, source: str) -> None:
        for _, r in df.head(top).iterrows():
            try:
                sector = str(r.get("行业") or r.get("概念") or "")
                leader_name = str(r.get("领涨股") or "").strip()
                leader_chg = float(r.get("领涨股-涨跌幅", 0) or 0)
                net_flow = float(r.get("净额", 0) or 0)
            except (ValueError, TypeError):
                continue
            if not leader_name or leader_name == "-":
                continue
            code = name_to_code.get(leader_name)
            if not code:
                stat["no_code"] += 1
                continue

            # 黑名单
            if code.startswith(EXCLUDE_PREFIXES):
                stat["blacklist"] += 1
                continue
            if "ST" in leader_name or "退" in leader_name or leader_name.startswith("N"):
                stat["blacklist"] += 1
                continue

            if leader_chg < MIN_CHANGE_PCT:
                stat["lowchg"] += 1
                continue

            # 重复时合并到原条目
            if code in seen_codes:
                stat["dup_merge"] += 1
                for c in candidates:
                    if c["code"] == code:
                        merged = c.setdefault("sector_list", [c["sector"]])
                        if sector not in merged:
                            merged.append(sector)
                            c["sector"] = " / ".join(merged[:3])
                        break
                continue
            seen_codes.add(code)

            score_chg = min((leader_chg - MIN_CHANGE_PCT) / 7 * 25 + 25, 50)
            if net_flow >= 20:
                score_flow = 30
            elif net_flow >= 10:
                score_flow = 22
            elif net_flow >= 5:
                score_flow = 15
            elif net_flow > 0:
                score_flow = 8
            else:
                score_flow = 0
            rank = len(candidates) + 1
            score_rank = max(20 - rank * 1.0, 0)
            total = round(score_chg + score_flow + score_rank, 1)

            candidates.append({
                "code": code,
                "name": leader_name,
                "change_pct": leader_chg,
                "source": source,
                "sector": sector,
                "sector_list": [sector],
                "net_flow_yi": net_flow,
                "score": total,
                "fallback": False,
            })

    # 1) 行业榜（主板覆盖好，优先扫）
    if industry_ff is not None and not industry_ff.empty:
        _scan(industry_ff, TOP_INDUSTRY_N, "行业")
    # 2) 概念榜（补充科技/题材热点）
    if concept_ff is not None and not concept_ff.empty:
        _scan(concept_ff, TOP_CONCEPT_N, "概念")

    print(f"   📊 hot_leaders: 行业 Top {TOP_INDUSTRY_N} + 概念 Top {TOP_CONCEPT_N} → "
          f"黑名单 {stat['blacklist']} / 涨幅<{MIN_CHANGE_PCT}% {stat['lowchg']} / "
          f"名称无映射 {stat['no_code']} / 重复合并 {stat['dup_merge']} → 入选 {len(candidates)}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


def render_hot_leaders_section(
    leaders: list[dict],
    label: str = "今日",
) -> str:
    """渲染今日热点领头 Markdown 段。"""
    if not leaders:
        return ""

    lines: list[str] = ["", "---", f"## 🔥 {label}热点领头股"]
    lines.append(f"> 行业榜 + 概念榜的领涨股（双源融合，主板优先）。")
    lines.append("> 综合分 = 领涨涨幅 + 板块资金净流入 + 板块热度排名。")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 涨幅 | 来源 | 板块 | 净流入 | 综合 |")
    lines.append("|---|---|---|---:|---|---|---:|---:|")
    for i, c in enumerate(leaders, 1):
        if c["score"] >= 80:
            tag = " 🔥"
        elif c["score"] >= 65:
            tag = " ⭐"
        else:
            tag = ""
        lines.append(
            f"| {i} | `{c['code']}` | **{c['name']}**{tag} | "
            f"+{c['change_pct']:.1f}% | {c.get('source','—')} | {c['sector']} | "
            f"{c['net_flow_yi']:+.1f}亿 | **{c['score']}** |"
        )
    lines.append("")
    lines.append(f"> ⚠️ 此栏为「**情绪/资金驱动**」候选，与十维主候选「**基本面+趋势**」并行。")
    lines.append("> 短线接力需快进快出，单只仓位 ≤10%，破开盘价或 -7% 立即离场。")
    return "\n".join(lines)


def _mock_leaders() -> list[dict]:
    """dry-run 用 mock。"""
    return [
        {"code": "600183", "name": "生益科技", "change_pct": 8.5, "source": "行业",
         "sector": "电子", "sector_list": ["电子"], "net_flow_yi": 25.6, "score": 85.0, "fallback": False},
        {"code": "601127", "name": "赛力斯", "change_pct": 6.2, "source": "概念",
         "sector": "华为概念", "sector_list": ["华为概念"], "net_flow_yi": 18.3, "score": 72.5, "fallback": False},
        {"code": "601012", "name": "隆基绿能", "change_pct": 5.0, "source": "行业",
         "sector": "光伏设备", "sector_list": ["光伏设备"], "net_flow_yi": 8.2, "score": 60.0, "fallback": False},
    ]
