"""地雷股过滤。"""
from __future__ import annotations

import pandas as pd


def remove_st(df: pd.DataFrame) -> pd.DataFrame:
    name_col = "名称" if "名称" in df.columns else df.columns[1]
    mask = ~df[name_col].astype(str).str.contains("ST|退", regex=True, na=False)
    return df[mask].reset_index(drop=True)


def remove_suspended(df: pd.DataFrame) -> pd.DataFrame:
    """成交额 0 视为停牌。"""
    if "成交额" not in df.columns:
        return df
    return df[df["成交额"].fillna(0) > 0].reset_index(drop=True)


def remove_small_cap(df: pd.DataFrame, min_cap_yi: float = 30.0) -> pd.DataFrame:
    """剔除总市值 < 30 亿。"""
    if "总市值" not in df.columns:
        return df
    return df[df["总市值"].fillna(0) >= min_cap_yi * 1e8].reset_index(drop=True)


def remove_bj(df: pd.DataFrame) -> pd.DataFrame:
    """北交所成交清淡，从候选池中剔除（用户主战场仍是沪深）。"""
    if "代码" not in df.columns:
        return df
    return df[~df["代码"].astype(str).str.startswith(("8", "4", "92"))].reset_index(drop=True)


def apply_all(df: pd.DataFrame) -> pd.DataFrame:
    df = remove_st(df)
    df = remove_suspended(df)
    df = remove_small_cap(df)
    df = remove_bj(df)
    return df
