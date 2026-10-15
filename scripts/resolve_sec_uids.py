"""从已采集的视频 ID 反查作者的 sec_uid，重建号清单

背景：2026-10-15 `_probe/` 被清空，19 个切片号的 sec_uid 列表（real_uids.txt）丢失。
本脚本用登录态打开每条已知视频的详情页，拦截 /aweme/detail/，提取
`author.sec_uid` 与 `author.nickname`，去重后写出号清单。

⚠️ sec_uid 必须来自「视频元数据的 author.sec_uid」——从页面 HTML 用正则抠会被截断，
   截断的 ID 会让抖音返回空壳页（title「的抖音」、接口 0 请求），看起来像风控其实是无效 ID。

用法：
  python scripts/resolve_sec_uids.py --from-registry        # 用登记表里的全部抖音 ID
  python scripts/resolve_sec_uids.py --ids id1,id2
"""
import os, sys, json, random, time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
PROFILE = os.path.join(STATE, "dy_profile")
OUT = os.path.join(STATE, "real_uids.txt")
REG = os.path.join(STATE, "registry.douyin.jsonl")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

found = {}          # sec_uid -> nickname


def on_resp(r):
    if "/aweme/detail/" not in r.url:
        return
    try:
        body = r.text()
        if len(body) < 3000:
            return
        j = json.loads(body)
    except Exception:
        return
    aw = j.get("aweme_detail") or {}
    au = aw.get("author") or {}
    su, nick = au.get("sec_uid"), au.get("nickname")
    if su and len(su) > 40:
        found[su] = nick or ""


def main():
    args = sys.argv[1:]
    ids = []
    if "--ids" in args:
        ids = [x.strip() for x in args[args.index("--ids") + 1].split(",") if x.strip()]
    else:
        seen = []
        if os.path.exists(REG):
            for l in open(REG, encoding="utf-8"):
                l = l.strip()
                if not l:
                    continue
                try:
                    r = json.loads(l)
                except Exception:
                    continue
                if r.get("id") and r.get("id") not in seen:
                    seen.append(r["id"])
        ids = seen

    if not ids:
        print("没有可用的视频 ID")
        return
    print(f"待反查 {len(ids)} 条视频 | 已登录 profile: {PROFILE}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"],
            locale="zh-CN", user_agent=UA,
            viewport={"width": 1440, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_resp)

        for i, vid in enumerate(ids, 1):
            before = len(found)
            try:
                page.goto(f"https://www.douyin.com/video/{vid}",
                          wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            page.wait_for_timeout(6000)
            tag = "新号" if len(found) > before else "已知"
            print(f"  [{i}/{len(ids)}] {vid}  → {tag}（累计 {len(found)} 个号）", flush=True)
            time.sleep(random.uniform(3, 7))
        ctx.close()

    # 合并已有的 real_uids.txt（若存在）
    old = {}
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            l = l.strip()
            if l and "\t" in l:
                u, n = l.split("\t", 1)
                old[u.strip()] = n.strip()
    merged = {**old, **found}
    with open(OUT, "w", encoding="utf-8") as f:
        for u, n in sorted(merged.items(), key=lambda x: x[1]):
            f.write(f"{u}\t{n}\n")

    print(f"\n=== 结果 ===")
    print(f"  本次识别 {len(found)} 个号 | 合并后共 {len(merged)} 个")
    for u, n in sorted(merged.items(), key=lambda x: x[1]):
        print(f"    {n[:24]:<26} {u[:46]}...")
    print(f"  → {OUT}")


if __name__ == "__main__":
    main()
