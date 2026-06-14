"""数据加载层：封装 akshare 接口，统一加 timeout + 重试 + 缓存。

所有对外暴露的函数都带 use_mock 参数，dry-run 时返回内置模拟数据。
"""
from __future__ import annotations

import functools
import io
import time
import warnings
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")


def _retry(times: int = 3, delay: float = 1.5):
    """简单重试装饰器。"""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_err = None
            for i in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if i < times - 1:
                        time.sleep(delay * (i + 1))
            raise last_err  # type: ignore[misc]

        return wrapper

    return deco


# ---------- mock 数据（dry-run 用） ----------

_MOCK_SPOT_CSV = """代码,名称,最新价,涨跌幅,成交额,总市值,市净率
600519,贵州茅台,1680.0,1.2,8500000000,2100000000000,9.5
000858,五粮液,158.3,2.1,3200000000,610000000000,5.8
300750,宁德时代,210.5,3.5,5800000000,950000000000,4.2
002594,比亚迪,265.2,1.8,4100000000,770000000000,3.9
601318,中国平安,48.6,0.5,2200000000,890000000000,1.0
000001,平安银行,11.2,-0.3,1500000000,220000000000,0.6
600036,招商银行,38.5,0.8,1900000000,970000000000,1.0
600900,长江电力,28.6,1.5,1100000000,700000000000,3.5
000333,美的集团,68.5,1.9,1700000000,470000000000,3.2
600276,恒瑞医药,45.2,2.3,1300000000,290000000000,5.1
300059,东方财富,15.8,4.2,3800000000,250000000000,4.0
002415,海康威视,32.1,2.8,2100000000,300000000000,3.8
000725,京东方Α,4.85,3.1,2900000000,180000000000,1.2
600030,中信证券,22.3,1.7,1800000000,330000000000,1.4
601012,隆基绿能,18.5,3.6,1600000000,140000000000,2.0
"""


def _mock_spot() -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(_MOCK_SPOT_CSV))
    return df


def _mock_kline(code: str, days: int = 60) -> pd.DataFrame:
    """生成符合多头排列的合成日线（用于 dry-run）。"""
    import numpy as np

    np.random.seed(int(code[-3:]) if code[-3:].isdigit() else 42)
    base = 50.0
    trend = np.linspace(0, 0.4, days)  # 整体上升 40%
    noise = np.random.normal(0, 0.015, days)
    closes = base * (1 + trend + noise.cumsum() * 0.3)
    closes = closes.clip(min=1.0)
    dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
    df = pd.DataFrame(
        {
            "日期": dates,
            "开盘": closes * 0.99,
            "最高": closes * 1.02,
            "最低": closes * 0.98,
            "收盘": closes,
            "成交量": (1e7 * (1 + trend) + np.random.uniform(0, 5e6, days)).astype(int),
            "成交额": (closes * (1e7 * (1 + trend) + np.random.uniform(0, 5e6, days))).astype(int),
        }
    )
    return df


_MOCK_INDUSTRIES = [
    ("电力设备", 8.5),
    ("电子", 6.2),
    ("汽车", 5.8),
    ("食品饮料", 4.1),
    ("银行", 0.5),
    ("钢铁", -1.2),
]


def _mock_industry_top(top_n: int = 5) -> pd.DataFrame:
    df = pd.DataFrame(_MOCK_INDUSTRIES, columns=["板块名称", "近5日涨幅"])
    return df.head(top_n)


def _mock_industry_cons(industry: str) -> pd.DataFrame:
    spot = _mock_spot()
    return spot.head(5)[["代码", "名称"]]


# ---------- 真实接口封装 ----------


@_retry()
def fetch_spot(use_mock: bool = False) -> pd.DataFrame:
    """全市场 A 股快照。"""
    if use_mock:
        return _mock_spot()
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    return df


@_retry()
def fetch_kline(code: str, days: int = 60, use_mock: bool = False) -> pd.DataFrame:
    """单只股票日线。"""
    if use_mock:
        return _mock_kline(code, days)
    import akshare as ak

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2 + 30)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
    )
    return df.tail(days).reset_index(drop=True)


@_retry()
def fetch_industry_rank(top_n: int = 5, use_mock: bool = False) -> pd.DataFrame:
    """近 5 日涨幅最强的 N 个板块。"""
    if use_mock:
        return _mock_industry_top(top_n)
    import akshare as ak

    df = ak.stock_board_industry_name_em()
    # 列名兼容性：东方财富板块字段经常是 板块名称/涨跌幅
    name_col = "板块名称" if "板块名称" in df.columns else df.columns[1]
    chg_col = next((c for c in df.columns if "涨跌幅" in c or "涨幅" in c), df.columns[-1])
    df = df[[name_col, chg_col]].rename(columns={name_col: "板块名称", chg_col: "近5日涨幅"})
    return df.sort_values("近5日涨幅", ascending=False).head(top_n).reset_index(drop=True)


@_retry()
def fetch_industry_cons(industry: str, use_mock: bool = False) -> pd.DataFrame:
    """板块成分股。"""
    if use_mock:
        return _mock_industry_cons(industry)
    import akshare as ak

    df = ak.stock_board_industry_cons_em(symbol=industry)
    return df[["代码", "名称"]] if "代码" in df.columns else df.iloc[:, :2].rename(
        columns={df.columns[0]: "代码", df.columns[1]: "名称"}
    )


def is_trading_day(use_mock: bool = False) -> bool:
    """判断今天是否为 A 股交易日。"""
    if use_mock:
        return True
    try:
        import akshare as ak

        cal = ak.tool_trade_date_hist_sina()
        today = datetime.now().date()
        col = cal.columns[0]
        cal[col] = pd.to_datetime(cal[col]).dt.date
        return today in set(cal[col].tolist())
    except Exception:
        # 接口失败时降级为周一-周五判断
        return datetime.now().weekday() < 5


def fetch_north_flow(code: str, use_mock: bool = False) -> Optional[float]:
    """近 5 日北向持股变动百分比，失败返回 None。"""
    if use_mock:
        import random

        random.seed(int(code[-3:]) if code[-3:].isdigit() else 0)
        return random.uniform(-2, 4)
    try:
        import akshare as ak

        df = ak.stock_hsgt_individual_em(stock=code)
        if df is None or df.empty:
            return None
        col = next((c for c in df.columns if "持股" in c or "比例" in c), None)
        if not col:
            return None
        recent = df[col].tail(5).astype(float)
        if len(recent) < 2:
            return None
        return float(recent.iloc[-1] - recent.iloc[0])
    except Exception:
        return None
