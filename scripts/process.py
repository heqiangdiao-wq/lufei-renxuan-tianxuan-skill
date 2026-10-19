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
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        # 兼容两种顶层键名（core_terms / terms）。
        # ⚠️ 曾因键名不一致导致**术语表静默失效**：yaml 解析成功、不抛异常，
        #    于是不会走下面的 fallback 文本解析，结果载入 0 条、错字一个都没修。
        for t in (data.get("core_terms") or data.get("terms") or []):
            items.append({"correct": t.get("correct"),
                          "wrong": t.get("wrong") or [],
                          "source": t.get("source", ""),
                          "confidence": t.get("confidence", "")})
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
    """只替换**高置信度**术语，返回 (清洗后文本, 替换记录)。

    ⚠️ `confidence: guess` 的条目不自动替换 —— 纯字符串替换分不清词边界，
    例如「味[道跟]那个不一样」里的「道跟」跨了「味道 + 跟」两个词，
    替换成「道痕」就是误伤。这类低置信词交给人工确认，不自动动手。
    """
    corr = []
    for t in terms:
        conf = (t.get("confidence") or "").lower()
        if conf == "guess":
            continue
        for w in t["wrong"]:
            if not w or w not in text:
                continue
            n = text.count(w)
            text = text.replace(w, t["correct"])
            corr.append({"from": w, "to": t["correct"], "count": n,
                         "confidence": "verified" if conf == "verified"
                                       else ("guess" if t.get("source") == "guess" else "verified")})
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


# ---------------- 来源判定 ----------------
# 关键：origin 描述的是「**说话人是谁**」，不是「发布账号是谁」。
# 这个区分是整个项目的命门 —— 混用会直接导致风格失真。
#
#   author_voice         本人/官方号发布的原声
#   author_voice_repost  第三方账号搬运的**他本人原声**（抖音切片号主体）
#   third_party          他人转述、解说、评论他的观点（说话人不是他）
#   unknown              账号归属未核实
#
# 抖音切片号（「路飞的影子（切片）」「風雪夜歸人「路飛切片」」等）剪的就是
# 他本人生直播的录音，发布者是第三方但**说话人是他**，因此归 author_voice_repost，
# 绝不能因为「账号不是官方号」就打成 third_party —— 那会把 92% 的语料废掉。
AUTHOR_VOICE_REPOST = "author_voice_repost"
_SLICE_HINT = ("切片", "路飞", "路飛", "飛哥", "路飞切片")

# 从 ORIGIN_MAP 里学出「已知第三方解说号」名单，跨平台复用。
# 同一个账号在 B 站被判 third_party，在抖音就不该判成别的 —— 否则同一张嘴两种性质。
KNOWN_THIRD_PARTY = {a for a, o in ORIGIN_MAP.values() if o == "third_party" and a}


def resolve_origin(plat, author, d, mapped_origin):
    """返回 (origin, channel_origin)。channel_origin 留档，便于回溯原始判定。"""
    raw_origin = d.get("origin") or mapped_origin or "unknown"
    if plat == "douyin":
        name = author or ""
        if name in KNOWN_THIRD_PARTY:
            return "third_party", raw_origin
        if any(h in name for h in _SLICE_HINT):
            return AUTHOR_VOICE_REPOST, raw_origin
        # 不是切片号、也不是已知解说号 —— 归属不明确，宁可标 unknown 让人复核
        return "unknown", raw_origin
    return raw_origin, raw_origin


# ---------------- 主处理 ----------------
def process_file(path, terms):
    d = json.load(open(path, encoding="utf-8"))
    vid = d.get("source_id") or os.path.splitext(os.path.basename(path))[0]
    merged = merge_segments(d.get("segments") or [])

    plat = d.get("platform", "bilibili")
    mapped_author, mapped_origin = ORIGIN_MAP.get(vid, (None, "unknown"))
    author = d.get("author") or mapped_author
    origin, channel_origin = resolve_origin(plat, author, d, mapped_origin)
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
                "author": author,                  # 发布账号（可能是切片号）
                "origin": origin,                  # 说话人属性（上表四值）
                "channel_origin": channel_origin,  # 原始判定留档
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
