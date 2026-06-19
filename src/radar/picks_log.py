"""推送记录持久化（事后回测的数据源）

每次真实推送后，把命中的快讯 + 个股写入 picks/YYYY-MM-DD.jsonl。
"""
from __future__ import annotations
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict


PICKS_DIR = os.path.join(Path(__file__).resolve().parent.parent.parent, "picks")


def record_picks(items: List[Dict]) -> str:
    """把命中的快讯写入 picks/<date>.jsonl，返回文件路径。"""
    if not items:
        return ""
    os.makedirs(PICKS_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(PICKS_DIR, f"{date_str}-radar.jsonl")

    pushed_at = int(time.time())
    appended = 0
    with open(path, "a", encoding="utf-8") as f:
        for it in items:
            record = {
                "id": it.get("id"),
                "ts": it.get("ctime"),
                "pushed_at": pushed_at,
                "title": it.get("title", ""),
                "content": (it.get("content") or "")[:500],
                "is_red": bool(it.get("is_red")),
                "score": it.get("_score"),
                "sectors": [s for s, _ in (it.get("_hits") or [])],
                "keywords": list({kw for _, kws in (it.get("_hits") or []) for kw in kws}),
                "llm": it.get("_llm"),
                "stocks": [
                    {
                        "code": s["code"],
                        "name": s["name"],
                        "price_at_push": s.get("price"),
                        "change_pct_at_push": s.get("change_pct"),
                    }
                    for s in (it.get("_stocks") or [])
                ],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            appended += 1
    print(f"[radar:picks] 写入 {appended} 条 → {os.path.basename(path)}")
    return path
