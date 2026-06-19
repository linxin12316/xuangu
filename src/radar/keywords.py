"""关键词 → 板块映射 + 命中评分

设计原则：
- 关键词分级：核心词 (3 分) + 普通词 (1 分)
- 每条快讯算总分，>= THRESHOLD 才推送
- 多板块命中按命中分最高的板块归类，但展示全部命中
- 排除噪音词（业绩报告等常规公告）以降低假阳性

v2: 从 themes.json 加载题材关键词，与 xuangu 保持题材体系一致。
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple


# 关键词 → 板块。核心词放第一个；列表越靠前权重越高。
# 命中即得 1 分；带 "★" 前缀的词算核心词得 3 分。
KEYWORD_SECTOR_MAP: Dict[str, List[str]] = {
    "货币政策": ["★降准", "★降息", "★MLF", "★逆回购", "★LPR", "公开市场", "流动性"],
    "财政政策": ["★专项债", "★特别国债", "★财政赤字", "财政部", "减税降费"],
    "算力/AI": ["★大模型", "★算力", "★GPU", "★HBM", "★AI芯片", "OpenAI", "DeepSeek",
              "Sora", "Grok", "GPT-", "Claude", "推理芯片", "训练芯片", "Scaling Law"],
    "光模块/CPO": ["★CPO", "★光模块", "★800G", "★1.6T", "硅光", "光通信", "光器件"],
    "液冷": ["★液冷", "★浸没式", "数据中心散热", "冷板"],
    "PCB": ["★PCB", "★高多层板", "★HDI", "覆铜板", "CCL"],
    "半导体设备": ["★光刻机", "★刻蚀机", "★薄膜沉积", "EDA", "国产替代", "ASML"],
    "半导体材料": ["★碳化硅", "★氮化镓", "★第三代半导体", "光刻胶", "电子特气"],
    "存储芯片": ["★DRAM", "★NAND", "★存储", "美光", "三星", "海力士"],
    "固态电池": ["★固态电池", "★半固态", "硫化物", "氧化物电解质"],
    "锂电池": ["★宁德时代", "★磷酸铁锂", "三元锂", "动力电池"],
    "光伏": ["★HJT", "★TOPCon", "★钙钛矿", "BC电池", "硅料"],
    "储能": ["★储能", "★工商业储能", "构网型", "虚拟电厂"],
    "核电": ["★核电", "★可控核聚变", "★华龙一号", "国和一号", "ITER"],
    "机器人": ["★人形机器人", "★具身智能", "特斯拉Optimus", "Figure", "宇树", "智元",
              "灵巧手", "丝杠", "减速器"],
    "低空经济": ["★低空经济", "★eVTOL", "★通用航空", "无人机", "亿航", "峰飞"],
    "商业航天": ["★商业航天", "★可回收火箭", "★千帆星座", "★星链", "G60", "GW星座"],
    "军工": ["★军工", "★军贸", "导弹", "战机", "航发"],
    "信创": ["★信创", "★鸿蒙", "★欧拉", "国产操作系统", "麒麟", "统信"],
    "华为": ["★华为", "★Mate", "★Pura", "★鸿蒙", "麒麟芯片", "昇腾", "鲲鹏"],
    "有色": ["★铜", "★黄金", "★白银", "锂", "稀土", "钨", "钼"],
    "煤炭": ["★煤炭", "动力煤", "焦煤", "发改委煤"],
    "创新药": ["★创新药", "★ADC", "★GLP-1", "减肥药", "司美格鲁肽", "BD出海"],
    "医疗器械": ["★医疗器械", "高值耗材", "集采"],
    "并购重组": ["★并购重组", "★借壳", "★控制权变更", "重大资产重组", "吸收合并"],
    "资本市场改革": ["★注册制", "★退市", "★IPO", "新国九条", "市值管理"],
    "新质生产力": ["★新质生产力", "★未来产业"],
    "数据要素": ["★数据要素", "★数据资产", "数据交易所"],
    "一带一路": ["★一带一路", "出海", "中欧班列", "亚投行"],
    "雄安": ["★雄安"],
    "海南": ["★海南自贸", "★封关"],
}

NOISE_PATTERNS = [
    r"业绩快报", r"业绩预告", r"分红派息", r"股权激励", r"股东增持",
    r"股东减持", r"董事会换届", r"独立董事", r"会计师事务所",
    r"投资者关系活动", r"\d+月\d+日.*接受.*调研",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS))


def load_themes_from_xuangu() -> dict:
    """从 xuangu/themes.json 加载题材关键词，与 static KEYWORD_SECTOR_MAP 融合。

    返回 {题材名: [关键词列表]}，带 ★ 前缀的核心词。
    themes.json 中的 keywords 字段全部视为核心词（★）。
    """
    target = os.path.join(
        Path(__file__).resolve().parent.parent.parent,
        "themes.json",
    )
    if not os.path.exists(target):
        target = os.path.join(str(Path.home()), "xuangu", "themes.json")
    if not os.path.exists(target):
        return {}

    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        out = {}
        for t in data.get("themes", []):
            name = t.get("name", "")
            kws = t.get("keywords", [])
            if name and kws:
                out[name] = [f"★{kw}" for kw in kws]
        return out
    except Exception:
        return {}


_THEMES_LOADED = False


def get_merged_keyword_map() -> Dict[str, List[str]]:
    """返回 KEYWORD_SECTOR_MAP + themes.json 融合后的完整关键词图。"""
    global _THEMES_LOADED
    merged = dict(KEYWORD_SECTOR_MAP)
    themes = load_themes_from_xuangu()
    for name, kws in themes.items():
        if name not in merged:
            merged[name] = kws
    return merged


THRESHOLD = 3
RED_ALWAYS_PUSH = True


def score_news(text: str) -> Tuple[int, List[Tuple[str, List[str]]]]:
    """对一条新闻文本评分。
    返回 (score, hits) 其中 hits 是 [(板块, [命中词...])] 列表。
    """
    score = 0
    hits: List[Tuple[str, List[str]]] = []

    noise_penalty = 0
    if NOISE_RE.search(text):
        noise_penalty = 2

    keyword_map = get_merged_keyword_map()
    for sector, keywords in keyword_map.items():
        sector_hits: List[str] = []
        sector_score = 0
        for kw in keywords:
            is_core = kw.startswith("★")
            kw_clean = kw.lstrip("★")
            if kw_clean in text:
                sector_hits.append(kw_clean)
                sector_score += 3 if is_core else 1
        if sector_hits:
            hits.append((sector, sector_hits))
            score += sector_score

    score = max(0, score - noise_penalty)
    return score, hits


def should_push(news: Dict) -> Tuple[bool, int, List[Tuple[str, List[str]]]]:
    """判断一条新闻是否应该推送。
    返回 (是否推送, 分数, 命中详情)
    """
    text = (news.get("title") or "") + " " + (news.get("content") or "")
    score, hits = score_news(text)

    if news.get("is_red") and hits and RED_ALWAYS_PUSH:
        return True, score + 5, hits

    if score >= THRESHOLD and hits:
        return True, score, hits

    return False, score, hits
