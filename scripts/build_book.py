#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 617 份单条清稿合并成一本"整本交付稿"。

为什么要合并本
  单条 md 适合溯源核对，但 617 个文件既人读不动、也不方便整份喂给模型。
  合并本按「作者原声 / 第三方解读」分组，正文去掉 [mm:ss] 噪音，
  既能一口气读完，也能整体作为上下文。

产出
  corpus/ALL_FULL.md     全文合并本（按来源分组）
  corpus/ALL_BY_SOURCE.md 不生成；目录见 corpus/clean/ALL.md

铁律：不改写、不摘要、不润色、不跨来源合并正文。只做拼接。
"""
import os
import glob
import json
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "corpus", "processed")
STATE = os.path.join(ROOT, "state")
DST = os.path.join(ROOT, "corpus", "ALL_FULL.md")

ORIGIN_LABEL = {
    "author_voice": "作者原声",
    "author_voice_repost": "作者原声 · 切片号搬运",
    "repost_read": "他人朗读原文",
    "commentary": "他人解读评论",
    "third_party": "第三方（旧值·待细分）",
    "unknown": "来源待确认",
}
ORIGIN_ORDER = ["author_voice", "author_voice_repost", "repost_read",
                "commentary", "third_party", "unknown"]


def mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def load_titles():
    """从登记表取真实视频标题。抖音标题含 @ 与话题标签，需清理。"""
    import re
    out = {}
    p = os.path.join(STATE, "registry.douyin.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for l in f:
                if not l.strip():
                    continue
                r = json.loads(l)
                t = (r.get("title") or "").strip()
                t = re.sub(r"@\S+", "", t)
                t = re.sub(r"#\S+", "", t)
                t = re.sub(r"\s+", " ", t).strip()
                if t:
                    # processed 侧的 vid 带 dy_ 前缀，登记表里是裸 ID，两边都登记
                    rid = str(r.get("id"))
                    out[rid] = t
                    out["dy_" + rid] = t
    return out


def load():
    titles = load_titles()
    items = []
    for p in sorted(glob.glob(os.path.join(PROC, "*.jsonl"))):
        rows = []
        with open(p, encoding="utf-8") as f:
            for l in f:
                if l.strip():
                    rows.append(json.loads(l))
        if not rows:
            continue
        src = rows[0].get("source") or {}
        vid = src.get("vid") or os.path.splitext(os.path.basename(p))[0]
        items.append({
            "vid": vid,
            "platform": src.get("platform") or "?",
            "author": src.get("author") or "未知",
            "origin": src.get("origin") or "unknown",
            "book_ref": rows[0].get("book_ref"),
            "title": titles.get(vid),
            "rows": rows,
            "chars": sum(r.get("chars", 0) for r in rows),
            "sec": (rows[-1].get("time") or {}).get("end") or 0,
        })
    return items


def main():
    items = load()
    total_chars = sum(i["chars"] for i in items)
    total_sec = sum(i["sec"] for i in items)

    out = [
        "# 路飞《人选天选论》语料全集",
        "",
        f"- **素材**：{len(items)} 条　**文字**：{total_chars:,} 字　"
        f"**时长**：{total_sec / 3600:.1f} 小时",
        f"- **导出**：{datetime.date.today().isoformat()}",
        "",
        "## 这份文件是什么",
        "",
        "全部口播素材的**逐字转录**合并本，只做两件事：改错别字、加标点。",
        "内容一字未改、未摘要、未润色。",
        "",
        "按性质分五部分，**这个划分比内容本身更要紧**：",
        "",
        "| 部分 | 含义 | 用法 |",
        "|---|---|---|",
        "| 一 · 作者原声 | 本人或官方录制账号发布 | 最可信，可直接当「他怎么说」 |",
        "| 二 · 作者原声·切片号搬运 | 第三方切片号剪的**他本人直播录音**，说话人仍是他 | 内容可用；账号非官方，引用时注明转载来源 |",
        "| 三 · 他人朗读原文 | 别人照着他的书稿念（文本一致、声音不是他） | 可作文本交叉验证，**不可标为「他说」** |",
        "| 四 · 他人解读评论 | 他人转述、解说、评论他的观点 | **只能当线索**，不可当作他的原话 |",
        "| 五 · 来源待确认 | 账号归属未核实 | 引用前须先核实 |",
        "",
        "⚠️ 最容易搞错的两步：",
        "① 把「二」当成「四」—— 切片号虽非官方号，剪的却是他在直播里说的话。",
        "   按「发布账号」判性质会把绝大多数语料误判成不可引用。**判的是说话人，不是发布者。**",
        "② 把「三」当成「二」—— 文本一样不代表声音是他，引用时不能标「他说」。",
        "",
        "混用会直接导致风格失真 —— 这是本项目的第一条纪律。",
        "",
        "> 每节标题后的 `[时长/字数/ID]` 用于回原视频核对；",
        "> 逐句时间戳版本见 `clean/{ID}.md`。",
        "",
        "---",
        "",
    ]

    idx = 0
    for oi, origin in enumerate(ORIGIN_ORDER, 1):
        group = [i for i in items if i["origin"] == origin]
        if not group:
            continue
        group.sort(key=lambda x: -x["chars"])
        gchars = sum(g["chars"] for g in group)
        out += [
            f"# 第{'一二三四五六'[oi - 1]}部分 · {ORIGIN_LABEL[origin]}",
            "",
            f"共 **{len(group)}** 条　**{gchars:,}** 字",
            "",
        ]
        for it in group:
            idx += 1
            title = it["book_ref"] or it["title"] or it["vid"]
            out.append(f"## {idx}. {title}")
            out.append("")
            out.append(f"`{it['vid']}` · {it['platform']} · {it['author']} · "
                       f"{mmss(it['sec'])} · {it['chars']:,} 字")
            out.append("")
            # 正文：相邻短句合成一段，避免满屏碎行
            buf = []
            for r in it["rows"]:
                t = (r.get("clean") or "").strip()
                if not t:
                    continue
                buf.append(t)
                if len("".join(buf)) >= 120:
                    out.append("".join(buf))
                    out.append("")
                    buf = []
            if buf:
                out.append("".join(buf))
                out.append("")
        out += ["---", ""]

    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))

    size = os.path.getsize(DST) / 1024 / 1024
    print(f"已写入 {os.path.relpath(DST, ROOT)}")
    print(f"  {len(items)} 条 / {total_chars:,} 字 / {total_sec / 3600:.1f} 小时 / {size:.1f} MB")
    for origin in ORIGIN_ORDER:
        g = [i for i in items if i["origin"] == origin]
        if g:
            print(f"  {ORIGIN_LABEL[origin]:<8} {len(g):>4} 条  {sum(x['chars'] for x in g):>10,} 字")


if __name__ == "__main__":
    main()
