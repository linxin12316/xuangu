"""backtest 模块基本单元测试 - 不依赖网络。

只测纯函数 + render_report 的格式，不测 _price_after / _benchmark_chg
（这俩需要网络）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import backtest


def test_render_with_no_data():
    md = backtest.render_report({"error": "test no data", "picks": []})
    assert "test no data" in md
    assert md.startswith("# ⚠️")


def test_render_with_full_stats():
    stats = {
        "period": "2026-06-10 ~ 2026-06-12",
        "total_picks": 15,
        "by_window": {
            "1d": {
                "count": 15, "win": 9, "win_rate": 60.0,
                "avg_return": 0.85, "max_gain": 4.2, "max_loss": -2.1,
                "alpha_avg": 0.3, "beat_bench_rate": 53.3,
            },
            "5d": {
                "count": 15, "win": 8, "win_rate": 53.3,
                "avg_return": 1.5, "max_gain": 8.5, "max_loss": -5.2,
                "alpha_avg": -0.5, "beat_bench_rate": 46.7,
            },
        },
        "best": {"code": "300750", "name": "宁德时代", "date": "2026-06-10", "chg_5d": 8.5},
        "worst": {"code": "600276", "name": "恒瑞医药", "date": "2026-06-11", "chg_5d": -5.2},
    }
    md = backtest.render_report(stats)
    assert "60.0%" in md, "1日胜率应出现"
    assert "宁德时代" in md, "最佳应展示"
    assert "恒瑞医药" in md, "最差应展示"
    assert "跑赢沪深300" in md
    assert "α" in md, "alpha 应在表格中"


def test_load_recent_picks_empty(tmp_path=None):
    """目录不存在或为空时不崩溃。"""
    # 通过临时挂掉 PICKS_DIR
    orig = backtest.PICKS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        backtest.PICKS_DIR = Path(tmp)
        try:
            r = backtest._load_recent_picks(days_back=14)
            assert r == []
        finally:
            backtest.PICKS_DIR = orig


def test_load_recent_picks_filter_by_days():
    """days_back 控制窗口内文件被加载。"""
    orig = backtest.PICKS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 1990 年的远古记录,无论 days_back 多大都应被排除
        (d / "1990-01-01.json").write_text(
            json.dumps([{"code": "000001", "name": "a", "total": 80, "last_close": 10}]),
            encoding="utf-8",
        )
        backtest.PICKS_DIR = d
        try:
            r = backtest._load_recent_picks(days_back=14)
            assert r == [], "远古记录应被过滤"
        finally:
            backtest.PICKS_DIR = orig


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
