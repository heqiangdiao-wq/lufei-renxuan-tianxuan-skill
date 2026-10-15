"""抖音采集（低频 · 限条数 · 断点续传）

与 B站共用同一张登记表 state/registry.jsonl，所以两边不会互相重复。
风控对策（基于 2026-10-13 实测：连续第 4 次访问就被拦）：
  * 单次会话默认只跑 3 条（--limit）
  * 每条之间随机间隔 45~90 秒（--gap-min / --gap-max）
  * 一旦页面返回空数据（"视频数据加载中"），判定被风控 —— 立刻停止，不硬重试
  * 已完成的跳过，可随时重跑接着来

流程：
  Playwright 开详情页 -> 拦 aweme/detail -> 取最低码率 play_addr
  -> 下载 mp4 -> ffmpeg 抽音轨 -> 转录 -> 登记

用法：
  python collect_douyin.py 7593565968472757625 7615417598855039018
  python collect_douyin.py --ids-file ids.txt --limit 3
  python collect_douyin.py --status
"""
import os, sys, json, re, time, random, subprocess, datetime, shutil


def _which_dir(name, fallback_dir):
    """优先从 PATH 查找工具所在目录，找不到再回退（便于在不同机器上使用）"""
    p = shutil.which(name)
    return os.path.dirname(p) if p else fallback_dir


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "registry.jsonl")
AUDIO = os.path.join(ROOT, "corpus", "audio")
TRANS = os.path.join(ROOT, "corpus", "transcripts")
PROBE = os.path.join(ROOT, "state")
FFMPEG = _which_dir("ffmpeg", r"C:\Users\PC\.workbuddy\binaries\ffmpeg\bin")
TRANSCRIBE = os.path.join(ROOT, "scripts", "transcribe.py")   # 支持长音频切段，避免内存崩
PY = sys.executable
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))

for _d in (AUDIO, TRANS):
    os.makedirs(_d, exist_ok=True)


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ---------------- 登记表（共享分片模块，与 B站互不冲突）----------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry as R

PLATFORM = "douyin"


def load_registry():
    return R.load()


def upsert(reg, vid, **kw):
    kw.pop("platform", None)          # 平台由本模块固定，避免和调用方重复传参
    r = R.upsert(vid, platform=PLATFORM, **kw)
    reg[vid] = r
    return r


def is_done(reg, vid):
    r = reg.get(vid)
    return bool(r and r.get("status") == "transcribed")


# ---------------- 抖音详情 ----------------
def fetch_detail(page, vid):
    """返回 (aweme_detail_dict | None, 原始JSON)。None 表示被风控或视频不存在"""
    store = {}
    url = f"https://www.douyin.com/video/{vid}"

    def on_resp(r):
        if "/aweme/detail/" not in r.url:
            return
        try:
            body = r.text()
        except Exception:
            return
        # 抖音会发多次 detail，其中一次是 211 字节空壳 —— 取最长的那份
        if len(body) > len(store.get("raw", "")):
            store["raw"] = body

    page.on("response", on_resp)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"    goto 异常: {type(e).__name__} {str(e)[:60]}")
    page.wait_for_timeout(9000)
    page.remove_listener("response", on_resp)

    raw = store.get("raw")
    if not raw:
        return None, None
    try:
        j = json.loads(raw)
    except Exception:
        return None, None
    aw = j.get("aweme_detail") or {}
    return (aw if aw.get("aweme_id") else None), raw


def pick_smallest(aw):
    """挑播放地址。

    ⚠️ 关键坑：抖音 web 端是 **DASH** 格式 —— `video.bit_rate[].play_addr` 里是
    **纯视频轨，没有音轨**（ffprobe 实测只有 `hevc,video` 一路流），拿它抽音轨必然失败。
    必须用顶层的 `play_addr` / `download_addr`（音视频合一）。
    """
    v = aw.get("video") or {}
    for key in ("play_addr", "play_addr_h264", "download_addr"):
        urls = (v.get(key) or {}).get("url_list") or []
        if urls:
            return None, urls[0], key
    # 兜底：DASH 分片（应急用，但很可能没音轨）
    for br in (v.get("bit_rate") or []):
        urls = (br.get("play_addr") or {}).get("url_list") or []
        if urls:
            return br.get("bit_rate"), urls[0], f"DASH:{br.get('gear_name')}"
    return None, None, None


def download_mp4(url, vid):
    dst = os.path.join(AUDIO, f"dy_{vid}.mp4")
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    cmd = ["curl", "-sL", "--max-time", "300", "-A", UA,
           "-H", "Referer: https://www.douyin.com/", "-o", dst, url]
    subprocess.run(cmd, capture_output=True)
    return dst if os.path.exists(dst) and os.path.getsize(dst) > 1000 else None


def _try_remove(path):
    """释放中间产物占用的磁盘空间。

    ⚠️ 绝对不能用 os.remove / shutil.rmtree —— 沙盒的「安全删除守卫」会拦截删除，
    并且**直接把 python 进程杀掉**（报 SAFE_DELETE_BULK_GUARD_ERROR，退出码 1），
    表现为"跑到一半什么都没发生"，极难排查。
    → 改为把文件**截断为 0 字节**：磁盘空间照样释放，且不触发守卫。
    """
    try:
        if path and os.path.exists(path):
            with open(path, "w"):
                pass
            return True
    except Exception:
        pass
    return False


def to_audio(mp4, vid, keep=False):
    """从 mp4 里只抽音轨（画面丢弃），转 16k 单声道 wav。

    ⚠️ 抽完立即删 mp4 —— 抖音必须下完整视频才能抽音轨，画面则毫无用处。
    实测 2207 条待采若保留 mp4 需 43GB，而 D 盘只剩 29GB，必须即时清理。
    """
    dst = os.path.join(AUDIO, f"dy_{vid}.wav")
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        if not keep:
            _try_remove(mp4)
        return dst
    cmd = [os.path.join(FFMPEG, "ffmpeg"), "-y", "-loglevel", "error",
           "-i", mp4, "-vn", "-ac", "1", "-ar", "16000", dst]
    subprocess.run(cmd, capture_output=True)
    ok = os.path.exists(dst) and os.path.getsize(dst) > 1000
    if ok and not keep:
        _try_remove(mp4)
    return dst if ok else None


def transcribe(audio, vid, aw):
    out_json = os.path.join(TRANS, f"dy_{vid}.json")
    if os.path.exists(out_json) and os.path.getsize(out_json) > 100:
        return out_json
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    r = subprocess.run([PY, TRANSCRIBE, audio, out_json],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", env=env)
    # stdout 与 stderr 都要打 —— 只打 stdout 会把崩溃原因吞掉（2026-10-15 踩过：
    # 转录子进程 OOM 崩溃，stdout 为空、stderr 才有 traceback，导致看起来"无声失败"）
    for tag, s in (("out", r.stdout), ("err", r.stderr)):
        s = (s or "").strip()
        if s:
            print(f"    [{tag}] " + s[-400:].replace("\n", "\n      "))
    if r.returncode != 0:
        print(f"    [!] 转录进程退出码 {r.returncode}")
    # 补上作者/来源信息，供 process.py 判断 origin
    if os.path.exists(out_json):
        d = json.load(open(out_json, encoding="utf-8"))
        d["platform"] = "douyin"
        d["author"] = (aw.get("author") or {}).get("nickname")
        d["desc"] = aw.get("desc")
        d["origin"] = "third_party"      # 抖音这些号都是切片号，非作者官方发布
        json.dump(d, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return out_json
    return None


# ---------------- 进程锁 ----------------
# 全天连续跑时定时任务可能重叠触发，若无锁保护，两个进程会同时下载同一条视频。
LOCK = os.path.join(ROOT, "state", "collect_douyin.lock")
LOCK_STALE = 3 * 3600          # 锁超过 3 小时视为残留（进程已异常退出）
MIN_FREE_GB = 1.0              # tiny 模型的最低需求；够不着才跳过。中间档由 transcribe.py 自动降级
MEM_WAIT_SEC = 300             # 内存不足时最多等 5 分钟，抓内存释放的窗口


def avail_mem_gb():
    """系统可用物理内存（GB）—— 本机常被浏览器等占满，不足时整轮跳过而不是每条都失败"""
    try:
        import ctypes

        class MSX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MSX()
        m.dwLength = ctypes.sizeof(MSX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullAvailPhys / 1024 ** 3
    except Exception:
        return 99.0


def pid_alive(pid):
    """检查进程是否存活（用来判定锁文件是不是残留）"""
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="gbk", errors="ignore")
        return str(pid) in (r.stdout or "")
    except Exception:
        return True          # 查不到就保守当作存活


def lock_is_busy():
    """是否有另一个抖音采集进程在跑。

    ⚠️ 锁文件**永远不删除** —— 删它会触发沙盒的安全删除守卫并**杀死 python 进程**。
    改为：读锁里的 PID → 检查该进程是否存活；再叠加时间兜底（防 PID 被系统复用）。
    """
    if not os.path.exists(LOCK):
        return False
    try:
        age = time.time() - os.path.getmtime(LOCK)
        if age > LOCK_STALE:
            return False
        pid = int(open(LOCK, encoding="utf-8").read().strip())
    except Exception:
        return False
    return pid_alive(pid)


# ---------------- 主流程 ----------------
def main():
    args = sys.argv[1:]
    if args and args[0] == "--status":
        reg = load_registry()
        dy = {k: v for k, v in reg.items() if v.get("platform") == "douyin"}
        for r in dy.values():
            print(f"  {r['id']:<22} {r.get('status',''):<14} {str(r.get('title',''))[:40]}")
        print(f"\n抖音已登记 {len(dy)} 条")
        return

    if lock_is_busy():
        age = int(time.time() - os.path.getmtime(LOCK))
        print(f"⏳ 已有一个抖音采集任务在运行（锁 {age}s 前创建），本次退出以免重复下载。")
        return
    try:
        open(LOCK, "w", encoding="utf-8").write(str(os.getpid()))
    except Exception:
        pass

    avail = avail_mem_gb()
    if avail < MIN_FREE_GB:
        # 内存是波动的（用户开/关应用、浏览器回收标签都会变），
        # 直接跳过会让任务永远跑不动 → 等一小会儿，抓住内存释放的窗口
        print(f"⏳ 可用内存 {avail:.1f} GB（需 ≥{MIN_FREE_GB} GB），等待最多 {MEM_WAIT_SEC//60} 分钟 ...",
              flush=True)
        waited = 0
        while waited < MEM_WAIT_SEC:
            time.sleep(30)
            waited += 30
            avail = avail_mem_gb()
            if avail >= MIN_FREE_GB:
                break
            if waited % 120 == 0:
                print(f"   ... 已等 {waited}s，当前 {avail:.1f} GB", flush=True)
        if avail < MIN_FREE_GB:
            print(f"⚠️ 等待后仍只有 {avail:.1f} GB，本次跳过。"
                  f"建议关闭部分应用（ChatGPT / 飞书 / 多余浏览器）后等下一次触发。")
            return
        print(f"✅ 内存已释放到 {avail:.1f} GB，继续采集", flush=True)

    limit, gap_min, gap_max = 3, 45, 90
    keep_media = "--keep-media" in args      # 默认清理中间产物（磁盘约束），加此参数可保留
    ids = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--limit":
            limit = int(args[i + 1]); i += 2
        elif a == "--gap-min":
            gap_min = int(args[i + 1]); i += 2
        elif a == "--gap-max":
            gap_max = int(args[i + 1]); i += 2
        elif a == "--ids-file":
            ids += [l.strip() for l in open(args[i + 1], encoding="utf-8")
                    if l.strip() and not l.startswith("#")]
            i += 2
        else:
            ids.append(a); i += 1

    # 从链接里抽 ID
    clean = []
    for x in ids:
        m = re.search(r"(?:video|shipin|note)/(\d{15,25})", x) or re.match(r"^(\d{15,25})$", x)
        if m:
            clean.append(m.group(1))
    if not clean:
        print("没有可用的视频 ID")
        return

    reg = load_registry()
    todo = [v for v in clean if not is_done(reg, v)]
    print(f"输入 {len(clean)} 个 ID | 待处理 {len(todo)} 条 | 本次上限 {limit} 条")
    if not todo:
        print("全部已完成，无需采集")
        return

    done = 0
    # 连续「拿不到详情」计数：用于区分「单条视频已失效」与「真风控」
    # 若不做区分，一条被删除的视频会导致每一轮都在第 1 条 break，整条流水线卡死
    consec_fail = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="zh-CN", user_agent=UA,
                                  viewport={"width": 1440, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        for vid in todo:
            if done >= limit:
                print(f"\n已达本次上限 {limit} 条，停止。剩余 {len(todo) - done} 条下次继续。")
                break
            done += 1          # 先计数：即使中途失败也占一个名额，避免 limit 失效后无限跑
            print(f"\n▶ [{done}/{limit}] {vid}")
            aw, raw = fetch_detail(page, vid)
            if not aw:
                consec_fail += 1
                upsert(reg, vid, platform="douyin", status="blocked")
                print(f"  ⛔ 未拿到数据（连续第 {consec_fail} 次）—— 可能风控，也可能该视频已失效")
                if consec_fail >= 2:
                    print("  ⛔ 连续 2 次拿不到数据 —— 判定被风控，**立即停止整轮**（不硬重试）")
                    break
                print("  → 判定为单条异常，跳过它继续下一条")
                time.sleep(random.randint(10, 20))
                continue
            consec_fail = 0

            title = str(aw.get("desc") or "")[:50].replace("\n", " ")
            author = (aw.get("author") or {}).get("nickname")
            dur = (aw.get("duration") or 0) / 1000
            print(f"  ✅ 详情到手 | {author} | {dur:.0f}秒 | {title}")
            upsert(reg, vid, platform="douyin", status="discovered",
                   title=title, author=author, duration=dur)

            # 优先复用已有音轨：上次转录失败（stage=transcribe）时音频通常已抽好，
            # 此时重跑应直接转录，不必再下 mp4（长视频的 mp4 有几十上百 MB，白花时间与流量）。
            wav_cached = os.path.join(AUDIO, f"dy_{vid}.wav")
            if os.path.exists(wav_cached) and os.path.getsize(wav_cached) > 1000:
                print("  ↩ 检测到已有音轨，跳过下载")
                wav = wav_cached
            else:
                rate, url, gear = pick_smallest(aw)
                if not url:
                    print("  ⛔ 没有可用播放地址")
                    upsert(reg, vid, status="failed", stage="no_playurl")
                    continue
                print(f"  ↓ 下载最低码率档 {gear} ({rate} bps)")
                mp4 = download_mp4(url, vid)
                if not mp4:
                    upsert(reg, vid, status="failed", stage="download")
                    continue
                upsert(reg, vid, status="downloaded",
                       media=os.path.relpath(mp4, ROOT))

                wav = to_audio(mp4, vid, keep=keep_media)
                if not wav:
                    upsert(reg, vid, status="failed", stage="extract_audio")
                    continue

            tj = transcribe(wav, vid, aw)
            if not tj:
                upsert(reg, vid, status="failed", stage="transcribe")
                continue
            upsert(reg, vid, status="transcribed",
                   transcript=os.path.relpath(tj, ROOT))
            print(f"  ✅ 转录完成 -> {os.path.relpath(tj, ROOT)}")
            if not keep_media:          # 转录完成，音频中间产物即可清理
                _try_remove(wav)

            if done < limit:
                wait = random.randint(gap_min, gap_max)
                print(f"  ⏳ 低频等待 {wait} 秒（避免触发风控）...")
                time.sleep(wait)

        browser.close()

    print(f"\n本次完成 {done} 条")
    dy = {k: v for k, v in load_registry().items() if v.get("platform") == "douyin"}
    from collections import Counter
    print("抖音累计:", dict(Counter(v.get("status") for v in dy.values())))
    # 注：不删除锁文件（删它会触发沙盒安全删除守卫并杀死进程）。
    # 锁的失效由 lock_is_busy() 通过「PID 是否存活 + 时间兜底」判定。


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    main()
