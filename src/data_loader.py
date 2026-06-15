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
    ("白酒", 8.5),
    ("电池", 6.2),
    ("汽车整车", 5.8),
    ("证券", 4.1),
    ("银行", 0.5),
    ("化学制药", -1.2),
]


def _mock_industry_top(top_n: int = 5) -> pd.DataFrame:
    df = pd.DataFrame(_MOCK_INDUSTRIES, columns=["板块名称", "近5日涨幅"])
    df["成员数"] = 8  # 占位
    return df.head(top_n)


# mock 行业 → 该行业的 mock 代码（与 fetch_industry_map 的 mock 数据保持一致）
_MOCK_INDUSTRY_CONS = {
    "白酒": [("600519", "贵州茅台"), ("000858", "五粮液")],
    "电池": [("300750", "宁德时代")],
    "汽车整车": [("002594", "比亚迪")],
    "保险": [("601318", "中国平安")],
    "银行": [("000001", "平安银行"), ("600036", "招商银行")],
    "电力": [("600900", "长江电力")],
    "家电": [("000333", "美的集团")],
    "化学制药": [("600276", "恒瑞医药")],
    "证券": [("300059", "东方财富"), ("600030", "中信证券")],
    "安防设备": [("002415", "海康威视")],
    "面板": [("000725", "京东方Α")],
    "光伏设备": [("601012", "隆基绿能")],
}


def _mock_industry_cons(industry: str) -> pd.DataFrame:
    items = _MOCK_INDUSTRY_CONS.get(industry, [])
    if not items:
        return pd.DataFrame(columns=["代码", "名称"])
    return pd.DataFrame(items, columns=["代码", "名称"])


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

    优先用 Tushare 聚合数据（fetch_market_window + fetch_industry_map）：
    全市场每只股票的 5 日涨幅按 stock_basic 行业聚合 → 取行业平均涨幅 Top N。
    要求每个行业至少有 5 只股票，避免小样本噪音。

    Tushare 路径不可用时回退东方财富板块接口（akshare），海外环境通常不通。
    都失败时返回空 DataFrame，由 main.py 触发"全市场降级"。
    """
    if use_mock:
        return _mock_industry_top(top_n)

    # 优先 Tushare 聚合
    window = fetch_market_window(use_mock=False)
    ind_map = fetch_industry_map(use_mock=False)
    if window is not None and not window.empty and ind_map:
        df = window.copy()
        df["industry"] = df["code"].map(ind_map)
        df = df.dropna(subset=["industry", "chg_5d"])
        if not df.empty:
            agg = (
                df.groupby("industry")
                  .agg(成员数=("code", "count"), 近5日涨幅=("chg_5d", "mean"))
                  .reset_index()
            )
            # 过滤小样本: 至少 5 只成分股,避免小行业被极端值带偏
            agg = agg[agg["成员数"] >= 5]
            if not agg.empty:
                agg = agg.sort_values("近5日涨幅", ascending=False).head(top_n)
                agg = agg[["industry", "近5日涨幅", "成员数"]].rename(columns={"industry": "板块名称"})
                print(f"   ✅ Tushare 行业择强 Top {top_n}: {agg['板块名称'].tolist()}")
                return agg.reset_index(drop=True)

    # 回退 akshare
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
    """板块成分股。优先 Tushare 行业映射,失败回退 akshare。"""
    if use_mock:
        return _mock_industry_cons(industry)

    # 优先用 Tushare stock_basic 行业聚合
    ind_map = fetch_industry_map(use_mock=False)
    if ind_map:
        codes = [c for c, ind in ind_map.items() if ind == industry]
        if codes:
            return pd.DataFrame({"代码": codes, "名称": ["" for _ in codes]})

    # 回退 akshare
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
    global _MARKET_WINDOW_CACHE, _MARKET_WINDOW_TRIED
    global _CONCEPT_FUNDFLOW_CACHE, _CONCEPT_FUNDFLOW_TRIED
    global _INDUSTRY_FUNDFLOW_CACHE, _INDUSTRY_FUNDFLOW_TRIED
    global _ZT_POOL_CACHE, _ZT_POOL_TRIED
    global _LHB_DETAIL_CACHE, _LHB_DETAIL_TRIED
    _HK_HOLD_CACHE = None
    _DAILY_BASIC_CACHE = None
    _LIMIT_LIST_CACHE = None
    _HK_HOLD_TRIED = False
    _DAILY_BASIC_TRIED = False
    _LIMIT_LIST_TRIED = False
    _INDUSTRY_MAP_CACHE = None
    _INDUSTRY_MAP_TRIED = False
    _MARKET_WINDOW_CACHE = None
    _MARKET_WINDOW_TRIED = False
    _CONCEPT_FUNDFLOW_CACHE = None
    _CONCEPT_FUNDFLOW_TRIED = False
    _INDUSTRY_FUNDFLOW_CACHE = None
    _INDUSTRY_FUNDFLOW_TRIED = False
    _ZT_POOL_CACHE = None
    _ZT_POOL_TRIED = False
    _LHB_DETAIL_CACHE = None
    _LHB_DETAIL_TRIED = False


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


# ---------- 全市场近 N 日行情快照（Tushare daily by trade_date）----------

_MARKET_WINDOW_CACHE: Optional[pd.DataFrame] = None
_MARKET_WINDOW_TRIED = False


def fetch_market_window(days: int = 6, use_mock: bool = False) -> Optional[pd.DataFrame]:
    """近 days 个交易日的全市场行情，用于行业择强 + 个股趋势排序。

    数据组织：每只股票一行，字段含 ts_code, code (6位), close_now (最新收盘),
    chg_5d (5 日累计涨幅 %), amount_now (最新成交额 千元)。

    Tushare daily(trade_date=...) 一次返回 5500 只全市场，6 次调用 ≈ 6 秒。
    50 次/分钟限速绰绰有余。
    """
    global _MARKET_WINDOW_CACHE, _MARKET_WINDOW_TRIED
    if _MARKET_WINDOW_TRIED:
        return _MARKET_WINDOW_CACHE
    _MARKET_WINDOW_TRIED = True

    if use_mock:
        # mock 用 spot CSV 凑出近似窗口
        spot = _mock_spot()
        rows = []
        for _, r in spot.iterrows():
            code = str(r["代码"]).zfill(6)
            close = float(r["最新价"])
            # 假设 5 日累计 = 当日涨跌幅 × 4（粗略 mock）
            chg_5d = float(r.get("涨跌幅", 0)) * 4
            rows.append({
                "ts_code": _to_ts_code(code), "code": code,
                "close_now": close, "chg_5d": chg_5d,
                "amount_now": float(r.get("成交额", 0)) / 1000,
            })
        _MARKET_WINDOW_CACHE = pd.DataFrame(rows)
        return _MARKET_WINDOW_CACHE

    pro = get_tushare()
    if pro is None:
        return None

    # 拉取近 days*2 个自然日内的所有交易日数据，确保拿到 days 个有效交易日
    daily_frames: list[pd.DataFrame] = []
    last_err = None
    offset = 1  # 从昨天开始（今天可能还没收盘）
    while len(daily_frames) < days and offset <= days * 2 + 5:
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        offset += 1
        try:
            df = pro.daily(trade_date=d)
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e)
            if "频率" in msg or "频次" in msg:
                print(f"   ⚠️  Tushare daily 限速,放弃: {e}")
                return None
            print(f"   ⚠️  Tushare daily {d} 失败: {e}")
            continue
        if df is not None and not df.empty:
            daily_frames.append(df)

    if len(daily_frames) < 2:
        print(f"   ⚠️  Tushare 全市场窗口数据不足 (got {len(daily_frames)}, last_err={last_err})")
        return None

    # 由近到远排：daily_frames[0] 是最新交易日
    latest = daily_frames[0]
    earliest = daily_frames[-1]
    print(f"   ✅ Tushare 全市场窗口: {earliest['trade_date'].iloc[0]} → {latest['trade_date'].iloc[0]} ({len(daily_frames)} 个交易日)")

    # 用最新一天 + 最早一天计算累计涨幅
    merged = latest[["ts_code", "close", "amount"]].rename(
        columns={"close": "close_now", "amount": "amount_now"}
    ).merge(
        earliest[["ts_code", "close"]].rename(columns={"close": "close_start"}),
        on="ts_code", how="inner",
    )
    merged["chg_5d"] = (merged["close_now"] - merged["close_start"]) / merged["close_start"] * 100
    # 6 位 code（去掉 .SH/.SZ/.BJ）
    merged["code"] = merged["ts_code"].astype(str).str.split(".").str[0]
    _MARKET_WINDOW_CACHE = merged[["ts_code", "code", "close_now", "chg_5d", "amount_now"]]
    return _MARKET_WINDOW_CACHE


# ---------- 同花顺资金流 + 涨停池 + 龙虎榜（akshare 海外可用源）----------

_CONCEPT_FUNDFLOW_CACHE: Optional[pd.DataFrame] = None
_CONCEPT_FUNDFLOW_TRIED = False
_INDUSTRY_FUNDFLOW_CACHE: Optional[pd.DataFrame] = None
_INDUSTRY_FUNDFLOW_TRIED = False
_ZT_POOL_CACHE: Optional[pd.DataFrame] = None
_ZT_POOL_TRIED = False
_LHB_DETAIL_CACHE: Optional[pd.DataFrame] = None
_LHB_DETAIL_TRIED = False


def fetch_concept_fundflow(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """同花顺概念资金流（385 个概念，按净流入排序）。

    返回字段: 行业(概念名), 行业指数, 行业-涨跌幅, 流入资金, 流出资金, 净额, 公司家数, 领涨股, 领涨股-涨跌幅, 当前价
    """
    global _CONCEPT_FUNDFLOW_CACHE, _CONCEPT_FUNDFLOW_TRIED
    if _CONCEPT_FUNDFLOW_TRIED:
        return _CONCEPT_FUNDFLOW_CACHE
    _CONCEPT_FUNDFLOW_TRIED = True
    if use_mock:
        _CONCEPT_FUNDFLOW_CACHE = pd.DataFrame([
            {"行业": "人工智能", "行业-涨跌幅": 5.2, "净额": 28.5, "公司家数": 120, "领涨股": "科大讯飞", "领涨股-涨跌幅": 9.8},
            {"行业": "机器人", "行业-涨跌幅": 4.8, "净额": 18.2, "公司家数": 85, "领涨股": "拓斯达", "领涨股-涨跌幅": 8.5},
            {"行业": "PCB概念", "行业-涨跌幅": 3.9, "净额": 15.6, "公司家数": 90, "领涨股": "胜宏科技", "领涨股-涨跌幅": 6.2},
            {"行业": "低空经济", "行业-涨跌幅": 3.5, "净额": 12.3, "公司家数": 50, "领涨股": "中信海直", "领涨股-涨跌幅": 7.1},
        ])
        return _CONCEPT_FUNDFLOW_CACHE
    try:
        import akshare as ak
        df = ak.stock_fund_flow_concept(symbol="即时")
        if df is None or df.empty:
            return None
        # 净额单位是亿,排序按净额
        df["净额"] = pd.to_numeric(df["净额"], errors="coerce").fillna(0)
        df = df.sort_values("净额", ascending=False).reset_index(drop=True)
        _CONCEPT_FUNDFLOW_CACHE = df
        print(f"   ✅ 同花顺概念资金流 {len(df)} 个概念")
        return _CONCEPT_FUNDFLOW_CACHE
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  概念资金流失败: {e}")
        return None


def fetch_industry_fundflow(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """同花顺行业资金流（90 个行业，按净流入排序）。"""
    global _INDUSTRY_FUNDFLOW_CACHE, _INDUSTRY_FUNDFLOW_TRIED
    if _INDUSTRY_FUNDFLOW_TRIED:
        return _INDUSTRY_FUNDFLOW_CACHE
    _INDUSTRY_FUNDFLOW_TRIED = True
    if use_mock:
        _INDUSTRY_FUNDFLOW_CACHE = pd.DataFrame([
            {"行业": "元件", "行业-涨跌幅": 4.5, "净额": 35.2, "公司家数": 62, "领涨股": "达利凯普", "领涨股-涨跌幅": 12.0},
            {"行业": "电子化学品", "行业-涨跌幅": 3.8, "净额": 22.1, "公司家数": 42, "领涨股": "天承科技", "领涨股-涨跌幅": 9.5},
            {"行业": "白酒", "行业-涨跌幅": 1.2, "净额": 8.5, "公司家数": 18, "领涨股": "贵州茅台", "领涨股-涨跌幅": 2.0},
        ])
        return _INDUSTRY_FUNDFLOW_CACHE
    try:
        import akshare as ak
        df = ak.stock_fund_flow_industry(symbol="即时")
        if df is None or df.empty:
            return None
        df["净额"] = pd.to_numeric(df["净额"], errors="coerce").fillna(0)
        df = df.sort_values("净额", ascending=False).reset_index(drop=True)
        _INDUSTRY_FUNDFLOW_CACHE = df
        print(f"   ✅ 同花顺行业资金流 {len(df)} 个行业")
        return _INDUSTRY_FUNDFLOW_CACHE
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  行业资金流失败: {e}")
        return None


def fetch_zt_pool(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """昨日涨停池（东方财富，含连板高度、所属行业、封板时间）。

    返回字段含: 代码, 名称, 涨跌幅, 最新价, 成交额, 流通市值, 总市值, 换手率, 封板资金,
               首次封板时间, 最后封板时间, 炸板次数, 涨停统计, 连板数, 所属行业
    """
    global _ZT_POOL_CACHE, _ZT_POOL_TRIED
    if _ZT_POOL_TRIED:
        return _ZT_POOL_CACHE
    _ZT_POOL_TRIED = True
    if use_mock:
        _ZT_POOL_CACHE = pd.DataFrame([
            {"代码": "603065", "名称": "宿迁联盛", "连板数": 4, "涨停统计": "8/5", "所属行业": "化学制品", "换手率": 12.5},
            {"代码": "000768", "名称": "中航西飞", "连板数": 1, "涨停统计": "1/1", "所属行业": "航空装备", "换手率": 3.5},
            {"代码": "601958", "名称": "金钼股份", "连板数": 2, "涨停统计": "2/2", "所属行业": "小金属", "换手率": 5.8},
        ])
        return _ZT_POOL_CACHE
    try:
        import akshare as ak
        # 取昨天作为涨停日（A 股盘前推送，今日涨停还没产生）
        for offset in range(1, 6):
            d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = ak.stock_zt_pool_em(date=d)
            except Exception:
                df = None
            if df is not None and not df.empty:
                _ZT_POOL_CACHE = df
                print(f"   ✅ 涨停池 {d} {len(df)} 只")
                return _ZT_POOL_CACHE
        print("   ⚠️  涨停池近 5 天无数据")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  涨停池失败: {e}")
        return None


def fetch_lhb_detail(use_mock: bool = False) -> Optional[pd.DataFrame]:
    """昨日龙虎榜详情。

    返回字段含: 代码, 名称, 上榜日, 解读, 收盘价, 涨跌幅, 龙虎榜净买额, 龙虎榜买入额,
               龙虎榜卖出额, 净买额占总成交比, 上榜原因, 上榜后1日/2日/5日/10日
    """
    global _LHB_DETAIL_CACHE, _LHB_DETAIL_TRIED
    if _LHB_DETAIL_TRIED:
        return _LHB_DETAIL_CACHE
    _LHB_DETAIL_TRIED = True
    if use_mock:
        _LHB_DETAIL_CACHE = pd.DataFrame([
            {"代码": "603065", "名称": "宿迁联盛", "解读": "知名游资买入",
             "龙虎榜净买额": 2.5e8, "上榜原因": "日涨幅偏离值达7%"},
            {"代码": "000768", "名称": "中航西飞", "解读": "机构买入",
             "龙虎榜净买额": 1.2e8, "上榜原因": "日涨幅偏离值达7%"},
        ])
        return _LHB_DETAIL_CACHE
    try:
        import akshare as ak
        for offset in range(1, 6):
            d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = ak.stock_lhb_detail_em(start_date=d, end_date=d)
            except Exception:
                df = None
            if df is not None and not df.empty:
                _LHB_DETAIL_CACHE = df
                print(f"   ✅ 龙虎榜 {d} {len(df)} 行")
                return _LHB_DETAIL_CACHE
        print("   ⚠️  龙虎榜近 5 天无数据")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️  龙虎榜失败: {e}")
        return None


def get_stock_market_signals(code: str, use_mock: bool = False) -> dict:
    """汇总单只股票的市场情绪信号：涨停连板数、是否上龙虎榜+净买额。

    所有数据来自全市场缓存，首次调用时触发拉取，之后 O(1) 查询。
    """
    out = {
        "zt_streak": 0,        # 连板数 (来自 zt_pool 的"连板数"列)
        "lhb_net_buy": None,   # 龙虎榜净买额 (元),正数=游资/机构净买入
    }
    code6 = str(code).zfill(6)

    zt = fetch_zt_pool(use_mock=use_mock)
    if zt is not None and not zt.empty and "代码" in zt.columns:
        row = zt[zt["代码"].astype(str).str.zfill(6) == code6]
        if not row.empty:
            try:
                out["zt_streak"] = int(row.iloc[0].get("连板数", 0) or 0)
            except (ValueError, TypeError):
                out["zt_streak"] = 0

    lhb = fetch_lhb_detail(use_mock=use_mock)
    if lhb is not None and not lhb.empty and "代码" in lhb.columns:
        row = lhb[lhb["代码"].astype(str).str.zfill(6) == code6]
        if not row.empty:
            try:
                # 同一只股票可能上榜多行(同日多个原因),取累计净买
                out["lhb_net_buy"] = float(row["龙虎榜净买额"].fillna(0).sum())
            except Exception:
                pass

    return out
