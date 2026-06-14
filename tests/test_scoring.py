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


def test_trend_bullish():
    kl = _make_kline(60, trend=0.4)
    s = scoring.score_trend(kl["收盘"])
    assert s == 30.0, f"多头排列应满分 30，实得 {s}"


def test_trend_bearish():
    kl = _make_kline(60, trend=-0.4)
    s = scoring.score_trend(kl["收盘"])
    assert s < 30, f"空头不应满分，实得 {s}"


def test_volume_boost():
    kl = _make_kline(60, vol_boost=True)
    s = scoring.score_volume(kl["成交量"])
    assert s > 20, f"放量应高分，实得 {s}"


def test_volume_flat():
    kl = _make_kline(60, vol_boost=False)
    s = scoring.score_volume(kl["成交量"])
    assert 10 <= s <= 15, f"平量应中性 12.5 附近，实得 {s}"


def test_rsi_normal_range():
    # 带回调的温和上涨，RSI 应在 50-70 区间
    np.random.seed(7)
    n = 60
    closes = pd.Series(np.linspace(50, 60, n) + np.random.normal(0, 1.5, n))
    s = scoring.score_momentum(closes)
    assert s in (20.0, 10.0), f"温和上涨(带回调) RSI 应在中高区间，实得 {s}"


def test_rsi_overbought_zero():
    # 单调暴涨 RSI=100,应该 0 分(避免追高)
    closes = pd.Series(np.linspace(50, 80, 60))
    s = scoring.score_momentum(closes)
    assert s == 0.0, f"严重超买应 0 分，实得 {s}"


def test_safety_close_to_ma60():
    closes = pd.Series([50.0] * 60)
    s = scoring.score_safety(closes)
    assert s == 10.0, f"贴线应满分，实得 {s}"


def test_safety_far_above_ma60():
    closes = pd.Series([50.0] * 30 + [80.0] * 30)
    s = scoring.score_safety(closes)
    assert s < 5, f"严重偏离应低分，实得 {s}"


def test_score_one_integration():
    kl = _make_kline(60, trend=0.4, vol_boost=True)
    score = scoring.score_one("000001", "测试", "测试板块", kl, north_change=2.0)
    assert score is not None
    assert score.total > 50, f"健康 K 线综合分应 >50，实得 {score.total}"
    assert score.suggested_stop_loss < score.last_close


def test_score_one_insufficient_data():
    kl = _make_kline(10)
    score = scoring.score_one("000001", "短K", "测试", kl, north_change=None)
    assert score is None, "数据不足应返回 None"


def test_score_total_in_range():
    kl = _make_kline(60)
    score = scoring.score_one("000001", "test", "ind", kl, north_change=5.0)
    assert score is not None
    assert 0 <= score.total <= 100, f"总分应在 0-100，实得 {score.total}"


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
