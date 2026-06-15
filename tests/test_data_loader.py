"""data_loader 行业 + 市场窗口逻辑测试 - 不依赖网络。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import data_loader as dl


def test_mock_industry_cons_returns_correct_stocks():
    """白酒板块 mock 应该返回茅台和五粮液,而不是 spot 头几只。"""
    dl._reset_caches_for_test()
    cons = dl._mock_industry_cons("白酒")
    codes = cons["代码"].tolist()
    assert "600519" in codes, "白酒应包含茅台"
    assert "000858" in codes, "白酒应包含五粮液"
    # 关键: 茅台不应出现在电力设备里(旧 bug)
    cons_power = dl._mock_industry_cons("电力设备")
    assert "600519" not in cons_power["代码"].tolist(), "茅台不应被错绑到电力设备"


def test_mock_industry_top_returns_real_industry_names():
    """mock 板块名称应该是真实行业(白酒/电池),不是粗粒度的'电力设备/电子'。"""
    dl._reset_caches_for_test()
    top = dl._mock_industry_top(top_n=5)
    names = top["板块名称"].tolist()
    assert "白酒" in names
    assert all(n not in ("电力设备", "电子", "钢铁") for n in names), \
        f"mock 行业不应该包含粗粒度名称: {names}"


def test_industry_map_mock_consistency():
    """fetch_industry_map mock 应能查到茅台→白酒。"""
    dl._reset_caches_for_test()
    m = dl.fetch_industry_map(use_mock=True)
    assert m.get("600519") == "白酒"
    assert m.get("002594") == "汽车整车"
    assert m.get("000001") == "银行"


def test_market_window_mock_has_chg_5d():
    """fetch_market_window mock 必须返回 chg_5d 字段。"""
    dl._reset_caches_for_test()
    w = dl.fetch_market_window(use_mock=True)
    assert w is not None
    for col in ("ts_code", "code", "close_now", "chg_5d", "amount_now"):
        assert col in w.columns, f"缺字段 {col}"
    # 6 位 code
    assert all(len(c) == 6 for c in w["code"]), "code 应为 6 位"


def test_industry_aggregation_logic():
    """模拟行业聚合：保证小行业(<5只)被过滤,大行业按平均涨幅排序。"""
    # 直接构造 fake market window + industry map
    market = pd.DataFrame([
        {"code": f"60000{i}", "chg_5d": 5.0, "amount_now": 1e6} for i in range(10)
    ] + [
        {"code": f"30075{i}", "chg_5d": 8.0, "amount_now": 1e6} for i in range(8)
    ] + [
        {"code": f"00072{i}", "chg_5d": 12.0, "amount_now": 1e6} for i in range(3)  # 小样本
    ])
    ind_map = {f"60000{i}": "银行" for i in range(10)}
    ind_map.update({f"30075{i}": "电池" for i in range(8)})
    ind_map.update({f"00072{i}": "面板" for i in range(3)})

    df = market.copy()
    df["industry"] = df["code"].map(ind_map)
    agg = df.groupby("industry").agg(
        成员数=("code", "count"), 近5日涨幅=("chg_5d", "mean")
    ).reset_index()
    agg = agg[agg["成员数"] >= 5]  # 这是 fetch_industry_rank 里的过滤逻辑

    industries = agg.sort_values("近5日涨幅", ascending=False)["industry"].tolist()
    assert "面板" not in industries, "样本<5的行业应被过滤"
    assert industries[0] == "电池", "8% > 5%, 电池应排首位"
    assert industries[1] == "银行"


def test_candidate_pool_filters_by_hot_industries():
    """候选池构建：只有热门行业的股票才进入。"""
    market = pd.DataFrame([
        {"code": "600519", "chg_5d": 8.0, "amount_now": 1e9},   # 白酒(热门)
        {"code": "000001", "chg_5d": 3.0, "amount_now": 1e9},   # 银行(冷门)
        {"code": "300750", "chg_5d": 10.0, "amount_now": 1e9},  # 电池(热门)
    ])
    ind_map = {"600519": "白酒", "000001": "银行", "300750": "电池"}
    spot_codes = {"600519", "000001", "300750"}
    hot = {"白酒", "电池"}

    df = market.copy()
    df["industry"] = df["code"].map(ind_map)
    df = df[df["industry"].isin(hot)]
    df = df[df["code"].isin(spot_codes)]

    selected = set(df["code"].tolist())
    assert selected == {"600519", "300750"}, f"应只剩白酒和电池里的, 实得 {selected}"


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
