"""radar_runner — 资讯雷达推送主逻辑

融入 xuangu 体系，复用 src/notifier.py 和 src/data_loader.py。

用法：
  python -m src.radar_runner           # 真实运行
  python -m src.radar_runner --dry-run # mock 不推送
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Set

from .radar.cls_fetcher import fetch_telegraph
from .radar.keywords import should_push
from .radar.dedup import load_seen, save_seen
from .radar.code_extractor import extract_codes, in_blacklist, is_st_by_name
from .radar.sector_leaders import get_leaders
from .radar.llm_judge import evaluate_batch
from .radar.picks_log import record_picks
from .notifier import send_to_wechat

# 每条快讯展示的最大个股数
MAX_STOCKS_PER_NEWS = 5


def _collect_codes(news: Dict) -> List[str]:
    """对一条命中新闻，收集相关个股代码。去重保序，已过黑名单。"""
    text = (news.get("title") or "") + " " + (news.get("content") or "")
    codes: List[str] = []
    seen: Set[str] = set()

    for c in extract_codes(text):
        if c not in seen:
            codes.append(c)
            seen.add(c)

    only_from_text = bool(codes)
    for sector, _kws in news.get("_hits", []):
        leaders = get_leaders(sector)
        for c in leaders:
            if c in seen:
                continue
            limit_per_sector = 2 if only_from_text else MAX_STOCKS_PER_NEWS
            if sum(1 for x in codes if x in get_leaders(sector)) >= limit_per_sector:
                break
            codes.append(c)
            seen.add(c)

    return codes[: MAX_STOCKS_PER_NEWS * 2]


def _enrich_with_quotes(items: List[Dict]) -> List[Dict]:
    """批量补腾讯行情，挂到 item['_stocks']。复用 data_loader.enrich_with_tencent。"""
    from .data_loader import enrich_with_tencent

    all_codes: Set[str] = set()
    for it in items:
        codes = _collect_codes(it)
        it["_codes"] = codes
        all_codes.update(codes)

    if not all_codes:
        for it in items:
            it["_stocks"] = []
        return items

    quotes = enrich_with_tencent(sorted(all_codes))
    print(f"  [radar] 腾讯行情 {len(quotes)}/{len(all_codes)} 只")

    for it in items:
        stocks = []
        for c in it["_codes"]:
            q = quotes.get(c)
            if not q or q.get("price", 0) <= 0:
                continue
            if is_st_by_name(q.get("name", "")):
                continue
            stocks.append({"code": c, **q})
        stocks.sort(key=lambda s: (s["change_pct"], s["vol_ratio"]), reverse=True)
        it["_stocks"] = stocks[:MAX_STOCKS_PER_NEWS]
    return items


def _judge_technical(s: dict) -> str:
    """轻量技术研判标签（≤6 字符）。"""
    chg = s.get("change_pct", 0) or 0
    vol = s.get("vol_ratio", 1.0) or 1.0
    amp = s.get("amplitude_pct", 0) or 0

    if chg > 3 and vol > 1.5:
        return "🟢强势"
    if chg > 1 and vol > 1.2:
        return "🟢启动"
    if chg > 0 and vol >= 0.8:
        return "🔵健康"
    if chg < -2 and vol > 1.5:
        return "🔴风险"
    if chg < -1 and vol < 0.7:
        return "⚪弱势"
    if abs(chg) <= 0.5 and 0.8 <= vol <= 1.2:
        return "⚪盘整"
    if chg > 5 and vol > 2.0 and amp > 8:
        return "⚠️天量"
    if chg < -3 and vol < 0.6:
        return "🟢止跌"
    return "⚪--"


def _format_message(items: List[Dict]) -> str:
    """组装 Markdown 推送内容。"""
    items = sorted(
        items,
        key=lambda x: ((x.get("_llm") or {}).get("strength", 0), x["_score"]),
        reverse=True,
    )

    lines: List[str] = []
    lines.append(f"### 📡 实时快讯 {len(items)} 条\n")
    lines.append(f"> {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    for i, it in enumerate(items, 1):
        ts = time.strftime("%H:%M", time.localtime(it["ctime"]))
        flag = "🔴 重磅 " if it["is_red"] else ""

        sectors = " / ".join(s for s, _ in it["_hits"])
        all_kws: List[str] = []
        for _, kws in it["_hits"]:
            all_kws.extend(kws)
        kw_str = "、".join(sorted(set(all_kws)))

        lines.append("---\n")
        lines.append(f"**{i}. {flag}[{ts}] {sectors}** (得分 {it['_score']})\n")

        llm = it.get("_llm")
        if llm:
            sent = llm["sentiment"]
            sent_emoji = {"利好": "🟢", "利空": "🔴", "中性": "⚪", "矛盾": "⚠️"}.get(sent, "")
            stars = "⭐" * min(llm["strength"], 5)
            lines.append(f"> {sent_emoji} **{sent}** {stars} ({llm['strength']}/5)")
            lines.append(f"> 💬 {llm['summary']}")
            if llm["tags"]:
                lines.append(f"> 🏷️ " + " · ".join(llm["tags"]))
            if llm["concern"]:
                lines.append(f"> ⚠️ **警惕**: {llm['concern']}")
            lines.append("")

        if it.get("title"):
            lines.append(f"**{it['title']}**\n")
        content = it["content"]
        if len(content) > 250:
            content = content[:250] + "..."
        lines.append(f"{content}\n")
        lines.append(f"🏷️ 关键词: `{kw_str}`")

        stocks = it.get("_stocks") or []
        if stocks:
            lines.append("\n📊 **关联个股**:\n")
            lines.append("| 代码 | 名称 | 现价 | 涨跌 | 量比 | PE | 流通市值 | 信号 |")
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
            for s in stocks:
                pe = f"{s['pe_ttm']:.0f}" if s["pe_ttm"] > 0 else "亏"
                mcap = f"{s['float_mcap_yi']:.0f}亿"
                up = "🔴" if s["change_pct"] > 0 else ("🟢" if s["change_pct"] < 0 else "⚪")
                tag = _judge_technical(s)
                lines.append(
                    f"| {s['code']} | {s['name']} | {s['price']:.2f} | "
                    f"{up}{s['change_pct']:+.2f}% | {s['vol_ratio']:.1f} | {pe} | {mcap} | {tag} |"
                )

        lines.append(f"\n🔗 [原文]({it['url']})\n")

    lines.append("---\n")
    lines.append("> ⚠️ 关联个股仅作消息→板块联动展示。ST/创/科/北已过滤。")
    return "\n".join(lines)


def _make_title(items: List[Dict]) -> str:
    has_red = any(it["is_red"] for it in items)
    top = max(
        items,
        key=lambda x: ((x.get("_llm") or {}).get("strength", 0), x["_score"]),
    )
    top_sector = top["_hits"][0][0] if top["_hits"] else ""

    sentiment = (top.get("_llm") or {}).get("sentiment")
    if sentiment == "利好":
        prefix = "🟢"
    elif sentiment == "利空":
        prefix = "🔴"
    elif sentiment == "矛盾":
        prefix = "⚠️"
    elif has_red:
        prefix = "🔴"
    else:
        prefix = "📡"

    strength = (top.get("_llm") or {}).get("strength", 0)
    star = "✦" if strength >= 4 else ""
    return f"{prefix}{star} 快讯 {len(items)}条 - {top_sector}"


def run_radar(dry_run: bool = False, skip_llm: bool = False) -> int:
    """运行一轮资讯雷达。

    返回推送条数（0 = 无命中 / 失败）。
    """
    print(f"[radar] start at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    news = fetch_telegraph(rn=30)
    if not news:
        print("[radar] 财联社无数据，跳过")
        return 0
    print(f"[radar] 抓到 {len(news)} 条")

    seen = load_seen()
    fresh = [n for n in news if n["id"] not in seen]
    print(f"[radar] 新增 {len(fresh)} 条（去重后）")

    hits: List[Dict] = []
    for n in fresh:
        ok, score, hit_list = should_push(n)
        if ok:
            n["_score"] = score
            n["_hits"] = hit_list
            hits.append(n)
    print(f"[radar] 命中 {len(hits)} 条")

    if not hits:
        if not dry_run:
            save_seen(seen | {n["id"] for n in news})
        return 0

    # 补行情
    hits = _enrich_with_quotes(hits)

    # LLM 点评
    if not skip_llm:
        evaluate_batch(hits)
        before = len(hits)
        hits = [
            h for h in hits
            if h.get("is_red") or not h.get("_llm") or h["_llm"]["strength"] >= 2
        ]
        if before != len(hits):
            print(f"[radar] LLM 过滤后剩 {len(hits)} 条（噪音 -{before - len(hits)}）")

    for h in hits:
        sectors = ",".join(s for s, _ in h["_hits"])
        stock_count = len(h.get("_stocks") or [])
        print(f"  [{h['_score']}分] {sectors} ({stock_count}股) | {h['content'][:50]}")

    if dry_run:
        print("\n--- DRY RUN ---")
        print(_make_title(hits))
        print(_format_message(hits))
        print("---\n")
    else:
        title = _make_title(hits)
        body = _format_message(hits)
        ok = send_to_wechat(title, body)
        print(f"[radar] 推送结果: {'成功✅' if ok else '失败❌'}")
        if ok:
            record_picks(hits)

    # 更新去重（不管是否推送都标记已见）
    if not dry_run:
        save_seen(seen | {n["id"] for n in news})

    return len(hits)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    skip_llm = "--no-llm" in sys.argv
    sys.exit(run_radar(dry_run=dry, skip_llm=skip_llm))
