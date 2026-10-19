"""导出干净文字稿：processed JSONL -> 人能直接读的 Markdown

产出
  corpus/clean/{vid}.md      每条素材一份，打开就能从头读到尾
  corpus/clean/ALL.md        全部素材汇总（供后续分类整理）
  corpus/clean/corpus.jsonl  智能体数据库（结构化，供 AI 检索调用）

设计
  文字稿只做两件事：改错别字、加标点。内容一字不改。
  时间戳保留在行首，方便回原视频核对。
"""
import os, json, glob, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "corpus", "processed")
CLEAN = os.path.join(ROOT, "corpus", "clean")
os.makedirs(CLEAN, exist_ok=True)

ORIGIN_LABEL = {"author_voice": "作者原声",
                "author_voice_repost": "作者原声·切片号搬运",
                "third_party": "第三方解读",
                "unknown": "来源待确认"}


def mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def base_title(vid):
    """从条目里取一个可读标题（用第一条 clean 的前 22 字）"""
    return vid


def export_video(jsonl_path):
    rows = [json.loads(l) for l in open(jsonl_path, encoding="utf-8") if l.strip()]
    if not rows:
        return None
    vid = rows[0]["source"]["vid"]
    src = rows[0]["source"]
    origin = ORIGIN_LABEL.get(src.get("origin"), src.get("origin", "?"))
    total_sec = rows[-1]["time"]["end"]
    total_chars = sum(r["chars"] for r in rows)
    ncorr = sum(len(r["corrections"]) for r in rows)

    head = [
        f"# {rows[0].get('book_ref') or src.get('vid')}",
        "",
        f"- **来源**：{src.get('platform')} · {src.get('author') or '未知'}（{origin}）",
        f"- **视频**：{src.get('vid')}",
        f"- **时长**：{mmss(total_sec)}　**文字**：{total_chars} 字　**段落**：{len(rows)} 段",
        f"- **纠错**：{ncorr} 处（仅改错别字，内容未改动）",
        f"- **导出**：{datetime.date.today().isoformat()}",
        "",
        "---",
        "",
        "## 正文",
        "",
    ]
    body = []
    for r in rows:
        ts = mmss(r["time"]["start"])
        text = r["clean"]
        marks = ""
        if r["corrections"]:
            pairs = "、".join(f'{c["from"]}→{c["to"]}' for c in r["corrections"])
            marks = f"  <!-- 纠错: {pairs} -->"
        body.append(f"`[{ts}]` {text}{marks}")
        body.append("")

    md = "\n".join(head + body)
    dst = os.path.join(CLEAN, f"{vid}.md")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(md)
    return {"vid": vid, "file": os.path.basename(dst), "title": rows[0].get("book_ref") or vid,
            "origin": origin, "author": src.get("author") or "未知",
            "rows": len(rows), "chars": total_chars, "corr": ncorr, "sec": total_sec}


def export_all(items):
    lines = ["# 路飞《人选天选论》语料总库", "",
             f"导出时间：{datetime.date.today().isoformat()}　"
             f"素材 {len(items)} 条　合计 {sum(i['chars'] for i in items)} 字", "",
             "## 素材清单", "",
             "| 文件 | 来源 | 性质 | 时长 | 字数 | 纠错 |", "|---|---|---|---|---|---|"]
    for it in items:
        lines.append(f"| [{it['file']}]({it['file']}) | {it['author']} | {it['origin']} "
                     f"| {mmss(it['sec'])} | {it['chars']} | {it['corr']} |")
    lines += ["", "---", ""]
    with open(os.path.join(CLEAN, "ALL.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def export_db(items):
    """智能体数据库：把全部条目合成一个 JSONL，带 source 便于溯源"""
    dst = os.path.join(CLEAN, "corpus.jsonl")
    n = 0
    with open(dst, "w", encoding="utf-8") as out:
        for it in items:
            p = os.path.join(PROC, f"{it['vid']}.jsonl")
            for line in open(p, encoding="utf-8"):
                if line.strip():
                    out.write(line)
                    n += 1
    return n


if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(PROC, "*.jsonl")))
    if not files:
        print("corpus/processed 下没有可导出的文件")
        raise SystemExit(0)
    items = []
    for f in files:
        r = export_video(f)
        if r:
            items.append(r)
            print(f"  ✅ {r['file']:<26} {r['rows']:>3} 段 / {r['chars']:>5} 字 / 纠错 {r['corr']}")
    export_all(items)
    n = export_db(items)
    print(f"\n  ✅ ALL.md（总目录）")
    print(f"  ✅ corpus.jsonl（智能体数据库，{n} 条）")
    print(f"\n输出目录：{os.path.relpath(CLEAN, ROOT)}")
