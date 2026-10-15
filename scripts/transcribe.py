"""音频 -> 带时间戳文本片段 -> JSON

⚠️ 两个关键设计（都是踩坑后确定的）

1) 长音频必须分段转录
   faster-whisper 会对**整个音频一次性做 STFT**，160 分钟音频需要连续分配 ~2.76 GiB
   （实测报错：`Unable to allocate 2.76 GiB for shape (1, 920973, 201)`），扛不住就崩。

2) ⚠️ 分段必须走「管道流式」，不能落地临时切片文件
   早期实现是「ffmpeg 切段写文件 → 逐段转录 → shutil.rmtree 删目录」，
   在沙盒环境里被安全删除守卫拦截：
       [safe-delete][SAFE_DELETE_BULK_GUARD_ERROR] failed to read state: Unexpected end of JSON input
   导致 242 分钟的音频转到第 12/17 段就整进程崩溃（退出码 1），且报错在 stderr 里，
   早期只打印 stdout 时完全看不到原因。
   → 现在改为：ffmpeg 输出 16k 单声道 PCM 到 stdout，Python 分块读入内存逐块转录。
     **全程零临时文件、零删除操作**，从根上绕开守卫。

用法: python transcribe.py <音频> [输出json] [--chunk 秒]
"""
import sys, os, json, time, subprocess, shutil

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import numpy as np
from faster_whisper import WhisperModel


def _which(name, fallback):
    """优先从 PATH 查找，找不到再回退到默认路径（便于在不同机器上使用）"""
    return shutil.which(name) or fallback


FFMPEG = _which("ffmpeg", r"C:\Users\PC\.workbuddy\binaries\ffmpeg\bin\ffmpeg")
FFPROBE = _which("ffprobe", r"C:\Users\PC\.workbuddy\binaries\ffmpeg\bin\ffprobe")
CHUNK_SEC = 900          # 单段 15 分钟，实测这个长度内存安全
SR = 16000
# 模型按可用内存选：small 约需 1.5GB 可用内存，base 约 0.6GB，tiny 约 0.3GB
# 本机 15.7GB 物理内存常被占用 87~94%，可用内存紧张时会加载失败
# （报错：OpenBLAS error: Memory allocation still failed / mkl_malloc: failed to allocate memory）
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")


def avail_mem_gb():
    """系统可用物理内存（GB）

    ⚠️ 本机 15.7GB 物理内存常被浏览器/ChatGPT/豆包等占掉 87~94%，
    可用内存不足时加载模型会报 `mkl_malloc: failed to allocate memory` 或
    `OpenBLAS error: Memory allocation still failed`，看起来像崩溃但其实只是内存没腾出来。
    """
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


MIN_FREE_GB = 1.0        # 低于这个连 tiny 都加载不了，直接退出

# 各模型加载所需可用内存（GB，实测估算）
# 各模型加载所需可用内存（GB，实测标定：base 在 1.74GB 可用时成功、1.5GB 时失败）
MODEL_NEED_GB = {"tiny": 1.0, "base": 1.7, "small": 2.5, "medium": 4.0, "large-v3": 6.0}


def load_model(preferred="small"):
    """按「首选 → base → tiny」的顺序加载，能加载哪个用哪个。

    本机 15.7GB 物理内存常被浏览器 / ChatGPT / 飞书 / java 等占掉 87~94%，
    small 加载会报 `mkl_malloc: failed to allocate memory` / `OpenBLAS error`。
    与其整轮失败，不如自动降级到小模型继续跑 —— JSON 里会记录实际用的模型
    （`asr.model`），内存宽裕时可以据此重跑低质量条目。
    """
    ladder = [preferred] + [m for m in ("base", "tiny") if m != preferred]
    avail = avail_mem_gb()
    last_err = None
    for name in ladder:
        need = MODEL_NEED_GB.get(name, 3.0)
        if avail < need:
            print(f"[load] 可用内存 {avail:.1f}GB < {name} 所需 {need}GB，跳过", flush=True)
            continue
        try:
            t = time.time()
            m = WhisperModel(name, device="cpu", compute_type="int8")
            print(f"[load] 模型 {name} 加载成功（{time.time()-t:.1f}s，可用 {avail:.1f}GB）",
                  flush=True)
            return m, name
        except Exception as e:
            last_err = e
            print(f"[load] 模型 {name} 加载失败（{type(e).__name__}），降级重试", flush=True)
    raise RuntimeError(
        f"所有模型都无法加载（可用内存 {avail:.1f}GB）；最后错误: {last_err}")


def audio_duration(path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=120)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def stream_chunks(src, chunk_sec):
    """ffmpeg 管道输出 16k 单声道 PCM → 按 chunk_sec 分块 yield (offset_sec, float32 ndarray)

    不落地任何文件，因此不需要任何删除操作。
    """
    chunk_bytes = SR * 2 * chunk_sec          # int16 = 2 字节
    cmd = [FFMPEG, "-v", "error", "-i", src,
           "-f", "s16le", "-ar", str(SR), "-ac", "1", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    offset = 0.0
    try:
        while True:
            buf = proc.stdout.read(chunk_bytes)
            if not buf:
                break
            audio = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                break
            yield offset, audio
            offset += audio.size / SR
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def run_asr(model, audio, offset=0.0):
    segments, info = model.transcribe(
        audio, language="zh", beam_size=5, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500))
    rows = []
    for s in segments:
        txt = s.text.strip()
        if txt:
            rows.append({"start": round(s.start + offset, 2),
                         "end": round(s.end + offset, 2),
                         "text": txt})
    return rows, info


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chunk = CHUNK_SEC
    model_size = MODEL_SIZE
    for a in sys.argv[1:]:
        if a.startswith("--chunk"):
            chunk = int(a.split("=")[1]) if "=" in a else chunk
        elif a.startswith("--model"):
            model_size = a.split("=")[1] if "=" in a else model_size
    src = args[0]
    dst = args[1] if len(args) > 1 else os.path.splitext(src)[0] + ".json"
    mid = os.path.splitext(os.path.basename(src))[0]

    total = audio_duration(src)
    need_split = total > chunk * 1.2

    avail = avail_mem_gb()
    if avail < MIN_FREE_GB:
        print(f"[!] 可用内存仅 {avail:.1f} GB，低于最低线 {MIN_FREE_GB} GB，"
              f"连 tiny 模型都加载不了。请关闭部分应用（浏览器 / ChatGPT / 飞书等）后重试。",
              flush=True)
        sys.exit(2)

    model, model_size = load_model(model_size)
    print(f"[load] | 音频 {total:.0f}s"
          f"{' | 超长，将流式分块转录' if need_split else ''}", flush=True)

    t1 = time.time()
    all_rows = []
    lang = "zh"

    if need_split:
        est = max(1, int(total // chunk) + (1 if total % chunk else 0))
        print(f"[stream] 管道流式分块（每块 ≤{chunk}s，不落地临时文件）", flush=True)
        for idx, (off, audio) in enumerate(stream_chunks(src, chunk), 1):
            rows, info = run_asr(model, audio, offset=off)
            all_rows += rows
            lang = info.language
            print(f"  [{idx}/{est}] 偏移{off:.0f}s -> {len(rows)} 段"
                  f"（累计 {len(all_rows)}）", flush=True)
    else:
        all_rows, info = run_asr(model, src)
        lang = info.language

    elapsed = time.time() - t1
    rec = {
        "source_id": mid, "platform": "bilibili", "audio_path": src,
        "duration_sec": round(total, 1),
        "asr": {"engine": "faster-whisper", "model": model_size,
                "compute": "cpu/int8", "chunked": need_split,
                "chunk_sec": chunk if need_split else None,
                "mode": "pipe-stream" if need_split else "whole-file"},
        "segments": all_rows,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)

    print(f"\n[ok] 片段 {len(all_rows)} 条 | 音频 {total:.0f}s | 转录 {elapsed:.1f}s "
          f"| 实时倍率 {elapsed/total if total else 0:.2f}x", flush=True)
    print(f"[out] {dst}", flush=True)


if __name__ == "__main__":
    main()
