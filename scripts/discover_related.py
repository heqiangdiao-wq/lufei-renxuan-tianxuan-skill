"""抖音「相关推荐」扩散发现器（2026-10-15）

背景：
  用户主页接口 `/aweme/v1/web/aweme/post/` 对未登录游客已不可用 ——
  headless / 有头 / 持久化 profile 三种配置全部只返回骨架页（title「的抖音」、请求 0 次）。
  但**视频详情页仍然可用**，且详情页会请求：
    /aweme/v1/web/aweme/related/   返回 10 条相关视频（含完整元数据）
    /aweme/v1/web/mix/aweme/       返回合集内视频
  → 用素材池里已有的视频当种子，就能滚雪球式扩散出新视频 ID，完全绕开主页接口。

用法：
  python discover_related.py --seeds 5            # 取 5 条还没扩散过的种子
  python discover_related.py --id 7593565968472757625
  python discover_related.py --seeds 10 --gap-min 8 --gap-max 20
"""
import os, sys, json, random, time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "state")
IDS_FILE = os.path.join(PROBE, "douyin_ids.txt")
META_FILE = os.path.join(PROBE, "douyin_videos.jsonl")
DONE_FILE = os.path.join(PROBE, "related_seeds_done.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BLOCK_AUTHORS = ["淘址", "参哥认知圈", "纯然商业", "手抓饼", "直播全过程"]


def is_relevant(author, desc):
    a, d = str(author or ""), str(desc or "")
    if any(b in a for b in BLOCK_AUTHORS):
        return False
    if any(k in a for k in ("路飞", "路飛", "自己造船")):
        return True
    if any(k in d for k in ("路飞", "路飛", "自己造船", "人选天选", "姜蓝")):
        return True
    return False


def load_meta():
    seen = {}
    if os.path.exists(META_FILE):
        for line in open(META_FILE, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    seen[r["id"]] = r
                except Exception:
                    pass
    return seen


def load_done():
    if not os.path.exists(DONE_FILE):
        return set()
    return {l.strip() for l in open(DONE_FILE, encoding="utf-8") if l.strip()}


def save_all(all_meta, done_new):
    have = load_meta()
    add = 0
    with open(META_FILE, "a", encoding="utf-8") as f:
        for aid, m in all_meta.items():
            if aid not in have:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
                add += 1
    merged = dict(have)
    merged.update(all_meta)
    keep = [k for k, v in merged.items() if is_relevant(v.get("author"), v.get("desc"))]
    with open(IDS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + "\n")
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        for d in done_new:
            f.write(d + "\n")
    return add, len(merged), len(keep)


def scrape(page, vid):
    """打开详情页，收集 related / mix 接口返回的视频元数据"""
    got = {}

    def on_resp(r):
        if "/aweme/v1/web/" not in r.url:
            return
        if not any(k in r.url for k in ("/aweme/related/", "/mix/aweme/")):
            return
        via = "related" if "/aweme/related/" in r.url else "mix"
        try:
            j = r.json()
        except Exception:
            return
        for a in (j.get("aweme_list") or []):
            if not a.get("aweme_id") or a["aweme_id"] == vid:
                continue
            got[a["aweme_id"]] = {
                "id": a["aweme_id"],
                "author": ((a.get("author") or {}).get("nickname") or ""),
                "sec_uid": ((a.get("author") or {}).get("sec_uid") or ""),
                "desc": (a.get("desc") or "")[:120].replace("\n", " "),
                "duration": (a.get("duration") or 0) // 1000,
                "create_time": a.get("create_time"),
                "digg": ((a.get("statistics") or {}).get("digg_count") or 0),
                "via": via,
                "seed": vid,
            }

    page.on("response", on_resp)
    try:
        page.goto(f"https://www.douyin.com/video/{vid}",
                  wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        print(f"    goto 异常 {type(e).__name__}", flush=True)
    page.wait_for_timeout(10000)
    for _ in range(3):                      # 滚动尝试触发更多相关推荐
        try:
            page.mouse.move(720, 450)
            page.mouse.wheel(0, 2500)
        except Exception:
            pass
        page.wait_for_timeout(2200)
    page.remove_listener("response", on_resp)
    return got


def main():
    args = sys.argv[1:]
    n_seeds, gap_min, gap_max, seeds = 5, 8, 20, []
    i = 0
    while i < len(args):
        if args[i] == "--seeds":
            n_seeds = int(args[i + 1]); i += 2
        elif args[i] == "--id":
            seeds.append(args[i + 1]); i += 2
        elif args[i] == "--gap-min":
            gap_min = int(args[i + 1]); i += 2
        elif args[i] == "--gap-max":
            gap_max = int(args[i + 1]); i += 2
        else:
            i += 1

    meta, done = load_meta(), load_done()
    if not seeds:
        # 种子优先取自「已被过滤为相关」的池子 —— 实测种子越相关，带出的相关率越高
        # （无关种子带出的 10 条几乎全是无关内容，如宠物/情感 vlog）
        pool_ids = []
        if os.path.exists(IDS_FILE):
            pool_ids = [l.strip() for l in open(IDS_FILE, encoding="utf-8") if l.strip()]
        pool = [k for k in pool_ids if k not in done]
        if len(pool) < n_seeds:                  # 池子不够时再退回全量元数据
            extra = [k for k in meta if k not in done and k not in pool]
            random.shuffle(extra)
            pool += extra
        random.shuffle(pool)
        seeds = pool[:n_seeds]

    if not seeds:
        print("没有可用种子（全部已扩散过）。可扩充素材池后再跑。")
        return

    print(f"种子 {len(seeds)} 条 | 已扩散过 {len(done)} 条 | 池子现有 {len(meta)} 条元数据")
    all_meta = {}
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA,
                            viewport={"width": 1440, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        for idx, vid in enumerate(seeds, 1):
            print(f"\n▶ [{idx}/{len(seeds)}] 种子 {vid}", flush=True)
            got = scrape(page, vid)
            rel = sum(1 for v in got.values() if is_relevant(v["author"], v["desc"]))
            new = sum(1 for k in got if k not in meta)
            print(f"  相关推荐 {len(got)} 条 | 其中相关 {rel} 条 | 新的 {new} 条", flush=True)
            for v in list(got.values())[:3]:
                tag = "✅" if is_relevant(v["author"], v["desc"]) else "  "
                print(f"    {tag} {v['duration']:>5}s {v['author'][:14]:<14} {v['desc'][:34]}",
                      flush=True)
            all_meta.update(got)
            if idx < len(seeds):
                w = random.randint(gap_min, gap_max)
                print(f"  ⏳ 等待 {w}s ...", flush=True)
                time.sleep(w)
        b.close()

    add, total, keep = save_all(all_meta, seeds)
    print(f"\n=== 结果 ===")
    print(f"  本次拿到 {len(all_meta)} 条 | 新入库 {add} 条")
    print(f"  元数据累计 {total} 条 | 过滤后保留 {keep} 条")
    print(f"  → {IDS_FILE}")


if __name__ == "__main__":
    main()
