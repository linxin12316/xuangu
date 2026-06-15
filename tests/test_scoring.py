"""scoring 模块单元测试 - 不依赖网络。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import scoring


def _make_kline(n: int, trend: float = 0.4, vol_boost: bool = True) -> pd.DataFrame:
    np.random.seed(0)
    base = 50.0
    closes = base * (1 + np.linspace(0, trend, n) + np.random.normal(0, 0.005, n).cumsum() * 0.1)
    closes = closes.clip(min=1.0)
    if vol_boost:
        vols = np.concatenate([np.ones(n - 5) * 1e6, np.ones(5) * 2e6])
    else:
        vols = np.ones(n) * 1e6
    return pd.DataFrame(
        {
            "收盘": closes,
            "成交量": vols,
        }
    )


# ---------- 趋势/量能/动量/安全（沿用旧用例,数值改为新权重） ----------


def test_trend_bullish():
    kl = _make_kline(60, trend=0.4)
    s = scoring.score_trend(kl["收盘"])
    assert s == 22.0, f"多头排列应满分 22，实得 {s}"


def test_trend_bearish():
    kl = _make_kline(60, trend=-0.4)
    s = scoring.score_trend(kl["收盘"])
    assert s < 22, f"空头不应满分，实得 {s}"


def test_volume_boost():
    kl = _make_kline(60, vol_boost=True)
    s = scoring.score_volume(kl["成交量"])
    assert s > 14, f"放量应高分 (>14/18)，实得 {s}"


def test_volume_flat():
    kl = _make_kline(60, vol_boost=False)
    s = scoring.score_volume(kl["成交量"])
    assert 7 <= s <= 11, f"平量应中性 9 附近，实得 {s}"


def test_rsi_normal_range():
    np.random.seed(7)
    n = 60
    closes = pd.Series(np.linspace(50, 60, n) + np.random.normal(0, 1.5, n))
    s = scoring.score_momentum(closes)
    assert s in (12.0, 6.0), f"温和上涨 RSI 应在中高区间，实得 {s}"


def test_rsi_overbought_zero():
    closes = pd.Series(np.linspace(50, 80, 60))
    s = scoring.score_momentum(closes)
    assert s == 0.0, f"严重超买应 0 分，实得 {s}"


def test_safety_close_to_ma60():
    closes = pd.Series([50.0] * 60)
    s = scoring.score_safety(closes)
    assert s == 8.0, f"贴线应满分 8，实得 {s}"


def test_safety_far_above_ma60():
    closes = pd.Series([50.0] * 30 + [80.0] * 30)
    s = scoring.score_safety(closes)
    assert s < 4, f"严重偏离应低分，实得 {s}"


# ---------- 资金/换手 ----------


def test_fund_north_positive():
    s = scoring.score_fund(north_change=2.5)
    assert s == 10.0, f"+2.5%北向应满分 10，实得 {s}"


def test_fund_north_negative():
    s = scoring.score_fund(north_change=-1.0)
    assert s == 0.0, f"北向流出应 0 分，实得 {s}"


def test_fund_neutral():
    s = scoring.score_fund(north_change=None, north_market_flow=None)
    assert s == 5.0, f"无数据应中性 5，实得 {s}"


def test_turnover_healthy():
    assert scoring.score_turnover(2.0) == 5.0


def test_turnover_overhot():
    assert scoring.score_turnover(15.0) == 0.0


# ---------- 新因子：涨停 / 估值 / 龙虎 / 财务 ----------


def test_limit_up_4_streak_max():
    s = scoring.score_limit_up(zt_streak=4)
    assert s == 10.0, f"4连板应满分10,实得 {s}"


def test_limit_up_3_streak():
    assert scoring.score_limit_up(zt_streak=3) == 8.0


def test_limit_up_2_streak():
    assert scoring.score_limit_up(zt_streak=2) == 6.0


def test_limit_up_1_streak():
    """昨日刚涨停 → 4 分。"""
    assert scoring.score_limit_up(zt_streak=1) == 4.0


def test_limit_up_recent_only():
    """近 7 日有涨停但昨日未涨停 → 2 分。"""
    s = scoring.score_limit_up(zt_streak=0, limit_times_10d=2)
    assert s == 2.0


def test_limit_up_none():
    assert scoring.score_limit_up(zt_streak=0, limit_times_10d=0) == 0.0


def test_valuation_low():
    s = scoring.score_valuation(pe_ttm=12, pb=1.5)
    assert s == 10.0, f"低估值应满分 10,实得 {s}"


def test_valuation_high():
    s = scoring.score_valuation(pe_ttm=80, pb=15)
    assert s == 0.0


def test_valuation_loss_company():
    """亏损股 PE<0,PB 正常的情况。"""
    s = scoring.score_valuation(pe_ttm=-5, pb=1.5)
    assert s == 5.0, f"亏损但 PB 低应得 PB 分,实得 {s}"


def test_valuation_no_data():
    s = scoring.score_valuation(None, None)
    assert s == 5.0


def test_longhu_no_data_neutral():
    s = scoring.score_longhu(lhb_net_buy=None)
    assert s == 2.5, "未上榜/数据缺失应中性 2.5"


def test_longhu_big_buy():
    """上榜+大额净买入 → 满分 5。"""
    assert scoring.score_longhu(lhb_net_buy=1e8) == 5.0


def test_longhu_small_buy():
    assert scoring.score_longhu(lhb_net_buy=1e6) == 4.0


def test_longhu_net_sell():
    """上榜+净卖出 → 0 (常预示出货)。"""
    assert scoring.score_longhu(lhb_net_buy=-1e6) == 0.0


def test_finance_neutral_when_no_perm():
    s = scoring.score_finance(roe=None, profit_growth=None)
    assert s == 2.5


def test_finance_excellent():
    s = scoring.score_finance(roe=20, profit_growth=30)
    assert s == 5.0, f"优秀财务应满分 5,实得 {s}"


def test_finance_negative():
    s = scoring.score_finance(roe=-5, profit_growth=-10)
    assert s == 0.0


# ---------- 集成 ----------


def test_score_one_integration():
    kl = _make_kline(60, trend=0.4, vol_boost=True)
    score = scoring.score_one(
        "000001", "测试", "测试板块", kl, north_change=2.0,
        turnover_rate=2.0, zt_streak=1, pe_ttm=20, pb=2.0,
    )
    assert score is not None
    assert score.total > 50, f"健康 K 线综合分应 >50，实得 {score.total}"
    assert score.suggested_stop_loss < score.last_close


def test_score_one_insufficient_data():
    kl = _make_kline(10)
    score = scoring.score_one("000001", "短K", "测试", kl, north_change=None)
    assert score is None, "数据不足应返回 None"


def test_score_total_in_range():
    kl = _make_kline(60)
    score = scoring.score_one(
        "000001", "test", "ind", kl, north_change=5.0,
        turnover_rate=2.0, zt_streak=2,
        pe_ttm=18, pb=1.8,
    )
    assert score is not None
    assert 0 <= score.total <= 100, f"总分应在 0-100，实得 {score.total}"


def test_score_one_returns_all_dimensions():
    kl = _make_kline(60, trend=0.4, vol_boost=True)
    score = scoring.score_one(
        "000001", "测试", "板块", kl, north_change=1.0,
        turnover_rate=2.0,
    )
    d = score.as_dict()
    for k in ("trend", "volume", "momentum", "fund", "safety", "turnover",
              "limit_up", "valuation", "longhu", "finance", "total"):
        assert k in d, f"as_dict 缺字段 {k}"


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"💥 {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
