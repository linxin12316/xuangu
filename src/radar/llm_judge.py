"""LLM 点评模块（DeepSeek API）

对每条命中快讯做：利好/利空判断 + 强度 1-5 + 一句话摘要 + 矛盾信号识别。

返回字段：
  sentiment: 利好/利空/中性/矛盾
  strength:  1-5  (1=噪音, 3=普通, 5=重磅)
  summary:   一句话(≤30字)
  tags:      [扩产/中标/政策/辟谣/重组/...]
  concern:   需警惕的点（可选，矛盾信号时填）
"""
from __future__ import annotations
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests


DEEPSEEK_API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 30
LLM_CONCURRENCY = 4


SYSTEM_PROMPT = """你是 A 股资讯快速点评专家，擅长识别消息对股价的实际影响。

你的任务：对一条财联社快讯输出结构化点评。

判断维度：
1. sentiment（情绪）: 利好/利空/中性/矛盾
   - "矛盾"专指：公司主动辟谣题材但股价仍在炒作、消息表面利好但暗藏减持/质押等
2. strength（强度 1-5）:
   - 1: 常规公告/噪音/低关注度
   - 2: 单家公司经营性事件
   - 3: 行业级别变化/公司重大订单
   - 4: 政策催化/板块景气度反转/重组停牌
   - 5: 货币政策、监管表态、龙头巨变（一年级别罕见）
3. summary: 30 字以内，提炼关键信息
4. tags: 1-3 个标签，从这些里选：扩产/中标/订单/重组/控制权变更/减持/增持/业绩/政策/监管/辟谣/异动/融资/合作/其他
5. concern: 仅在 sentiment="矛盾" 或 strength<=2 但情绪炒作明显时填，30 字内

严格输出 JSON：{"sentiment":"...","strength":N,"summary":"...","tags":[...],"concern":"..."}
不要包含其他文字。"""


def evaluate_news(news_text: str, api_key: Optional[str] = None,
                  retry: int = 1) -> Optional[Dict]:
    """对一条快讯打分。失败返回 None。"""
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"快讯：{news_text}"},
        ],
        "temperature": 0.2,
        "max_tokens": 250,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    for attempt in range(retry + 1):
        try:
            r = requests.post(DEEPSEEK_API, json=body, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            return {
                "sentiment": result.get("sentiment", "中性"),
                "strength": int(result.get("strength", 3)),
                "summary": (result.get("summary") or "")[:50],
                "tags": result.get("tags") or [],
                "concern": (result.get("concern") or "")[:60],
            }
        except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[radar:llm] attempt {attempt + 1} failed: {e}")
            if attempt < retry:
                time.sleep(2)
                continue
            return None
    return None


def evaluate_batch(items: List[Dict]) -> List[Dict]:
    """对一批快讯并发打分。在 item 上挂 _llm 字段。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not items or not api_key:
        return items

    def _judge_one(it: Dict) -> Dict:
        text = (it.get("title") or "") + " " + (it.get("content") or "")
        text = text.strip()[:1000]
        it["_llm"] = evaluate_news(text, api_key=api_key)
        return it

    if len(items) <= 1:
        for it in items:
            _judge_one(it)
        return items

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(LLM_CONCURRENCY, len(items))) as pool:
        list(pool.map(_judge_one, items))
    print(f"  [radar:llm] 并发评 {len(items)} 条耗时 {time.time() - t0:.1f}s")
    return items
