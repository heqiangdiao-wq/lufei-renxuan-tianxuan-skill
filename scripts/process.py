"""P2 后处理：转录 JSON -> 清洗后的结构化条目

工序（严格按此顺序，每步留痕）
  ① 读转录 JSON（带时间戳的零散片段）
  ② 按停顿合并成句（加标点）—— 已有标点的原样保留
  ③ 术语校正（terms.yaml）—— 只换字，不改句
  ④ 附来源元数据（平台/作者/时间/原声还是第三方）
  ⑤ 输出 JSONL，一条一句，raw 与 clean 并存

铁律：不改写、不摘要、不润色、不跨来源合并。
      raw 字段永久保留原始转录，任何清洗结果都只写进 clean。

用法
  python process.py                    # 处理 corpus/transcripts 下全部
  python process.py BV1DpbC67ELK       # 只处理指定 ID
  python process.py --stats            # 看统计
"""
import os, sys, json, re, glob, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANS = os.path.join(ROOT, "corpus", "transcripts")
OUTDIR = os.path.join(ROOT, "corpus", "processed")
TERMS = os.path.join(ROOT, "state", "terms.yaml")
os.makedirs(OUTDIR, exist_ok=True)

# 章节映射：用于给条目挂 book_ref（B站作者官方号的章节视频）
CHAPTER_MAP = {
    "BV1DpbC67ELK": "第一章·河流世界",
    "BV1DnbC6UEWB": "第二章·走向觉醒的\u201c人之路\u201d",
}

# 来源属性映射：区分「作者原声」与「第三方解读」——这是防失真的关键字段
# 由 _probe/bv_clean.json 的 UP 主信息自动生成
ORIGIN_MAP = {
    "BV1UC9vBEEqn": ("账号已注销", "third_party"),
    "BV1ETPwzcEme": ("风行录制组呀", "author_voice"),
    "BV1ZTH86vEGV": ("风行录制小组", "author_voice"),
    "BV1tKpq6VEDP": ("jack叔叔直播全过程", "third_party"),
    "BV1yCHi6NEiJ": ("blibli888444", "third_party"),
    "BV1Phpo6ZE3U": ("风行录制小组", "author_voice"),
    "BV1iWLg6hEGq": ("bili_1015528523", "third_party"),
    "BV1QZpo6uEE4": ("风行录制小组", "author_voice"),
    "BV1QZpo6uEny": ("风行录制小组", "author_voice"),
    "BV1QZpo6uEqr": ("风行录制小组", "author_voice"),
    "BV1RnHe6GEyM": ("风行录制小组", "author_voice"),
    "BV1hGPXzfE8b": ("一颗卤蛋陈昌文", "third_party"),
    "BV1qoHe6mE5R": ("风行录制小组", "author_voice"),
    "BV17JgR65EV8": ("自己造船的路飞切片号", "third_party"),
    "BV1QZpo6uE8f": ("风行录制小组", "author_voice"),
    "BV1X1HY6TE4x": ("风行录制小组", "author_voice"),
    "BV1GZpo6gEM6": ("风行录制小组", "author_voice"),
    "BV1A4JF6WE5x": ("何鑫淼_路飞切片", "third_party"),
    "BV1QZpo6uEQs": ("风行录制小组", "author_voice"),
    "BV1CiTz6iE1v": ("Lyricspace心事空间", "third_party"),
    "BV16XXsBBESe": ("玩心帮", "third_party"),
    "BV1QN5y6mEF7": ("自己造船的路飞", "author_voice"),
    "BV1n9p96LEUH": ("风行录制小组", "author_voice"),
    "BV1yRha6CEJs": ("何鑫淼_路飞切片", "third_party"),
    "BV1gnps6LEXV": ("索隆-路飞切片", "third_party"),
    "BV1SFZtBgEqj": ("自己造船的路飞", "author_voice"),
    "BV13fpo6zENh": ("风行录制小组", "author_voice"),
    "BV1zwpV6ZEEE": ("何鑫淼_路飞切片", "third_party"),
    "BV16nEY6kEPy": ("竹西-路飞切片号", "third_party"),
    "BV1QZpo6uEPD": ("风行录制小组", "author_voice"),
    "BV1ZRPKzCEZJ": ("一颗卤蛋陈昌文", "third_party"),
    "BV1DpbC67ELK": ("自己造船的路飞", "author_voice"),
    "BV1HbG16PEoA": ("自己造船的路飞", "author_voice"),
    "BV1QZpo6uE7i": ("风行录制小组", "author_voice"),
    "BV1kAHC61ET7": ("何鑫淼_路飞切片", "third_party"),
    "BV13Zpo6gESj": ("风行录制小组", "author_voice"),
    "BV1CPp96UEoE": ("福星2077", "third_party"),
    "BV1Tu4FzhE7e": ("自己造船的路飞", "author_voice"),
    "BV1J3an6MEWA": ("索隆-路飞切片", "third_party"),
    "BV1edHr6aE6D": ("阳光兆林", "third_party"),
    "BV1TRGu66EgW": ("无刀流的Zoro路飞切片", "third_party"),
    "BV1c2hx6dE7U": ("福星2077", "third_party"),
    "BV11qYP6uEMQ": ("福星2077", "third_party"),
    "BV1B7pd6mEkX": ("自己造船的路飞", "author_voice"),
    "BV1cTGg6aECy": ("无刀流的Zoro路飞切片", "third_party"),
    "BV1pehg6UEHa": ("福星2077", "third_party"),
    "BV1nGpg6sEhJ": ("自己造船的路飞", "author_voice"),
    "BV1TNHQ67E5c": ("福星2077", "third_party"),
    "BV1549kBVE3x": ("Scott_CFD", "third_party"),
    "BV1ihPvzXEct": ("福星2077", "third_party"),
    "BV1V5pf67EK4": ("曹小派", "third_party"),
    "BV1G5pe63Ei7": ("索隆-路飞切片", "third_party"),
    "BV1JhpX6GEaZ": ("福星2077", "third_party"),
    "BV132pE6PEvK": ("自己造船的路飞", "author_voice"),
    "BV1d3pB6ZE2B": ("自己造船的路飞", "author_voice"),
    "BV1Gi95BYEmJ": ("自己造船的路飞", "author_voice"),
    "BV1D5pF6GEL1": ("自己造船的路飞", "author_voice"),
    "BV1wLaB6bEWj": ("福星2077", "third_party"),
    "BV1PrWA6LEum": ("福星2077", "third_party"),
    "BV1HYyaY2Eeq": ("自己造船的路飞", "author_voice"),
    "BV1K7QSB1ER6": ("Veggieg不读V哥哥呀", "third_party"),
    "BV1fJpd6jEPR": ("曹小派", "third_party"),
}


# ---------------- 术语表 ----------------
def load_terms(path=TERMS):
    """读 terms.yaml；无 pyyaml 时用轻量解析"""
    items = []
    if not os.path.exists(path):
        return items
    try:
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8"))
        for t in (data or {}).get("core_terms", []):
            items.append({"correct": t.get("correct"),
                          "wrong": t.get("wrong") or [],
                          "source": t.get("source", "")})
    except Exception:
        txt = open(path, encoding="utf-8").read()
        for blk in txt.split("- correct:")[1:]:
            c = blk.split("\n")[0].strip()
            m = re.search(r"wrong:\s*\[([^\]]*)\]", blk)
            s = re.search(r"source:\s*(\S+)", blk)
            if c and m:
                items.append({"correct": c,
                              "wrong": [w.strip() for w in m.group(1).split(",") if w.strip()],
                              "source": s.group(1) if s else ""})
    # 长词优先，避免嵌套覆盖（先替「人选天选论」再替「天选」）
    items.sort(key=lambda x: -len(x["correct"]))
    return items


def apply_terms(text, terms):
    corr = []
    for t in terms:
        for w in t["wrong"]:
            if not w or w not in text:
                continue
            n = text.count(w)
            text = text.replace(w, t["correct"])
            corr.append({"from": w, "to": t["correct"], "count": n,
                         "confidence": "verified" if t.get("source") != "guess" else "guess"})
    return text, corr


# ---------------- 标点/合并 ----------------
def merge_segments(segs, gap_sentence=0.45, max_chars=70):
    """合并片段并恢复标点。

    关键认知：Whisper 的片段切分点本身就是语音停顿（上一段 end == 下一段 start，
    所以按 gap 判断永远为 0，这就是之前一整段没有逗号的原因）。
    因此**每个原始片段之间都补一个逗号**，只在停顿明显或累计超长时才断句。
    """
    out, buf, start, prev_end = [], "", None, None
    for s in segs:
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        if start is None:
            start = s.get("start", 0.0)
        gap = (s.get("start", 0.0) - prev_end) if prev_end is not None else 0.0

        if buf and (gap > gap_sentence or len(buf) + len(txt) > max_chars):
            out.append({"start": start, "end": prev_end,
                        "text": buf.rstrip("，") + "。"})
            buf, start = txt, s.get("start", 0.0)
        elif buf:
            buf += "，" + txt
        else:
            buf = txt
        prev_end = s.get("end", 0.0)

    if buf:
        out.append({"start": start, "end": prev_end, "text": buf.rstrip("，") + "。"})
    return out


# ---------------- 主处理 ----------------
def process_file(path, terms):
    d = json.load(open(path, encoding="utf-8"))
    vid = d.get("source_id") or os.path.splitext(os.path.basename(path))[0]
    merged = merge_segments(d.get("segments") or [])

    plat = d.get("platform", "bilibili")
    mapped_author, mapped_origin = ORIGIN_MAP.get(vid, (None, "unknown"))
    author = d.get("author") or mapped_author
    origin = d.get("origin") or mapped_origin
    rows, ncorr = [], 0
    for i, m in enumerate(merged, 1):
        raw = m["text"]
        clean, corr = apply_terms(raw, terms)
        ncorr += len(corr)
        rows.append({
            "id": f"{plat}-{vid}-{i:04d}",
            "source": {
                "platform": plat,
                "vid": vid,
                "author": author,
                "origin": origin,          # author_voice=作者原声 / third_party=他人解读
                "audio": d.get("audio_path"),
            },
            "time": {"start": round(m["start"], 2), "end": round(m["end"], 2)},
            "raw": raw,
            "clean": clean,
            "corrections": corr,
            "chars": len(clean),
            "book_ref": CHAPTER_MAP.get(vid),
            "collected_at": d.get("collected_at") or datetime.date.today().isoformat(),
        })
    return vid, rows, ncorr


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    terms = load_terms()
    print(f"[术语表] 载入 {len(terms)} 条")

    if args and args[0] == "--stats":
        files = glob.glob(os.path.join(OUTDIR, "*.jsonl"))
        tot = 0
        for f in files:
            n = sum(1 for _ in open(f, encoding="utf-8"))
            tot += n
            print(f"  {os.path.basename(f):<40} {n} 条")
        print(f"合计 {len(files)} 个文件 / {tot} 条")
        sys.exit(0)

    files = ([os.path.join(TRANS, f"{a}.json") for a in args] if args
             else sorted(glob.glob(os.path.join(TRANS, "*.json"))))
    files = [f for f in files if os.path.exists(f)]
    if not files:
        print("没有可处理的转录文件")
        sys.exit(0)

    grand = 0
    for f in files:
        vid = os.path.splitext(os.path.basename(f))[0]
        vid, rows, ncorr = process_file(f, terms)
        dst = os.path.join(OUTDIR, f"{vid}.jsonl")
        with open(dst, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        grand += len(rows)
        print(f"  {vid:<22} {len(rows):>4} 条  纠错 {ncorr:>3} 处  -> {os.path.relpath(dst, ROOT)}")
    print(f"\n合计 {grand} 条")
