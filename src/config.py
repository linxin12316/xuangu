"""配置加载与黑名单。

仓库根的 config.json 示例:
{
  "blacklist": {
    "codes": ["688981", "300433"],
    "name_keywords": ["华大基因", "退"]
  },
  "max_per_industry": 1,
  "max_picks": 5
}

所有字段都是可选，缺失时使用代码内默认值。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


_DEFAULT = {
    "blacklist": {"codes": [], "name_keywords": []},
    "max_per_industry": 1,    # Top 5 中同行业最多保留 1 只
    "max_picks": 5,
}


def load_config() -> dict:
    """读取 config.json，缺失字段用默认值兜底。"""
    if not CONFIG_PATH.exists():
        return _DEFAULT.copy()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  config.json 解析失败: {e}, 使用默认配置")
        return _DEFAULT.copy()

    out = _DEFAULT.copy()
    bl = data.get("blacklist", {}) or {}
    out["blacklist"] = {
        "codes": [str(c).zfill(6) for c in bl.get("codes", [])],
        "name_keywords": list(bl.get("name_keywords", [])),
    }
    if "max_per_industry" in data:
        out["max_per_industry"] = int(data["max_per_industry"])
    if "max_picks" in data:
        out["max_picks"] = int(data["max_picks"])
    return out


def apply_blacklist(df: pd.DataFrame, blacklist: dict) -> pd.DataFrame:
    """从 DataFrame（必须有 代码 / 名称 列）中剔除黑名单条目。

    blacklist = {"codes": [...], "name_keywords": [...]}
    """
    if df is None or df.empty:
        return df
    codes = set(blacklist.get("codes") or [])
    keywords = list(blacklist.get("name_keywords") or [])

    if "代码" in df.columns and codes:
        df = df[~df["代码"].astype(str).str.zfill(6).isin(codes)]
    name_col = "名称" if "名称" in df.columns else None
    if name_col and keywords:
        pattern = "|".join(map(str, keywords))
        df = df[~df[name_col].astype(str).str.contains(pattern, regex=True, na=False)]
    return df.reset_index(drop=True)


def dedup_by_industry(picks: list, max_per_industry: int = 1) -> list:
    """同一行业最多保留 max_per_industry 只（按出现顺序，picks 已按总分排序）。"""
    if max_per_industry <= 0:
        return picks
    seen: dict[str, int] = {}
    out = []
    for p in picks:
        ind = getattr(p, "industry", None) or "_unknown"
        # "全市场"或空白行业按"_unknown"统一处理但不去重
        if ind in ("全市场", "", "_unknown"):
            out.append(p)
            continue
        if seen.get(ind, 0) >= max_per_industry:
            continue
        seen[ind] = seen.get(ind, 0) + 1
        out.append(p)
    return out
