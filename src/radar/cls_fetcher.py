"""财联社电报抓取
官方接口（带 sign 校验）：
  https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraphList&os=web&sv=8.7.9&lastTime=&sign=<sign>

签名算法（逆向自前端）：
  sign = md5(sha1(sorted_query_string).hexdigest())
  其中 sorted_query_string = "k1=v1&k2=v2&..." 按 key 字典序

返回字段（关键）:
  data.roll_data[]:
    - id          (int)   唯一 id, 用于去重
    - title       (str)   标题（可能为空）
    - content     (str)   正文
    - ctime       (int)   时间戳（秒）
    - shareurl    (str)   原文链接
    - is_ad       (int)   广告标志
    - level       (str)   "A" = 普通, "B" = 重要
    - reading_num (int)   阅读量
    - red         (int)   1 = 红色重磅
"""
from __future__ import annotations
import hashlib
import time
import requests
from typing import List, Dict


CLS_API = "https://www.cls.cn/api/cache"
SV = "8.7.9"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://www.cls.cn/telegraph",
    "Accept": "application/json",
}


def _sign(params: Dict[str, str]) -> str:
    """财联社签名算法：md5(sha1(sorted_qs))"""
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sha1 = hashlib.sha1(qs.encode()).hexdigest()
    return hashlib.md5(sha1.encode()).hexdigest()


def fetch_telegraph(rn: int = 30, timeout: int = 10) -> List[Dict]:
    """抓取最近 rn 条财联社电报。返回去掉广告后的列表。
    抓不到时返回空列表，不抛异常。
    """
    base_params = {
        "app": "CailianpressWeb",
        "name": "telegraphList",
        "os": "web",
        "sv": SV,
        "lastTime": "",
    }
    params = dict(base_params)
    params["sign"] = _sign(base_params)

    try:
        r = requests.get(CLS_API, params=params, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[radar:cls] fetch failed: {e}")
        return []

    if data.get("errno") != 0:
        print(f"[radar:cls] api error: {data}")
        return []

    rolls = (data.get("data") or {}).get("roll_data") or []
    out: List[Dict] = []
    for it in rolls:
        if it.get("is_ad"):
            continue
        out.append({
            "id": int(it.get("id", 0)),
            "title": (it.get("title") or "").strip(),
            "content": (it.get("content") or "").strip(),
            "ctime": int(it.get("ctime", 0)),
            "url": it.get("shareurl") or f"https://www.cls.cn/detail/{it.get('id')}",
            "level": it.get("level", "A"),
            "is_red": bool(it.get("red")),
            "reading_num": int(it.get("reading_num", 0)),
        })
    out.sort(key=lambda x: x["ctime"], reverse=True)
    return out[:rn]
