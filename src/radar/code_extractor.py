"""个股代码抽取 + 黑名单过滤

A 股代码格式（财联社快讯里常见的形式）：
- 600519.SH, 002815.SZ, 688110.SH, 300750.SZ, 8xxxxx.BJ
- (600519), (002815)
- 600519、002815  (无后缀)

黑名单规则：
- 名字含 ST/退
- 创业板 300/301
- 科创板 688
- 北交所 8开头/4开头/92开头
"""
from __future__ import annotations
import re
from typing import List, Set


_CODE_RE = re.compile(r"(?<![\d])(\d{6})(?:\.(?:SH|SZ|BJ))?(?![\d])")


def extract_codes(text: str) -> List[str]:
    """从文本中抽取 A 股代码（6 位数字）。
    返回去重保序的列表，已应用黑名单过滤。
    """
    if not text:
        return []
    raw: List[str] = []
    seen: Set[str] = set()
    for m in _CODE_RE.finditer(text):
        c = m.group(1)
        if c not in seen and _looks_like_stock(c):
            seen.add(c)
            raw.append(c)
    return [c for c in raw if not in_blacklist(c)]


def _looks_like_stock(code: str) -> bool:
    if len(code) != 6 or not code.isdigit():
        return False
    p = code[:3]
    p1 = code[0]
    if p1 in ("4", "8") or code.startswith("92"):
        return True
    if p in ("600", "601", "603", "605", "688", "689", "900",
             "000", "001", "002", "003", "300", "301"):
        return True
    return False


def in_blacklist(code: str) -> bool:
    if not code or len(code) != 6:
        return True
    if code.startswith(("300", "301")):
        return True
    if code.startswith(("688", "689")):
        return True
    if code[0] in ("4", "8") or code.startswith("92"):
        return True
    return False


def is_st_by_name(name: str) -> bool:
    if not name:
        return False
    upper = name.upper()
    return any(t in upper for t in ("ST", "*ST", "退", "退市"))
