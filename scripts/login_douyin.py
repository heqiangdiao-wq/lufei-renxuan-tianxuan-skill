"""抖音扫码登录（一次性，专用小号）— 重建版 2026-10-15

⚠️ 为什么 profile 放在 state/ 而不是 _probe/：
   2026-10-15 整个 _probe/ 目录被外部机制永久清空（不走回收站、不走 trash），
   里面的登录态、5647 条视频元数据、待采清单、术语表全部丢失。
   corpus/ 与 state/ 未受影响 → 此后所有持久数据一律放这两个目录。

用法：python scripts/login_douyin.py
"""
import os, sys, time, json

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
PROFILE = os.path.join(STATE, "dy_profile")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

LOGIN_COOKIES = ["sessionid", "sessionid_ss", "sid_tt", "uid_tt_ss"]
WAIT_TOTAL = 600          # 最长等待 10 分钟


def main():
    os.makedirs(STATE, exist_ok=True)
    ok = False
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"],
            locale="zh-CN", user_agent=UA,
            viewport={"width": 1360, "height": 850})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("打开抖音首页 ...", flush=True)
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print("  goto 异常:", type(e).__name__, flush=True)
        page.wait_for_timeout(5000)

        for sel in ["[data-e2e='login-button']", "text=登录", "button:has-text('登录')"]:
            try:
                page.click(sel, timeout=4000)
                print(f"  已自动点击登录入口（{sel}）", flush=True)
                page.wait_for_timeout(4000)
                break
            except Exception:
                continue

        print("\n" + "=" * 58, flush=True)
        print("👉 请在弹出的浏览器窗口里，用**手机抖音 App 扫码登录**", flush=True)
        print("   （扫的是专门用于采集的小号，不是主号）", flush=True)
        print(f"   最长等待 {WAIT_TOTAL // 60} 分钟 ...", flush=True)
        print("=" * 58 + "\n", flush=True)

        hit = []
        for i in range(WAIT_TOTAL // 5):
            page.wait_for_timeout(5000)
            try:
                names = {c["name"] for c in ctx.cookies()}
            except Exception:
                names = set()
            hit = [n for n in LOGIN_COOKIES if n in names]
            if hit:
                ok = True
                break
            if i % 12 == 11:
                print(f"  ... 已等待 {(i + 1) * 5}s", flush=True)

        if ok:
            page.wait_for_timeout(4000)
            names = sorted({c["name"] for c in ctx.cookies()})
            with open(os.path.join(STATE, "dy_profile_info.json"), "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "profile_dir": PROFILE,
                           "login_cookie_present": hit,
                           "cookie_count": len(names),
                           "cookie_names": names}, f, ensure_ascii=False, indent=1)
            print(f"\n  ✅ 登录成功，凭据已保存（cookie {len(names)} 条）", flush=True)
            print(f"     profile: {PROFILE}", flush=True)
        else:
            print("\n  ❌ 超时，未检测到登录凭据。可重新运行本脚本。", flush=True)
        try:
            ctx.close()
        except Exception:
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
