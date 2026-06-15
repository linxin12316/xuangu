"""evening 复盘单元测试 - 不依赖网络。"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import report


@dataclass
class _FakePick:
    code: str
    name: str
    industry: str
    total: float
    trend: float = 22.0
    volume: float = 18.0
    momentum: float = 12.0
    fund: float = 10.0
    safety: float = 8.0
    turnover: float = 5.0
    limit_up: float = 0.0
    valuation: float = 5.0
    longhu: float = 5.0
    finance: float = 2.5
    last_close: float = 100.0
    suggested_stop_loss: float = 92.0

    def as_dict(self):
        return {
            "code": self.code, "name": self.name, "industry": self.industry,
            "total": round(self.total, 1),
            "trend": self.trend, "volume": self.volume, "momentum": self.momentum,
            "fund": self.fund, "safety": self.safety, "turnover": self.turnover,
            "limit_up": self.limit_up, "valuation": self.valuation,
            "longhu": self.longhu, "finance": self.finance,
            "last_close": self.last_close,
            "suggested_stop_loss": self.suggested_stop_loss,
        }


def test_evening_basic_structure():
    """渲染必须包含 5 个核心模块标题。"""
    picks = [_FakePick("002428", "云南锗业", "稀有金属", 89.0)]
    md = report.render_evening_report(
        picks=picks,
        risk_score=7, risk_desc="温和上涨",
        north_flow=38.5,
    )
    for keyword in ("# 🌙 晚间复盘", "今日盘面", "明日 Top 1 候选", "注意事项",
                    "云南锗业", "002428", "稀有金属"):
        assert keyword in md, f"缺关键字: {keyword}"


def test_evening_with_all_modules():
    picks = [_FakePick("002428", "云南锗业", "稀有金属", 89.0, last_close=89.38)]
    concept_ff = pd.DataFrame([
        {"行业": "人工智能", "行业-涨跌幅": 5.2, "净额": 28.5, "公司家数": 120, "领涨股": "科大讯飞", "领涨股-涨跌幅": 9.8},
        {"行业": "机器人", "行业-涨跌幅": 4.8, "净额": 18.2, "公司家数": 85, "领涨股": "拓斯达", "领涨股-涨跌幅": 8.5},
    ])
    industry_ff = pd.DataFrame([
        {"行业": "半导体", "行业-涨跌幅": 5.98, "净额": 336.4, "公司家数": 179, "领涨股": "炬光科技"},
    ])
    zt_pool = pd.DataFrame([
        {"代码": "603065", "名称": "宿迁联盛", "连板数": 4, "换手率": 12.5, "所属行业": "化学制品"},
        {"代码": "601958", "名称": "金钼股份", "连板数": 2, "换手率": 5.8, "所属行业": "小金属"},
    ])
    lhb_detail = pd.DataFrame([
        {"代码": "002428", "名称": "云南锗业", "龙虎榜净买额": 5404e4, "解读": "3家机构买入"},
    ])
    md = report.render_evening_report(
        picks=picks, concept_ff=concept_ff, industry_ff=industry_ff,
        zt_pool=zt_pool, lhb_detail=lhb_detail,
        risk_score=7, risk_desc="正常", north_flow=38.5,
    )
    assert "💡 今日热门概念" in md
    assert "💰 主力行业流向" in md
    assert "🚀 今日连板梯队" in md
    assert "🐯 今日龙虎榜净买" in md
    assert "人工智能" in md
    assert "宿迁联盛" in md
    assert "云南锗业" in md
    assert "**4板**" in md, "连板数应高亮"
    # 买点公式：last_close × 1.02
    assert "≤91.17" in md, "买点上限应是 last_close * 1.02"


def test_evening_buy_point_calculation():
    """买点上限正好是 last_close * 1.02，止损取自 suggested_stop_loss。"""
    picks = [_FakePick("000001", "test", "银行", 75.0,
                       last_close=100.0, suggested_stop_loss=93.5)]
    md = report.render_evening_report(picks=picks)
    assert "≤102.00" in md
    assert "止损位**：93.5" in md
    assert "-6.5%" in md, f"止损百分比应是 -6.5%"


def test_evening_empty_picks():
    """空 picks 也不应崩溃，只是没明日候选模块。"""
    md = report.render_evening_report(picks=[])
    assert "# 🌙 晚间复盘" in md
    assert "明日 Top" not in md


def test_evening_risk_indicator():
    """大盘风险评分对应不同图标。"""
    picks = [_FakePick("000001", "x", "y", 50)]
    md_high = report.render_evening_report(picks=picks, risk_score=8, risk_desc="强势")
    md_mid = report.render_evening_report(picks=picks, risk_score=5, risk_desc="中性")
    md_low = report.render_evening_report(picks=picks, risk_score=2, risk_desc="弱势")
    assert "🟢" in md_high
    assert "🟡" in md_mid
    assert "🔴" in md_low


def test_evening_lhb_skipped_when_all_negative():
    """所有 lhb 净买额都为负时，整个龙虎榜模块被跳过。"""
    picks = [_FakePick("000001", "x", "y", 50)]
    lhb_detail = pd.DataFrame([
        {"代码": "002428", "名称": "云南锗业", "龙虎榜净买额": -1e6, "解读": "机构卖出"},
    ])
    md = report.render_evening_report(picks=picks, lhb_detail=lhb_detail)
    assert "🐯 今日龙虎榜净买" not in md, "全部净卖出时不应展示龙虎榜模块"


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
