"""技术面信号引擎 — 从 Iwencai「基础技术指标信号引擎」技能移植。

整合趋势（EMA/ADX）、均值回归（布林带/RSI）、量价（OBV/量比）三维度，
通过投票机制生成综合技术信号。纯 pandas 实现。

用法：
  from technical_signals import compute_technical_score
  score_map = compute_technical_score(kline_map)
  # score_map[code] = {"adx_signal": 0-1, "bb_signal": 0-1, "obv_signal": 0-1, ...}
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """计算 RSI（Wilder EWM 平滑）。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """计算 ADX 及 +DI/-DI。"""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    alpha = 1 / period
    smoothed_tr = tr.ewm(alpha=alpha, min_periods=period).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=alpha, min_periods=period).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=alpha, min_periods=period).mean()

    plus_di = 100 * smoothed_plus_dm / smoothed_tr.replace(0, np.nan)
    minus_di = 100 * smoothed_minus_dm / smoothed_tr.replace(0, np.nan)

    di_sum = plus_di + minus_di
    di_sum = di_sum.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, min_periods=period).mean()

    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx})


def compute_bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """计算布林带。"""
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    return pd.DataFrame({
        "bb_mid": mid,
        "bb_upper": mid + num_std * std,
        "bb_lower": mid - num_std * std,
    })


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """计算 OBV（能量潮指标）。"""
    sign = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (volume * sign).cumsum()


def compute_technical_score(kline: pd.DataFrame) -> dict:
    """对一只股票的 K 线计算三维度技术信号得分。

    Args:
        kline: DataFrame 必须包含 收盘/最高/最低/成交量 列。

    Returns:
        dict: {
            "adx_trend": 0-10,        # ADX 趋势强度
            "adx_direction": 0-10,     # +DI/-DI 多空方向
            "bb_position": 0-10,       # 布林带位置（越低越靠近下轨→超卖）
            "rsi_position": 0-10,      # RSI 位置（50-70=多头强势区）
            "obv_trend": 0-10,         # OBV 量价配合度
            "composite_signal": 0-10,  # 综合信号
        }
        数据不足时返回全 0。
    """
    required = {"收盘", "最高", "最低", "成交量"}
    if kline is None or kline.empty or not required.issubset(kline.columns):
        return {k: 0.0 for k in ("adx_trend", "adx_direction", "bb_position", "rsi_position", "obv_trend", "composite_signal")}

    close = kline["收盘"].astype(float)
    high = kline["最高"].astype(float)
    low = kline["最低"].astype(float)
    volume = kline["成交量"].astype(float)

    if len(close) < 30:
        return {k: 0.0 for k in ("adx_trend", "adx_direction", "bb_position", "rsi_position", "obv_trend", "composite_signal")}

    out = {}

    # === 1. ADX 趋势强度 ===
    adx_df = compute_adx(high, low, close, period=14)
    latest_adx = float(adx_df["adx"].iloc[-1]) if not adx_df["adx"].empty and pd.notna(adx_df["adx"].iloc[-1]) else 0
    # ADX > 25 视为趋势行情，> 40 强趋势
    if latest_adx >= 40:
        out["adx_trend"] = 10.0
    elif latest_adx >= 30:
        out["adx_trend"] = 7.0
    elif latest_adx >= 25:
        out["adx_trend"] = 5.0
    elif latest_adx >= 20:
        out["adx_trend"] = 3.0
    else:
        out["adx_trend"] = 1.0

    # ADX 方向：+DI > -DI → 多头
    latest_plus = float(adx_df["plus_di"].iloc[-1]) if not adx_df["plus_di"].empty and pd.notna(adx_df["plus_di"].iloc[-1]) else 0
    latest_minus = float(adx_df["minus_di"].iloc[-1]) if not adx_df["minus_di"].empty and pd.notna(adx_df["minus_di"].iloc[-1]) else 0
    if latest_plus > latest_minus + 10:
        out["adx_direction"] = 10.0
    elif latest_plus > latest_minus:
        out["adx_direction"] = 7.0
    elif abs(latest_plus - latest_minus) <= 5:
        out["adx_direction"] = 5.0
    elif latest_minus > latest_plus:
        out["adx_direction"] = 3.0
    else:
        out["adx_direction"] = 1.0

    # === 2. 布林带位置（均值回归） ===
    bb = compute_bollinger(close, window=20, num_std=2.0)
    last_close = float(close.iloc[-1])
    bb_upper = float(bb["bb_upper"].iloc[-1]) if not bb["bb_upper"].empty and pd.notna(bb["bb_upper"].iloc[-1]) else last_close
    bb_lower = float(bb["bb_lower"].iloc[-1]) if not bb["bb_lower"].empty and pd.notna(bb["bb_lower"].iloc[-1]) else last_close
    bb_mid = float(bb["bb_mid"].iloc[-1]) if not bb["bb_mid"].empty and pd.notna(bb["bb_mid"].iloc[-1]) else last_close

    bb_range = bb_upper - bb_lower
    if bb_range > 0:
        # 位置比例 0~1（0=下轨，1=上轨）
        bb_pos = (last_close - bb_lower) / bb_range
    else:
        bb_pos = 0.5

    # 靠近下轨(≤0.2) → 超卖，技术面偏多 → 高评分
    if bb_pos <= 0.2:
        out["bb_position"] = 10.0
    elif bb_pos <= 0.35:
        out["bb_position"] = 8.0
    elif bb_pos <= 0.5:
        out["bb_position"] = 6.0
    elif bb_pos <= 0.65:
        out["bb_position"] = 4.0
    elif bb_pos <= 0.8:
        out["bb_position"] = 2.0
    else:
        out["bb_position"] = 0.0

    # === 3. RSI 位置 ===
    rsi_series = compute_rsi(close, period=14)
    latest_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else 50
    if 50 <= latest_rsi <= 70:
        out["rsi_position"] = 10.0
    elif 40 <= latest_rsi < 50:
        out["rsi_position"] = 7.0
    elif 70 < latest_rsi <= 80:
        out["rsi_position"] = 6.0
    elif 30 <= latest_rsi < 40:
        out["rsi_position"] = 5.0
    elif latest_rsi < 30:
        out["rsi_position"] = 8.0  # 超卖区域，反弹预期
    else:
        out["rsi_position"] = 2.0

    # === 4. OBV 量价配合度 ===
    obv_series = compute_obv(close, volume)
    obv_ma = obv_series.rolling(20).mean()
    if len(obv_series) >= 20 and not obv_ma.empty and pd.notna(obv_ma.iloc[-1]):
        if obv_series.iloc[-1] > obv_ma.iloc[-1] * 1.05:
            out["obv_trend"] = 10.0  # 量价配合上行
        elif obv_series.iloc[-1] > obv_ma.iloc[-1]:
            out["obv_trend"] = 7.0
        elif obv_series.iloc[-1] > obv_ma.iloc[-1] * 0.95:
            out["obv_trend"] = 5.0
        elif obv_series.iloc[-1] > obv_ma.iloc[-1] * 0.9:
            out["obv_trend"] = 3.0
        else:
            out["obv_trend"] = 1.0
    else:
        out["obv_trend"] = 5.0

    # === 5. 综合信号（等权平均） ===
    scores = [out["adx_trend"], out["adx_direction"], out["bb_position"], out["rsi_position"], out["obv_trend"]]
    out["composite_signal"] = sum(scores) / len(scores)

    return out
