"""个股打分（十维 0-100）。

权重盘 v2 (2026-06-15):
  趋势 22 + 量能 18 + 动量 12 + 资金 10 + 安全 8 + 换手 5
  + 涨停 10 + 估值 10 + 龙虎榜 5 + 财务 5  = 100
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Score:
    code: str
    name: str
    industry: str
    total: float
    trend: float
    volume: float
    momentum: float
    fund: float
    safety: float
    turnover: float
    limit_up: float
    valuation: float
    longhu: float
    finance: float
    last_close: float
    suggested_stop_loss: float

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "industry": self.industry,
            "total": round(self.total, 1),
            "trend": round(self.trend, 1),
            "volume": round(self.volume, 1),
            "momentum": round(self.momentum, 1),
            "fund": round(self.fund, 1),
            "safety": round(self.safety, 1),
            "turnover": round(self.turnover, 1),
            "limit_up": round(self.limit_up, 1),
            "valuation": round(self.valuation, 1),
            "longhu": round(self.longhu, 1),
            "finance": round(self.finance, 1),
            "last_close": round(self.last_close, 2),
            "suggested_stop_loss": round(self.suggested_stop_loss, 2),
        }


def _ma(s: pd.Series, n: int) -> float:
    return float(s.tail(n).mean()) if len(s) >= n else float("nan")


def _rsi(s: pd.Series, n: int = 14) -> float:
    if len(s) < n + 1:
        return 50.0
    diff = s.diff().dropna()
    up = diff.clip(lower=0).rolling(n).mean().iloc[-1]
    dn = (-diff.clip(upper=0)).rolling(n).mean().iloc[-1]
    if dn == 0:
        return 100.0
    rs = up / dn
    return float(100 - 100 / (1 + rs))


def score_trend(closes: pd.Series) -> float:
    """MA 多头排列：22 满分。"""
    if len(closes) < 20:
        return 0.0
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    if ma5 > ma10 > ma20:
        return 22.0
    if ma5 > ma20 or ma10 > ma20:
        return 11.0
    return 0.0


def score_volume(volumes: pd.Series) -> float:
    """近5日均量/前20日均量 的放量倍数：18 满分。"""
    if len(volumes) < 25:
        return 0.0
    recent5 = float(volumes.tail(5).mean())
    base20 = float(volumes.iloc[-25:-5].mean())
    if base20 == 0:
        return 0.0
    ratio = min(recent5 / base20, 2.0)
    return ratio * 9.0  # 2倍 → 18 分


def score_momentum(closes: pd.Series) -> float:
    """RSI(14) ∈ [50,70] 满分 12。"""
    rsi = _rsi(closes, 14)
    if 50 <= rsi <= 70:
        return 12.0
    if 40 <= rsi < 50 or 70 < rsi <= 80:
        return 6.0
    return 0.0


def score_fund(
    north_change: Optional[float] = None,
    north_market_flow: Optional[float] = None,
) -> float:
    """北向资金 10 分。

    优先用个股北向 5 日变动（Tushare hk_hold）。
    拿不到时回退全市场北向净流入（亿元）。
    都拿不到给中性 5 分。
    """
    if north_change is not None:
        if north_change <= 0:
            return 0.0
        return min(north_change / 2.0, 1.0) * 10.0
    if north_market_flow is not None:
        # -100 亿净流出→0 分, +100 亿净流入→10 分
        return max(0.0, min(10.0, 5.0 + north_market_flow * 5.0 / 100.0))
    return 5.0


def score_turnover(turnover_rate: Optional[float]) -> float:
    """换手率评分（5 分）。

    - 1%~5%：健康活跃 → 5 分
    - 0.5~1% 或 5~10%：中性 → 2.5 分
    - <0.5%（太冷）或 >10%（过热）→ 0 分
    """
    if turnover_rate is None:
        return 2.5
    if 1.0 <= turnover_rate <= 5.0:
        return 5.0
    if 0.5 <= turnover_rate < 1.0 or 5.0 < turnover_rate <= 10.0:
        return 2.5
    return 0.0


def score_safety(closes: pd.Series) -> float:
    """距 60 日均线偏离度：<15% 满分 8，30% 时归零。"""
    if len(closes) < 60:
        return 4.0
    ma60 = _ma(closes, 60)
    last = float(closes.iloc[-1])
    if ma60 == 0:
        return 4.0
    deviation = (last - ma60) / ma60
    if deviation <= 0.15:
        return 8.0
    if deviation >= 0.30:
        return 0.0
    return float(8.0 * (0.30 - deviation) / 0.15)


def score_limit_up(limit_times_10d: int = 0, max_streak: int = 0) -> float:
    """涨停强度 10 分。

    - 近期(7日窗口)涨停 1 次 → 4 分
    - 涨停 2 次 → 6 分
    - 涨停 3+ 次 → 8 分
    - 同时有连板(max_streak ≥ 2) → 额外 +2 分(最高 10)
    - 完全无涨停 → 0 分
    """
    if limit_times_10d <= 0 and max_streak <= 0:
        return 0.0
    base = 0.0
    if limit_times_10d >= 3:
        base = 8.0
    elif limit_times_10d == 2:
        base = 6.0
    elif limit_times_10d == 1:
        base = 4.0
    bonus = 2.0 if max_streak >= 2 else 0.0
    return min(10.0, base + bonus)


def score_valuation(
    pe_ttm: Optional[float] = None,
    pb: Optional[float] = None,
) -> float:
    """估值评分 10 分（PE / PB 横截面分位代理）。

    免费 Tushare 没有 3 年历史 PE 分位接口，这里用单日全市场分位代替不了，
    所以退化为绝对阈值打分（行业中位数 PE 一般 20-30，PB 一般 2-3）。

    - PE 0~15: +5 分（低估）
    - PE 15~30: +3 分（合理）
    - PE 30~60: +1 分
    - PE 60+ 或 PE<0(亏损): 0 分
    - PB 0~2: +5 分
    - PB 2~5: +3 分
    - PB 5~10: +1 分
    - PB 10+ 或 PB<0: 0 分

    数据缺失时该项给中性 5 分。
    """
    if pe_ttm is None and pb is None:
        return 5.0

    pe_score = 0.0
    if pe_ttm is not None and pe_ttm > 0:
        if pe_ttm <= 15:
            pe_score = 5.0
        elif pe_ttm <= 30:
            pe_score = 3.0
        elif pe_ttm <= 60:
            pe_score = 1.0
    elif pe_ttm is None:
        pe_score = 2.5

    pb_score = 0.0
    if pb is not None and pb > 0:
        if pb <= 2:
            pb_score = 5.0
        elif pb <= 5:
            pb_score = 3.0
        elif pb <= 10:
            pb_score = 1.0
    elif pb is None:
        pb_score = 2.5

    return float(pe_score + pb_score)


def score_longhu(longhu_active: Optional[bool] = None) -> float:
    """龙虎榜活跃度 5 分。

    Tushare top_list 接口需要 2000 积分，免费版无法使用，
    longhu_active=None 时退化为中性 2.5 分。
    True → 5 分；False → 0 分。
    """
    if longhu_active is None:
        return 2.5
    return 5.0 if longhu_active else 0.0


def score_finance(
    roe: Optional[float] = None,
    profit_growth: Optional[float] = None,
) -> float:
    """财务质量 5 分。

    Tushare fina_indicator 需要 2000 积分，免费版无法使用，
    数据缺失时退化为中性 2.5 分。

    ROE 维度（满分 3）：
      - ROE >= 15% → 3 分
      - ROE >= 8% → 2 分
      - ROE >= 0% → 1 分
      - ROE < 0% → 0 分
    净利润同比（满分 2）：
      - growth >= 20% → 2 分
      - growth >= 0% → 1 分
      - growth < 0% → 0 分
    """
    if roe is None and profit_growth is None:
        return 2.5

    roe_score = 0.0
    if roe is not None:
        if roe >= 15:
            roe_score = 3.0
        elif roe >= 8:
            roe_score = 2.0
        elif roe >= 0:
            roe_score = 1.0
    else:
        roe_score = 1.5

    growth_score = 0.0
    if profit_growth is not None:
        if profit_growth >= 20:
            growth_score = 2.0
        elif profit_growth >= 0:
            growth_score = 1.0
    else:
        growth_score = 1.0

    return float(roe_score + growth_score)


def score_one(
    code: str,
    name: str,
    industry: str,
    kline: pd.DataFrame,
    north_change: Optional[float],
    north_market_flow: Optional[float] = None,
    turnover_rate: Optional[float] = None,
    limit_times_10d: int = 0,
    max_streak: int = 0,
    pe_ttm: Optional[float] = None,
    pb: Optional[float] = None,
    longhu_active: Optional[bool] = None,
    roe: Optional[float] = None,
    profit_growth: Optional[float] = None,
) -> Optional[Score]:
    if kline is None or kline.empty or "收盘" not in kline.columns:
        return None
    closes = kline["收盘"].astype(float).reset_index(drop=True)
    volumes = kline["成交量"].astype(float).reset_index(drop=True) if "成交量" in kline.columns else pd.Series(dtype=float)
    if len(closes) < 25:
        return None

    t = score_trend(closes)
    v = score_volume(volumes) if not volumes.empty else 0.0
    m = score_momentum(closes)
    f = score_fund(north_change, north_market_flow)
    s = score_safety(closes)
    to = score_turnover(turnover_rate)
    lu = score_limit_up(limit_times_10d, max_streak)
    val = score_valuation(pe_ttm, pb)
    lh = score_longhu(longhu_active)
    fin = score_finance(roe, profit_growth)

    last = float(closes.iloc[-1])
    # 止损建议：MA20 与 -7% 取较高者
    ma20 = _ma(closes, 20)
    stop = max(ma20, last * 0.93) if not np.isnan(ma20) else last * 0.93

    return Score(
        code=code,
        name=name,
        industry=industry,
        total=t + v + m + f + s + to + lu + val + lh + fin,
        trend=t,
        volume=v,
        momentum=m,
        fund=f,
        safety=s,
        turnover=to,
        limit_up=lu,
        valuation=val,
        longhu=lh,
        finance=fin,
        last_close=last,
        suggested_stop_loss=stop,
    )
