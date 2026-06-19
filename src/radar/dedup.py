"""去重：用 radar_data/seen_ids.json 记录已推送的快讯 id。
保留最近 2000 条，避免文件无限增长。
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Set

SEEN_FILE = os.path.join(
    Path(__file__).resolve().parent.parent.parent,
    "radar_data",
    "seen_ids.json",
)
MAX_KEEP = 2000


def load_seen() -> Set[int]:
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(ids: Set[int]) -> None:
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    keep = sorted(set(ids), reverse=True)[:MAX_KEEP]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(keep), f, ensure_ascii=False)
