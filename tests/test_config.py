"""config 模块单元测试 - 不依赖网络。"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config as cfgm


@dataclass
class _FakePick:
    code: str
    name: str
    industry: str
    total: float


def test_load_config_default_when_missing():
    orig = cfgm.CONFIG_PATH
    with tempfile.TemporaryDirectory() as tmp:
        cfgm.CONFIG_PATH = Path(tmp) / "missing.json"
        try:
            c = cfgm.load_config()
            assert c["blacklist"] == {"codes": [], "name_keywords": []}
            assert c["max_per_industry"] == 1
            assert c["max_picks"] == 5
        finally:
            cfgm.CONFIG_PATH = orig


def test_load_config_partial_override():
    orig = cfgm.CONFIG_PATH
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "config.json"
        p.write_text(json.dumps({
            "blacklist": {"codes": [688981, "300433"]},
            "max_per_industry": 3,
        }), encoding="utf-8")
        cfgm.CONFIG_PATH = p
        try:
            c = cfgm.load_config()
            # 代码自动 zfill 6
            assert "688981" in c["blacklist"]["codes"]
            assert "300433" in c["blacklist"]["codes"]
            assert c["blacklist"]["name_keywords"] == []
            assert c["max_per_industry"] == 3
            assert c["max_picks"] == 5  # 默认值
        finally:
            cfgm.CONFIG_PATH = orig


def test_load_config_zfill_short_codes():
    orig = cfgm.CONFIG_PATH
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "config.json"
        # 数字代码 1 应当变成 "000001"
        p.write_text(json.dumps({"blacklist": {"codes": [1]}}), encoding="utf-8")
        cfgm.CONFIG_PATH = p
        try:
            c = cfgm.load_config()
            assert "000001" in c["blacklist"]["codes"]
        finally:
            cfgm.CONFIG_PATH = orig


def test_apply_blacklist_by_code():
    df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台"},
        {"代码": "300433", "名称": "蓝思科技"},
    ])
    out = cfgm.apply_blacklist(df, {"codes": ["300433"], "name_keywords": []})
    assert len(out) == 1
    assert out.iloc[0]["代码"] == "600519"


def test_apply_blacklist_by_keyword():
    df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台"},
        {"代码": "000001", "名称": "*ST退市"},
    ])
    out = cfgm.apply_blacklist(df, {"codes": [], "name_keywords": ["退"]})
    assert len(out) == 1


def test_apply_blacklist_zfill_match():
    """传入短代码（不带前导零）也应能匹配 6 位代码。"""
    df = pd.DataFrame([{"代码": "000001", "名称": "平安银行"}])
    out = cfgm.apply_blacklist(df, {"codes": ["000001"], "name_keywords": []})
    assert out.empty


def test_apply_blacklist_empty_input():
    df = pd.DataFrame([])
    out = cfgm.apply_blacklist(df, {"codes": ["x"], "name_keywords": ["y"]})
    assert out.empty


def test_dedup_keeps_top_score_per_industry():
    picks = [
        _FakePick("A", "n1", "电池", 90),
        _FakePick("B", "n2", "电池", 85),  # 同行业，应被去掉
        _FakePick("C", "n3", "白酒", 80),
        _FakePick("D", "n4", "电池", 75),  # 同行业，应被去掉
    ]
    out = cfgm.dedup_by_industry(picks, max_per_industry=1)
    assert [p.code for p in out] == ["A", "C"]


def test_dedup_max_two_per_industry():
    picks = [
        _FakePick("A", "n1", "电池", 90),
        _FakePick("B", "n2", "电池", 85),
        _FakePick("C", "n3", "电池", 80),  # 第三只电池被去
        _FakePick("D", "n4", "白酒", 75),
    ]
    out = cfgm.dedup_by_industry(picks, max_per_industry=2)
    assert [p.code for p in out] == ["A", "B", "D"]


def test_dedup_unknown_industry_not_capped():
    """'全市场' 标签不参与去重，否则降级路径只会留 1 只。"""
    picks = [
        _FakePick("A", "n1", "全市场", 90),
        _FakePick("B", "n2", "全市场", 85),
        _FakePick("C", "n3", "全市场", 80),
    ]
    out = cfgm.dedup_by_industry(picks, max_per_industry=1)
    assert len(out) == 3, "全市场标签下不应去重"


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
