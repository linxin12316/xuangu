"""个股打分（十二维 0-110 → 归一化 0-100）。

v3 (2026-06-16):
  趋势 18 + 量能 14 + 动量 10 + 资金 8 + 安全 6 + 换手 4
  + 涨停 8 + 估值 8 + 龙虎榜 4 + 财务 4
  + **技术信号 10** + **因子得分 6**  = 100

新增技术信号（从 Iwencai「基础技术指标信号引擎」技能移植）：
  ADX 趋势强度 + 方向 + 布林带位置 + RSI 位置 + OBV 量价配合 → 三维投票综合评分

新增因子得分（从 Iwencai「多因子选股策略」技能移植）：
  截面 Z-score 标准化后的动量/反转/波动率/量比因子等权综合得分 → 相对于候选池的相对排名
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
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
    technical: float        # 技术信号（ADX/BB/OBV 三维投票，0-10 归一化）
    factor_score: float     # 多因子截面得分（相对排名，0-6 归一化）
    last_close: float
    suggested_stop_loss: float
    fund_flow: Optional[dict] = field(default=None)

    def as_dict(self) -> dict:
        d = {
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
            "technical_signal": round(self.technical, 1),
            "factor_score": round(self.factor_score, 1),
            "last_close": round(self.last_close, 2),
            "suggested_stop_loss": round(self.suggested_stop_loss, 2),
        }
        if self.fund_flow:
            d["fund_flow_total"] = round(self.fund_flow.get("total_main_net", 0), 0)
            d["fund_flow_trend"] = self.fund_flow.get("trend", "")
            d["fund_flow_positive_days"] = self.fund_flow.get("positive_days", 0)
        return d


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


def score_limit_up(zt_streak: int = 0, limit_times_10d: int = 0) -> float:
    """涨停强度 10 分。

    数据源（按优先级）:
      - zt_streak: 来自 stock_zt_pool_em 的"连板数"字段（昨日涨停时该值≥1，最稳定）
      - limit_times_10d: 来自 Tushare limit_list_d 的近期次数（限速时常缺）

    评分:
      - 4 连板及以上 → 10 分（妖股级）
      - 3 连板 → 8 分
      - 2 连板 → 6 分
      - 1 连板（昨日刚涨停）→ 4 分
      - 近 7 日有涨停（仅靠 limit_times_10d）→ 2 分
      - 否则 0 分
    """
    if zt_streak >= 4:
        return 10.0
    if zt_streak == 3:
        return 8.0
    if zt_streak == 2:
        return 6.0
    if zt_streak == 1:
        return 4.0
    if limit_times_10d >= 1:
        return 2.0
    return 0.0


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


def score_longhu(lhb_net_buy: Optional[float] = None) -> float:
    """龙虎榜活跃度 5 分。

    lhb_net_buy: 来自 stock_lhb_detail_em 的"龙虎榜净买额"字段（元）。
      - None: 未上榜（也未拉到数据）→ 中性 2.5 分
      - > 5000万: 大资金净买入 → 5 分
      - 0 ~ 5000万: 小幅净买入 → 4 分
      - 0: 平 → 2.5 分
      - < 0: 净卖出（上榜+卖出常预示出货）→ 0 分
    """
    if lhb_net_buy is None:
        return 2.5
    if lhb_net_buy >= 5e7:
        return 5.0
    if lhb_net_buy > 0:
        return 4.0
    if lhb_net_buy == 0:
        return 2.5
    return 0.0


def score_technical(tech_signal: dict) -> float:
    """技术信号评分（0-10 归一化到 0-10 权重）。

    基于 ADX/布林带/RSI/OBV 三维投票综合信号。
    参数来自 technical_signals.compute_technical_score() 的返回。
    """
    composite = tech_signal.get("composite_signal", 0)
    # composite_signal 范围 0-10，直接映射
    return min(composite, 10.0)


def score_factor_zscore(zscore: Optional[float]) -> float:
    """截面因子 Z-score 评分（0-6 归一化）。

    zscore: 经过 Z-score 标准化后的综合因子得分。
      - z > 1.5: 远超平均 → 6 分
      - z > 1.0: 超过平均 1 个标准差 → 5 分
      - z > 0.5: 略超平均 → 4 分
      - -0.5 ≤ z ≤ 0.5: 中性 → 3 分
      - z < -0.5: 偏弱 → 1 分
      - z < -1.0: 显著偏弱 → 0 分
      - None: 无法计算 → 3 分 (中性)
    """
    if zscore is None:
        return 3.0
    if zscore >= 1.5:
        return 6.0
    if zscore >= 1.0:
        return 5.0
    if zscore >= 0.5:
        return 4.0
    if zscore >= -0.5:
        return 3.0
    if zscore >= -1.0:
        return 1.0
    return 0.0


def zscore_cross_section(values: dict[str, Optional[float]]) -> dict[str, float]:
    """截面 Z-score 标准化（从「多因子选股策略」技能移植）。

    对一组标的的某个因子值做截面标准化：
      z = (x - mean) / std

    Args:
        values: {code: factor_value}

    Returns:
        {code: z_score}
    """
    valid = {k: v for k, v in values.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
    if len(valid) < 3:
        return {k: 0.0 for k in values}
    vals = list(valid.values())
    mean = sum(vals) / len(vals)
    variance = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
    std = math.sqrt(variance) if variance > 1e-12 else 1.0
    return {k: 0.0 if v is None else ((v - mean) / std) for k, v in values.items()}


def compute_cross_sectional_factors(
    kline_map: dict[str, pd.DataFrame],
    candidate_codes: list[str],
) -> dict[str, float]:
    """候选池截面因子评分。

    计算动量/波动率/量比三个因子，Z-score 标准化后等权合成。

    Args:
        kline_map: {code: kline_df}
        candidate_codes: 候选代码列表

    Returns:
        {code: composite_zscore}
    """
    factor_data: dict[str, dict[str, Optional[float]]] = {c: {} for c in candidate_codes}

    for code in candidate_codes:
        kline = kline_map.get(code)
        if kline is None or kline.empty or "收盘" not in kline.columns:
            continue
        close = kline["收盘"].astype(float)
        if len(close) < 20:
            continue

        # 动量因子：过去 20 日累计收益
        momentum = close.iloc[-1] / close.iloc[0] - 1 if len(close) >= 20 else 0
        factor_data[code]["momentum"] = momentum * 100  # 转百分比

        # 波动率因子（反向）：过去 20 日收益标准差（取负，值越大越好）
        returns = close.pct_change().dropna()
        if len(returns) >= 20:
            vol = returns.tail(20).std() * 100
            factor_data[code]["volatility"] = -vol  # 反向
        else:
            factor_data[code]["volatility"] = None

        # 量比因子：近 5 日 / 前 20 日均量
        if "成交量" in kline.columns:
            volume = kline["成交量"].astype(float)
            if len(volume) >= 25:
                recent5 = volume.tail(5).mean()
                prev20 = volume.iloc[-25:-5].mean()
                factor_data[code]["volume_ratio"] = (recent5 / prev20) - 1 if prev20 > 0 else 0
            else:
                factor_data[code]["volume_ratio"] = None
        else:
            factor_data[code]["volume_ratio"] = None

    # 每个因子做截面 Z-score
    composite: dict[str, float] = {c: 0.0 for c in candidate_codes}
    factor_count = 0
    for factor_name in ("momentum", "volatility", "volume_ratio"):
        raw = {c: factor_data[c].get(factor_name) for c in candidate_codes}
        zs = zscore_cross_section(raw)
        for c in candidate_codes:
            composite[c] = composite.get(c, 0.0) + zs.get(c, 0.0)
        factor_count += 1

    # 等权平均 composite_zscore
    if factor_count > 0:
        for c in candidate_codes:
            composite[c] = composite[c] / factor_count
    return composite


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
    zt_streak: int = 0,
    limit_times_10d: int = 0,
    pe_ttm: Optional[float] = None,
    pb: Optional[float] = None,
    lhb_net_buy: Optional[float] = None,
    roe: Optional[float] = None,
    profit_growth: Optional[float] = None,
    tech_signal: Optional[dict] = None,          # 新增：技术信号得分
    factor_zscore: Optional[float] = None,       # 新增：截面因子 Z-score
) -> Optional[Score]:
    if kline is None or kline.empty or "收盘" not in kline.columns:
        return None
    closes = kline["收盘"].astype(float).reset_index(drop=True)
    volumes = kline["成交量"].astype(float).reset_index(drop=True) if "成交量" in kline.columns else pd.Series(dtype=float)
    if len(closes) < 25:
        return None

    # 原始十维评分（权重总计 80，较 v2 压缩18%腾给新维度）
    t = score_trend(closes) * (18 / 22)     # 22→18
    v = score_volume(volumes) * (14 / 18) if not volumes.empty else 0.0      # 18→14
    m = score_momentum(closes) * (10 / 12)  # 12→10
    f = score_fund(north_change, north_market_flow) * (8 / 10)   # 10→8
    s = score_safety(closes) * (6 / 8)      # 8→6
    to = score_turnover(turnover_rate) * (4 / 5)  # 5→4
    lu = score_limit_up(zt_streak=zt_streak, limit_times_10d=limit_times_10d) * (8 / 10)  # 10→8
    val = score_valuation(pe_ttm, pb) * (8 / 10)  # 10→8
    lh = score_longhu(lhb_net_buy) * (4 / 5)       # 5→4
    fin = score_finance(roe, profit_growth) * (4 / 5)  # 5→4

    # 新增：技术信号评分（权重 10）
    tech = score_technical(tech_signal or {}) * (10 / 10)

    # 新增：因子截面得分（权重 6）
    fac = score_factor_zscore(factor_zscore) * (6 / 6)

    last = float(closes.iloc[-1])
    # 止损建议：MA20 与 -7% 取较高者
    ma20 = _ma(closes, 20)
    stop = max(ma20, last * 0.93) if not np.isnan(ma20) else last * 0.93

    return Score(
        code=code,
        name=name,
        industry=industry,
        total=t + v + m + f + s + to + lu + val + lh + fin + tech + fac,
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
        technical=tech,
        factor_score=fac,
        last_close=last,
        suggested_stop_loss=stop,
    )
