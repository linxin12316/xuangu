"""个股五维打分（0-100）。"""
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
    last_close: float
    suggested_stop_loss: float  # 止损建议价位

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
    """MA 多头排列：30 满分。"""
    if len(closes) < 20:
        return 0.0
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    if ma5 > ma10 > ma20:
        return 30.0
    if ma5 > ma20 or ma10 > ma20:
        return 15.0
    return 0.0


def score_volume(volumes: pd.Series) -> float:
    """近5日均量/前20日均量 的放量倍数：25 满分。"""
    if len(volumes) < 25:
        return 0.0
    recent5 = float(volumes.tail(5).mean())
    base20 = float(volumes.iloc[-25:-5].mean())
    if base20 == 0:
        return 0.0
    ratio = min(recent5 / base20, 2.0)
    return ratio * 12.5


def score_momentum(closes: pd.Series) -> float:
    """RSI(14) ∈ [50,70] 满分 20。"""
    rsi = _rsi(closes, 14)
    if 50 <= rsi <= 70:
        return 20.0
    if 40 <= rsi < 50 or 70 < rsi <= 80:
        return 10.0
    return 0.0


def score_fund(north_change: Optional[float]) -> float:
    """北向持股 5 日变动：>2% 给满分 15，0~2% 线性，<0 不得分。"""
    if north_change is None:
        return 7.0  # 数据缺失给中性分
    if north_change <= 0:
        return 0.0
    return min(north_change / 2.0, 1.0) * 15.0


def score_safety(closes: pd.Series) -> float:
    """距 60 日均线偏离度：<15% 满分 10，30% 时归零。"""
    if len(closes) < 60:
        return 5.0
    ma60 = _ma(closes, 60)
    last = float(closes.iloc[-1])
    if ma60 == 0:
        return 5.0
    deviation = (last - ma60) / ma60
    if deviation <= 0.15:
        return 10.0
    if deviation >= 0.30:
        return 0.0
    return float(10.0 * (0.30 - deviation) / 0.15)


def score_one(
    code: str,
    name: str,
    industry: str,
    kline: pd.DataFrame,
    north_change: Optional[float],
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
    f = score_fund(north_change)
    s = score_safety(closes)

    last = float(closes.iloc[-1])
    # 止损建议：MA20 与 -7% 取较高者（避免假突破后深套）
    ma20 = _ma(closes, 20)
    stop = max(ma20, last * 0.93) if not np.isnan(ma20) else last * 0.93

    return Score(
        code=code,
        name=name,
        industry=industry,
        total=t + v + m + f + s,
        trend=t,
        volume=v,
        momentum=m,
        fund=f,
        safety=s,
        last_close=last,
        suggested_stop_loss=stop,
    )
