"""抖音用户主页作品列表抓取（登录态）— 2026-10-15

⚠️ 踩了一整天的坑，务必记住：
  **sec_uid 必须是完整的**。截断的 ID 会让抖音返回空壳页
  （title「的抖音」、/aweme/v1/web/aweme/post/ 请求 0 次），
  看起来一模一样像风控，实际是 ID 无效。
  完整 sec_uid 只能从「视频元数据里的 author.sec_uid」取（_probe/real_uids.txt），
  不要从页面 HTML 正则里抠。

原理：用登录态的持久化 profile 打开 douyin.com/user/{sec_uid}，
      滚动翻页（实测每页 18 条，has_more=1 表示还有），
      拦截 /aweme/v1/web/aweme/post/ 响应拿完整元数据。

风控约束（务必遵守）：
  1. 只读 —— 只滚动页面，绝不点赞 / 评论 / 关注 / 分享
  2. 号与号之间间隔 15~35 秒随机
  3. 单号翻页上限 max_pages（默认 25 页 ≈ 450 条）
  4. 号内翻页之间插入随机停留，模拟人看列表
  5. 连续 3 个号返回 0 条 → 判定异常，立即停止本次任务
  6. 单次会话最多处理 max_accounts 个号（默认 3）

用法：
  python discover_user_page.py --uid-file _probe/real_uids.txt --accounts 3
  python discover_user_page.py --sec-uid MS4wLj... --pages 25
"""
import os, sys, json, random, time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "state")
PROFILE = os.path.join(PROBE, "dy_profile")
IDS_FILE = os.path.join(PROBE, "douyin_ids.txt")
META_FILE = os.path.join(PROBE, "douyin_videos.jsonl")
DONE_ACC = os.path.join(PROBE, "userpage_done.txt")
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


def save_all(all_meta, accounts_done):
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
    with open(DONE_ACC, "a", encoding="utf-8") as f:
        for a in accounts_done:
            f.write(a + "\n")
    return add, len(merged), len(keep)


def harvest(page, sec_uid, max_pages):
    """滚动翻页抓该号全部作品；返回 (视频字典, 每页统计)"""
    got, pages = {}, []

    def on_resp(r):
        if "/aweme/v1/web/aweme/post/" not in r.url:
            return
        try:
            j = r.json()
        except Exception:
            return
        lst = j.get("aweme_list") or []
        pages.append({"n": len(lst), "has_more": j.get("has_more")})
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
                "via": "userpage",
            }

    page.on("response", on_resp)
    try:
        page.goto(f"https://www.douyin.com/user/{sec_uid}",
                  wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        print(f"    goto 异常 {type(e).__name__}", flush=True)
    page.wait_for_timeout(9000)

    for i in range(max_pages):
        if pages and pages[-1].get("has_more") == 0:
            print(f"    [翻页] has_more=0，已到列表底部", flush=True)
            break
        before = len(got)
        for _ in range(4):                       # 滚到底触发下一页
            try:
                page.mouse.move(720, 450)
                page.mouse.wheel(0, 9000)
            except Exception:
                pass
            try:
                page.evaluate("""()=>{
                    window.scrollTo(0, document.body.scrollHeight);
                    let b=null,m=0;
                    document.querySelectorAll('div,main,section').forEach(e=>{
                      if(e.scrollHeight>e.clientHeight+300&&e.clientHeight>300&&e.scrollHeight>m){m=e.scrollHeight;b=e;}});
                    if(b) b.scrollTop=b.scrollHeight;
                }""")
            except Exception:
                pass
            page.wait_for_timeout(1300)
        page.wait_for_timeout(random.randint(1200, 3000))   # 拟人停留
        if len(got) == before and i > 1:
            print(f"    [翻页 {i+1}] 无新增，停止", flush=True)
            break
    page.remove_listener("response", on_resp)
    return got, pages


def main():
    args = sys.argv[1:]
    uids, max_accounts, max_pages = [], 3, 25
    i = 0
    while i < len(args):
        if args[i] == "--uid-file":
            for l in open(args[i + 1], encoding="utf-8"):
                l = l.strip()
                if l and not l.startswith("#"):
                    uids.append(l.split("\t")[0].strip())
            i += 2
        elif args[i] == "--sec-uid":
            uids.append(args[i + 1]); i += 2
        elif args[i] == "--accounts":
            max_accounts = int(args[i + 1]); i += 2
        elif args[i] == "--pages":
            max_pages = int(args[i + 1]); i += 2
        else:
            i += 1

    if not uids:
        print("需要 --uid-file 或 --sec-uid")
        return

    done = set()
    if os.path.exists(DONE_ACC):
        done = {l.strip() for l in open(DONE_ACC, encoding="utf-8") if l.strip()}
    todo = [u for u in uids if u not in done][:max_accounts]
    if not todo:
        print("所有号都已抓过（_probe/userpage_done.txt）。")
        return

    print(f"待抓 {len(todo)} 个号 | 已抓过 {len(done)} 个 | 单号上限 {max_pages} 页")
    all_meta, done_now, empty_streak = {}, [], 0

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"],
            locale="zh-CN", user_agent=UA,
            viewport={"width": 1440, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for idx, uid in enumerate(todo, 1):
            print(f"\n▶ [{idx}/{len(todo)}] {uid[:46]}", flush=True)
            got, pages = harvest(page, uid, max_pages)
            try:
                title = (page.title() or "")[:40]
            except Exception:
                title = ""
            author = next(iter(got.values()), {}).get("author", "") if got else ""
            print(f"  拿到 {len(got)} 条 | 翻页请求 {len(pages)} 次 | "
                  f"title=「{title}」", flush=True)

            if not got:
                empty_streak += 1
                print(f"  ⚠️ 该号 0 条（连续空 {empty_streak} 次）", flush=True)
                if empty_streak >= 3:
                    print("  🛑 连续 3 个号为空，判定异常（风控或 ID 失效），立即停止。", flush=True)
                    break
            else:
                empty_streak = 0
                all_meta.update(got)
                done_now.append(uid)
                for v in list(got.values())[:2]:
                    print(f"    {v['duration']:>5}s {v['author'][:16]:<16} {v['desc'][:34]}",
                          flush=True)

            if idx < len(todo):
                w = random.randint(15, 35)
                print(f"  ⏳ 间隔 {w}s（拟人节奏）...", flush=True)
                time.sleep(w)
        ctx.close()

    add, total, keep = save_all(all_meta, done_now)
    print(f"\n=== 结果 ===")
    print(f"  本次抓到 {len(all_meta)} 条 | 新入库 {add} 条")
    print(f"  元数据累计 {total} 条 | 过滤后保留 {keep} 条")
    print(f"  → {IDS_FILE}")


if __name__ == "__main__":
    main()
