"""Server 酱推送。"""
from __future__ import annotations

import os
from typing import Optional

import requests


SCT_URL = "https://sctapi.ftqq.com/{key}.send"


def send_to_wechat(title: str, markdown: str, sckey: Optional[str] = None) -> bool:
    """通过 Server 酱发送 Markdown 到微信。

    返回 True 表示推送 API 调用成功（不保证微信端到达）。
    """
    sckey = sckey or os.environ.get("SCKEY")
    if not sckey:
        print("⚠️  未配置 SCKEY 环境变量，跳过推送")
        return False

    if len(title) > 32:
        title = title[:30] + "..."
    if len(markdown) > 32000:
        markdown = markdown[:32000] + "\n\n...(已截断)"

    url = SCT_URL.format(key=sckey)
    try:
        resp = requests.post(
            url,
            data={"title": title, "desp": markdown},
            timeout=30,
        )
        data = resp.json()
        if data.get("code") == 0:
            print(f"✅ 推送成功: {title}")
            return True
        print(f"❌ 推送失败: {data}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"❌ 推送异常: {e}")
        return False
