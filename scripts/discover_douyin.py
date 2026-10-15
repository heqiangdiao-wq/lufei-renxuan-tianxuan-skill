"""抖音批量发现器 v2：带元数据 + 深度翻页 + 自动过滤

原理（2026-10-14 实测）：
  打开 douyin.com/user/{sec_uid} 会请求 /aweme/v1/web/aweme/post/
  返回 JSON 含 aweme_list + has_more + max_cursor，可据此翻页。
  实测单页 16~21 条。

v2 改进：
  1. 保存完整元数据到 _probe/douyin_videos.jsonl（id/作者/标题/时长）
  2. 自动过滤无关号（淘址/参哥/狗视频等）
  3. 支持真正翻页（滚动到底反复触发，直到 has_more=0 或达上限）

用法：
  python discover_douyin.py --uid-file _probe/sec_uids.txt --pages 5
  python discover_douyin.py --sec-uid XXX --pages 3 --keep-all
"""
import os, sys, json, time, random

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDS_FILE = os.path.join(ROOT, "state", "douyin_ids.txt")
META_FILE = os.path.join(ROOT, "state", "douyin_videos.jsonl")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 与路飞无关的号（实测发现）
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


def load_jsonl():
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


def save_jsonl(all_meta):
    have = load_jsonl()
    add = 0
    with open(META_FILE, "a", encoding="utf-8") as f:
        for aid, m in all_meta.items():
            if aid not in have:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
                add += 1
    # 重建过滤后的 ID 清单
    merged = dict(have)
    merged.update(all_meta)
    keep = [k for k, v in merged.items() if is_relevant(v.get("author"), v.get("desc"))]
    with open(IDS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + "\n")
    return add, len(merged), len(keep)


def harvest(page, sec_uid, max_pages):
    """滚动 + 翻页，直到 has_more=0 或达到 max_pages"""
    got, pages = {}, []

    def on_resp(r):
        if "/aweme/v1/web/aweme/post/" not in r.url:
            return
        try:
            j = r.json()
        except Exception:
            return
        lst = j.get("aweme_list") or []
        for a in lst:
            if not a.get("aweme_id"):
                continue
            got[a["aweme_id"]] = {
                "id": a["aweme_id"],
                "author": ((a.get("author") or {}).get("nickname") or ""),
                "sec_uid": ((a.get("author") or {}).get("sec_uid") or ""),
                "desc": (a.get("desc") or "")[:120].replace("\n", " "),
                "duration": (a.get("duration") or 0) // 1000,
                "create_time": a.get("create_time"),
                "digg": ((a.get("statistics") or {}).get("digg_count") or 0),
            }
        pages.append({"n": len(lst), "has_more": j.get("has_more")})

    page.on("response", on_resp)
    try:
        page.goto(f"https://www.douyin.com/user/{sec_uid}",
                  wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        print(f"  goto 异常: {type(e).__name__} {str(e)[:60]}", flush=True)
    page.wait_for_timeout(10000)

    # 若首次加载没拿到 post 请求，再等一轮（headless 下抖音渲染有时要 15-25 秒）
    if not got:
        print("    [等待] 首屏未出数据，再等 15s ...", flush=True)
        page.wait_for_timeout(15000)

    # 翻页关键：抖音 PC 主页是瀑布流，必须真正滚到页面底部才会触发下一页请求。
    # 2026-10-15 修正 v3：
    #   ① page.mouse.wheel 必须先 mouse.move 到视口中央，否则滚轮事件落在 (0,0) 上不生效 —— 这是前两版失败的主因
    #   ② 抖音主页的滚动容器可能是某个 overflow:auto 的 div，不是 window —— 所以三路一起滚
    #   ③ 双保险：wheel 事件 + JS 直改 scrollTop + 键盘 End
    stale = 0
    for i in range(max_pages):
        before = len(got)
        for _ in range(6):
            # a) 真实滚轮事件（先移到页面中央）
            try:
                page.mouse.move(720, 450)
                page.mouse.wheel(0, 9000)
            except Exception:
                pass
            # b) 三路 JS 滚动兜底：window / documentElement / 最大可滚动容器
            page.evaluate("""() => {
                window.scrollTo(0, document.body.scrollHeight);
                document.documentElement.scrollTop = document.documentElement.scrollHeight;
                let best = null, maxh = 0;
                document.querySelectorAll('div,main,section').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 300 && el.clientHeight > 300) {
                        if (el.scrollHeight > maxh) { maxh = el.scrollHeight; best = el; }
                    }
                });
                if (best) best.scrollTop = best.scrollHeight;
            }""")
            page.wait_for_timeout(700)
            try:
                page.keyboard.press("End")
            except Exception:
                pass
            page.wait_for_timeout(1500)
        if len(got) == before:
            stale += 1
            print(f"    [翻页 {i+1}] 无新增（stale={stale}）", flush=True)
            if stale >= 2:
                break
        else:
            stale = 0
            print(f"    [翻页 {i+1}] {before} -> {len(got)} 条", flush=True)
        page.wait_for_timeout(1500)
    page.remove_listener("response", on_resp)

    # 诊断输出：区分「风控拦截」与「页面结构变化」——定时任务失败时靠这段定位原因
    try:
        url = page.url
        title = (page.title() or "")[:60]
        links = page.evaluate("document.querySelectorAll('a[href*=\"/video/\"]').length")
        hlen = page.evaluate("document.documentElement.outerHTML.length")
        body = (page.evaluate("document.body.innerText") or "")[:110].replace("\n", " ")
        print(f"    [诊断] url={url[:72]}", flush=True)
        print(f"    [诊断] title={title}", flush=True)
        print(f"    [诊断] 视频链接={links} | HTML={hlen} 字符", flush=True)
        print(f"    [诊断] 正文: {body}", flush=True)
    except Exception as e:
        print(f"    [诊断] 失败: {type(e).__name__} {str(e)[:60]}", flush=True)
    return got, pages


def main():
    args = sys.argv[1:]
    uids, max_pages, keep_all = [], 5, False
    i = 0
    while i < len(args):
        if args[i] == "--sec-uid":
            uids.append(args[i + 1]); i += 2
        elif args[i] == "--uid-file":
            uids += [l.strip() for l in open(args[i + 1], encoding="utf-8")
                     if l.strip() and not l.startswith("#")]
            i += 2
        elif args[i] == "--pages":
            max_pages = int(args[i + 1]); i += 2
        elif args[i] == "--keep-all":
            keep_all = True; i += 1
        else:
            uids.append(args[i]); i += 1

    if not uids:
        print("需要 sec_uid")
        return

    global BLOCK_AUTHORS
    if keep_all:
        BLOCK_AUTHORS = []

    all_meta = {}
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(locale="zh-CN", user_agent=UA,
                            viewport={"width": 1440, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        # 预热：必须先访问首页拿到 ttwid 等 cookie。
        # 2026-10-15 实测：直接 goto 用户主页会只返回页面骨架（title「的抖音」、作品列表为空、
        # /aweme/v1/web/aweme/post/ 一次都不请求），但不是验证码页——属于"未预热"而非风控。
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(6000)
            print("  [预热] 首页已访问", flush=True)
        except Exception as e:
            print(f"  [预热] 异常 {type(e).__name__} {str(e)[:50]}", flush=True)

        for idx, uid in enumerate(uids, 1):
            print(f"\n▶ [{idx}/{len(uids)}] {uid[:36]}...", flush=True)
            got, pages = harvest(page, uid, max_pages)
            rel = sum(1 for v in got.values() if is_relevant(v["author"], v["desc"]))
            print(f"  请求 {len(pages)} 次 | 拿到 {len(got)} 条 | 相关 {rel} 条", flush=True)
            for v in list(got.values())[:3]:
                print(f"    {v['duration']:>5}s {v['author'][:14]:<14} {v['desc'][:36]}", flush=True)
            all_meta.update(got)
            if idx < len(uids):
                w = random.randint(12, 25)
                print(f"  ⏳ 等待 {w}s ...", flush=True)
                time.sleep(w)
        b.close()

    add, total, keep = save_jsonl(all_meta)
    print(f"\n=== 结果 ===")
    print(f"  本次发现 {len(all_meta)} 条 | 新入库 {add} 条")
    print(f"  元数据累计 {total} 条 | 过滤后保留 {keep} 条")
    print(f"  → {IDS_FILE}")


if __name__ == "__main__":
    main()
