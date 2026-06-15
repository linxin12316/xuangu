"""数据加载层：封装 akshare + Tushare 双通道。

- akshare：海外 runner 上 spot/kline 走新浪 fallback 还能用，板块/北向/财务全废
- Tushare：海外稳定，但免费 100 积分有接口权限和限速限制，按"全市场每日拉一次"使用

所有对外暴露的函数都带 use_mock 参数，dry-run 时返回内置模拟数据。
"""
from __future__ import annotations

import functools
import io
import os
import time
import warnings
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")


def _retry(times: int = 3, delay: float = 1.5):
    """简单重试装饰器。"""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_err = None
            for i in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if i < times - 1:
                        time.sleep(delay * (i + 1))
            raise last_err  # type: ignore[misc]

        return wrapper

    return deco


# ---------- Tushare 单例 ----------

_TS_PRO = None
_TS_INIT_TRIED = False


def get_tushare():
    """返回 tushare pro_api 实例；token 不存在时返回 None。"""
    global _TS_PRO, _TS_INIT_TRIED
    if _TS_INIT_TRIED:
        return _TS_PRO
    _TS_INIT_TRIED = True
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return None
    try:
        import tushare as ts

        ts.set_token(token)
        _TS_PRO = ts.pro_api()
        return _TS_PRO
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  Tushare 初始化失败: {e}")
        return None


def _to_ts_code(code: str) -> str:
    """6 位代码 → ts_code 格式（600519.SH / 000001.SZ）。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith("4") or code.startswith("8") or code.startswith("92"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _last_trade_date_str() -> str:
    """返回上一个工作日的 YYYYMMDD（够用做 trade_date 默认值）。"""
    d = datetime.now().date()
    # 周一往前找到周五；周日找周五；周六找周五
    if d.weekday() == 0:
        d = d - timedelta(days=3)
    elif d.weekday() == 6:
        d = d - timedelta(days=2)
    elif d.weekday() == 5:
        d = d - timedelta(days=1)
    else:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


# ---------- mock 数据（dry-run 用） ----------

_MOCK_SPOT_CSV = """代码,名称,最新价,涨跌幅,成交额,总市值,市净率
600519,贵州茅台,1680.0,1.2,8500000000,2100000000000,9.5
000858,五粮液,158.3,2.1,3200000000,610000000000,5.8
300750,宁德时代,210.5,3.5,5800000000,950000000000,4.2
002594,比亚迪,265.2,1.8,4100000000,770000000000,3.9
601318,中国平安,48.6,0.5,2200000000,890000000000,1.0
000001,平安银行,11.2,-0.3,1500000000,220000000000,0.6
600036,招商银行,38.5,0.8,1900000000,970000000000,1.0
600900,长江电力,28.6,1.5,1100000000,700000000000,3.5
000333,美的集团,68.5,1.9,1700000000,470000000000,3.2
600276,恒瑞医药,45.2,2.3,1300000000,290000000000,5.1
300059,东方财富,15.8,4.2,3800000000,250000000000,4.0
002415,海康威视,32.1,2.8,2100000000,300000000000,3.8
000725,京东方Α,4.85,3.1,2900000000,180000000000,1.2
600030,中信证券,22.3,1.7,1800000000,330000000000,1.4
601012,隆基绿能,18.5,3.6,1600000000,140000000000,2.0
"""


def _mock_spot() -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(_MOCK_SPOT_CSV))
    return df


def _mock_kline(code: str, days: int = 60) -> pd.DataFrame:
    """生成符合多头排列的合成日线（用于 dry-run）。"""
    import numpy as np

    np.random.seed(int(code[-3:]) if code[-3:].isdigit() else 42)
    base = 50.0
    trend = np.linspace(0, 0.4, days)  # 整体上升 40%
    noise = np.random.normal(0, 0.015, days)
    closes = base * (1 + trend + noise.cumsum() * 0.3)
    closes = closes.clip(min=1.0)
    dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
    df = pd.DataFrame(
        {
            "日期": dates,
            "开盘": closes * 0.99,
            "最高": closes * 1.02,
            "最低": closes * 0.98,
            "收盘": closes,
            "成交量": (1e7 * (1 + trend) + np.random.uniform(0, 5e6, days)).astype(int),
            "成交额": (closes * (1e7 * (1 + trend) + np.random.uniform(0, 5e6, days))).astype(int),
        }
    )
    return df


_MOCK_INDUSTRIES = [
    ("电力设备", 8.5),
    ("电子", 6.2),
    ("汽车", 5.8),
    ("食品饮料", 4.1),
    ("银行", 0.5),
    ("钢铁", -1.2),
]


def _mock_industry_top(top_n: int = 5) -> pd.DataFrame:
    df = pd.DataFrame(_MOCK_INDUSTRIES, columns=["板块名称", "近5日涨幅"])
    return df.head(top_n)


def _mock_industry_cons(industry: str) -> pd.DataFrame:
    spot = _mock_spot()
    return spot.head(5)[["代码", "名称"]]


# ---------- 真实接口封装 ----------


@_retry()
def fetch_spot(use_mock: bool = False) -> pd.DataFrame:
    """全市场 A 股快照。

    优先尝试东方财富 (stock_zh_a_spot_em)，失败回退到新浪 (stock_zh_a_spot)。
    新浪对海外 IP 更友好，适合 GitHub Actions 海外 runner。
    """
    if use_mock:
        return _mock_spot()
    import akshare as ak

    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            return df
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  东方财富快照失败: {e}, 回退新浪源")

    # 新浪源：列名不同，需要标准化
    df = ak.stock_zh_a_spot()
    rename_map = {
        "symbol": "代码",
        "code": "代码",
        "name": "名称",
        "trade": "最新价",
        "changepercent": "涨跌幅",
        "amount": "成交额",
        "mktcap": "总市值",
        "pb": "市净率",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    if "代码" in df.columns:
        # 新浪代码带 sh/sz 前缀，去掉
        df["代码"] = df["代码"].astype(str).str.replace(r"^(sh|sz|bj)", "", regex=True)
    if "总市值" in df.columns:
        # 新浪单位是万元，转换为元
        df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce") * 1e4
    return df


@_retry()
def fetch_kline(code: str, days: int = 60, use_mock: bool = False) -> pd.DataFrame:
    """单只股票日线。优先 Tushare，失败回退东方财富，再回退新浪。

    Tushare daily 接口海外稳定且无限速（仅有总积分限制），是首选。
    """
    if use_mock:
        return _mock_kline(code, days)

    # 优先 Tushare
    pro = get_tushare()
    if pro is not None:
        try:
            ts_code = _to_ts_code(code)
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 2 + 30)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                # Tushare 返回按日期倒序，转为正序
                df = df.sort_values("trade_date").reset_index(drop=True)
                df = df.rename(columns={
                    "trade_date": "日期",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "vol": "成交量",
                    "amount": "成交额",
                })
                df["日期"] = pd.to_datetime(df["日期"])
                # Tushare vol 单位是手，amount 是千元，按内部惯例对齐到 akshare 的"股/元"
                df["成交量"] = df["成交量"] * 100
                df["成交额"] = df["成交额"] * 1000
                return df.tail(days).reset_index(drop=True)
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️  Tushare daily 失败 {code}: {e}, 回退 akshare")

    # akshare 东方财富
    import akshare as ak

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2 + 30)).strftime("%Y%m%d")

    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
        )
        if df is not None and not df.empty:
            return df.tail(days).reset_index(drop=True)
    except Exception:
        pass

    # 新浪源
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", adjust="qfq")
        rename = {"open": "开盘", "high": "最高", "low": "最低",
                  "close": "收盘", "volume": "成交量", "amount": "成交额", "date": "日期"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        return df.tail(days).reset_index(drop=True)
    except Exception as e:
        raise e


@_retry()
def fetch_industry_rank(top_n: int = 5, use_mock: bool = False) -> pd.DataFrame:
    """近 5 日涨幅最强的 N 个板块。

    海外环境下东方财富板块接口经常被拒，失败时返回空 DataFrame，
    上层逻辑会改用 spot 全市场作为候选池。
    Tushare index_classify 需要 2000 积分，免费版不可用。
    """
    if use_mock:
        return _mock_industry_top(top_n)
    import akshare as ak

    try:
        df = ak.stock_board_industry_name_em()
        name_col = "板块名称" if "板块名称" in df.columns else df.columns[1]
        chg_col = next((c for c in df.columns if "涨跌幅" in c or "涨幅" in c), df.columns[-1])
        df = df[[name_col, chg_col]].rename(columns={name_col: "板块名称", chg_col: "近5日涨幅"})
        return df.sort_values("近5日涨幅", ascending=False).head(top_n).reset_index(drop=True)
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  板块涨幅接口失败: {e}, 将跳过板块择强")
        return pd.DataFrame(columns=["板块名称", "近5日涨幅"])


@_retry()
def fetch_industry_cons(industry: str, use_mock: bool = False) -> pd.DataFrame:
    """板块成分股。"""
    if use_mock:
        return _mock_industry_cons(industry)
    import akshare as ak

    try:
        df = ak.stock_board_industry_cons_em(symbol=industry)
        return df[["代码", "名称"]] if "代码" in df.columns else df.iloc[:, :2].rename(
            columns={df.columns[0]: "代码", df.columns[1]: "名称"}
        )
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  板块 {industry} 成分股接口失败: {e}")
        return pd.DataFrame(columns=["代码", "名称"])


def is_trading_day(use_mock: bool = False) -> bool:
    """判断今天是否为 A 股交易日。"""
    if use_mock:
        return True
    try:
        import akshare as ak

        cal = ak.tool_trade_date_hist_sina()
        today = datetime.now().date()
        col = cal.columns[0]
        cal[col] = pd.to_datetime(cal[col]).dt.date
        return today in set(cal[col].tolist())
    except Exception:
        # 接口失败时降级为周一-周五判断
        return datetime.now().weekday() < 5


# ---------- Tushare 全市场批量缓存 ----------
# 这些接口免费版限速 1 次/小时,但单次拉全市场,主流程整轮只调一次
# 失败时也要记住"试过了",避免被 200 只股票反复触发限速

_HK_HOLD_CACHE: Optional[pd.DataFrame] = None
_HK_HOLD_TRIED = False
_DAILY_BASIC_CACHE: Optional[pd.DataFrame] = None
_DAILY_BASIC_TRIED = False
_LIMIT_LIST_CACHE: Optional[pd.DataFrame] = None
_LIMIT_LIST_TRIED = False


def fetch_hk_hold_market(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """全市场北向持股（最近 10 个交易日），按 ts_code 索引。

    返回 DataFrame 包含 ts_code 和最近 5 日持股变动百分比；
    上层用 code -> 变动百分比的字典消费。
    """
    global _HK_HOLD_CACHE, _HK_HOLD_TRIED
    if _HK_HOLD_TRIED:
        return _HK_HOLD_CACHE
    _HK_HOLD_TRIED = True
    if use_mock:
        # mock: 给若干代码生成一个模拟的 5 日持股变动
        import random
        rows = []
        for code in ["600519.SH", "000858.SZ", "300750.SZ", "002594.SZ"]:
            random.seed(hash(code) & 0xffff)
            rows.append({"ts_code": code, "north_5d_change": random.uniform(-2, 4)})
        _HK_HOLD_CACHE = pd.DataFrame(rows)
        return _HK_HOLD_CACHE
    pro = get_tushare()
    if pro is None:
        return None
    try:
        # 拉最近 10 个自然日窗口的全市场北向持股
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
        df = pro.hk_hold(start_date=start, end_date=end)
        if df is None or df.empty:
            print("   ⚠️  Tushare hk_hold 返回空")
            return None
        # 按 ts_code 取最早和最新两个交易日的 vol（持股数），算变化百分比
        df = df.sort_values(["ts_code", "trade_date"])
        agg = df.groupby("ts_code").agg(
            first_vol=("vol", "first"),
            last_vol=("vol", "last"),
        ).reset_index()
        agg["north_5d_change"] = (agg["last_vol"] - agg["first_vol"]) / agg["first_vol"].replace(0, pd.NA) * 100
        _HK_HOLD_CACHE = agg[["ts_code", "north_5d_change"]].dropna()
        print(f"   ✅ Tushare hk_hold 全市场 {len(_HK_HOLD_CACHE)} 只")
        return _HK_HOLD_CACHE
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  Tushare hk_hold 失败: {e}")
        return None


def fetch_daily_basic_market(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """全市场基本面快照（PE / PB / 换手率 / 总市值），按上一个交易日。"""
    global _DAILY_BASIC_CACHE, _DAILY_BASIC_TRIED
    if _DAILY_BASIC_TRIED:
        return _DAILY_BASIC_CACHE
    _DAILY_BASIC_TRIED = True
    if use_mock:
        rows = []
        for code in ["600519.SH", "000858.SZ", "300750.SZ", "002594.SZ"]:
            rows.append({"ts_code": code, "pe_ttm": 25.0, "pb": 4.0,
                         "turnover_rate": 1.5, "total_mv": 5e9})
        _DAILY_BASIC_CACHE = pd.DataFrame(rows)
        return _DAILY_BASIC_CACHE
    pro = get_tushare()
    if pro is None:
        return None
    # 全市场单次拉取最近一个交易日;遇到限速立即停止避免被 ban
    last_err = None
    for offset in range(1, 8):  # 从昨天开始（今天通常无数据）
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            df = pro.daily_basic(trade_date=d, fields="ts_code,pe_ttm,pb,turnover_rate,total_mv,circ_mv")
            if df is not None and not df.empty:
                _DAILY_BASIC_CACHE = df
                print(f"   ✅ Tushare daily_basic {d} 全市场 {len(df)} 只")
                return _DAILY_BASIC_CACHE
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e)
            if "频率" in msg or "频次" in msg:
                print(f"   ⚠️  Tushare daily_basic 限速,放弃: {e}")
                return None
            # 其它错误（无权限、网络）也直接退出
            print(f"   ⚠️  Tushare daily_basic 失败: {e}")
            return None
    print(f"   ⚠️  Tushare daily_basic 7 天内无数据 (last={last_err})")
    return None


def fetch_limit_list_market(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """近 10 个交易日全市场涨停列表（汇总每只代码的涨停次数 + 最高连板数）。"""
    global _LIMIT_LIST_CACHE, _LIMIT_LIST_TRIED
    if _LIMIT_LIST_TRIED:
        return _LIMIT_LIST_CACHE
    _LIMIT_LIST_TRIED = True
    if use_mock:
        rows = [
            {"ts_code": "300750.SZ", "limit_times_10d": 2, "max_streak": 1},
            {"ts_code": "300059.SZ", "limit_times_10d": 3, "max_streak": 2},
        ]
        _LIMIT_LIST_CACHE = pd.DataFrame(rows)
        return _LIMIT_LIST_CACHE
    pro = get_tushare()
    if pro is None:
        return None
    last_err = None
    for offset in range(1, 8):
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            df = pro.limit_list_d(trade_date=d, limit_type="U")
            if df is not None and not df.empty:
                # 按 ts_code 聚合
                streak_col = "limit_times" if "limit_times" in df.columns else "ts_code"
                agg = df.groupby("ts_code").agg(
                    limit_times_10d=("ts_code", "count"),
                    max_streak=(streak_col, "max"),
                ).reset_index()
                _LIMIT_LIST_CACHE = agg
                print(f"   ✅ Tushare limit_list_d {d} 涨停 {len(agg)} 只")
                return _LIMIT_LIST_CACHE
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e)
            if "频率" in msg or "频次" in msg:
                print(f"   ⚠️  Tushare limit_list_d 限速,放弃: {e}")
                return None
            print(f"   ⚠️  Tushare limit_list_d 失败: {e}")
            return None
    print(f"   ⚠️  Tushare limit_list_d 7 天内无数据 (last={last_err})")
    return None


def fetch_north_flow(code: str, use_mock: bool = False) -> Optional[float]:
    """单只股票近 5 日北向持股变动百分比，失败返回 None。

    优先用全市场缓存（Tushare hk_hold），失败回退 akshare 个股查询。
    """
    if use_mock:
        import random

        random.seed(int(code[-3:]) if code[-3:].isdigit() else 0)
        return random.uniform(-2, 4)

    # 优先走 Tushare 全市场缓存
    cache = fetch_hk_hold_market(use_mock=False)
    if cache is not None and not cache.empty:
        ts_code = _to_ts_code(code)
        row = cache[cache["ts_code"] == ts_code]
        if not row.empty:
            return float(row.iloc[0]["north_5d_change"])
        return None  # Tushare 有数据但这只不在内,大概率不是港股通标的

    # 回退 akshare 个股
    try:
        import akshare as ak

        df = ak.stock_hsgt_individual_em(stock=code)
        if df is None or df.empty:
            return None
        col = next((c for c in df.columns if "持股" in c or "比例" in c), None)
        if not col:
            return None
        recent = df[col].tail(5).astype(float)
        if len(recent) < 2:
            return None
        return float(recent.iloc[-1] - recent.iloc[0])
    except Exception:
        return None


def get_stock_factors(code: str, use_mock: bool = False) -> dict:
    """汇总单只股票的新因子数据：PE/PB/换手率 + 涨停次数 + 北向变动。

    所有数据都来自全市场缓存,首次调用时触发拉取,之后 O(1) 查询。
    返回字典中字段缺失时为 None。
    """
    ts_code = _to_ts_code(code)
    out: dict = {
        "pe_ttm": None,
        "pb": None,
        "turnover_rate": None,
        "total_mv": None,
        "limit_times_10d": 0,
        "max_streak": 0,
        "north_5d_change": None,
    }
    db = fetch_daily_basic_market(use_mock=use_mock)
    if db is not None:
        row = db[db["ts_code"] == ts_code]
        if not row.empty:
            r = row.iloc[0]
            for k in ("pe_ttm", "pb", "turnover_rate", "total_mv"):
                if k in r and pd.notna(r[k]):
                    out[k] = float(r[k])
    ll = fetch_limit_list_market(use_mock=use_mock)
    if ll is not None:
        row = ll[ll["ts_code"] == ts_code]
        if not row.empty:
            r = row.iloc[0]
            out["limit_times_10d"] = int(r.get("limit_times_10d", 0) or 0)
            out["max_streak"] = int(r.get("max_streak", 0) or 0)
    hk = fetch_hk_hold_market(use_mock=use_mock)
    if hk is not None:
        row = hk[hk["ts_code"] == ts_code]
        if not row.empty:
            out["north_5d_change"] = float(row.iloc[0]["north_5d_change"])
    return out


def _reset_caches_for_test():
    """单元测试用：重置全市场缓存。"""
    global _HK_HOLD_CACHE, _DAILY_BASIC_CACHE, _LIMIT_LIST_CACHE
    global _HK_HOLD_TRIED, _DAILY_BASIC_TRIED, _LIMIT_LIST_TRIED
    global _INDUSTRY_MAP_CACHE, _INDUSTRY_MAP_TRIED
    _HK_HOLD_CACHE = None
    _DAILY_BASIC_CACHE = None
    _LIMIT_LIST_CACHE = None
    _HK_HOLD_TRIED = False
    _DAILY_BASIC_TRIED = False
    _LIMIT_LIST_TRIED = False
    _INDUSTRY_MAP_CACHE = None
    _INDUSTRY_MAP_TRIED = False


# ---------- 全市场行业映射（Tushare stock_basic, 不限速）----------

_INDUSTRY_MAP_CACHE: Optional[dict] = None
_INDUSTRY_MAP_TRIED = False


def fetch_industry_map(use_mock: bool = False) -> dict:
    """全市场代码→行业映射，{ "600519": "白酒", ... }。

    Tushare stock_basic 接口免费版完全可用，不限速，覆盖 ~5500 只 A 股。
    单次会话内只拉一次。失败时回退空字典（行业字段会显示"全市场"占位）。
    """
    global _INDUSTRY_MAP_CACHE, _INDUSTRY_MAP_TRIED
    if _INDUSTRY_MAP_TRIED:
        return _INDUSTRY_MAP_CACHE or {}
    _INDUSTRY_MAP_TRIED = True
    if use_mock:
        _INDUSTRY_MAP_CACHE = {
            "600519": "白酒", "000858": "白酒",
            "300750": "电池", "002594": "汽车整车",
            "601318": "保险", "000001": "银行", "600036": "银行",
            "600900": "电力", "000333": "家电", "600276": "化学制药",
            "300059": "证券", "002415": "安防设备",
            "000725": "面板", "600030": "证券", "601012": "光伏设备",
        }
        return _INDUSTRY_MAP_CACHE
    pro = get_tushare()
    if pro is None:
        _INDUSTRY_MAP_CACHE = {}
        return {}
    try:
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="symbol,industry")
        if df is None or df.empty:
            _INDUSTRY_MAP_CACHE = {}
            return {}
        df = df.dropna(subset=["industry"])
        _INDUSTRY_MAP_CACHE = dict(zip(df["symbol"].astype(str), df["industry"].astype(str)))
        print(f"   ✅ Tushare stock_basic 行业映射 {len(_INDUSTRY_MAP_CACHE)} 只")
        return _INDUSTRY_MAP_CACHE
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  Tushare stock_basic 失败: {e}")
        _INDUSTRY_MAP_CACHE = {}
        return {}
