"""共享登记表（**按平台分文件**，从根上避免并发冲突）

设计变更说明：
  第一版用"文件锁 + 读改写"，实测在 4 进程并发压测下会互相等待到超时 —— 太重。
  改为 **每个平台写自己的文件**：
      state/registry.bilibili.jsonl
      state/registry.douyin.jsonl
      state/registry.<platform>.jsonl
  读取时把所有分片合并（同一 id 取 updated_at 最新的）。
  这样 B站进程和抖音进程各写各的，永远不冲突，也不需要锁。
"""
import os, json, glob, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
LEGACY = os.path.join(STATE_DIR, "registry.jsonl")
os.makedirs(STATE_DIR, exist_ok=True)


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _shard(platform):
    return os.path.join(STATE_DIR, f"registry.{platform or 'unknown'}.jsonl")


def _read_one(path):
    reg = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                reg[r["id"]] = r
            except Exception:
                pass
    return reg


def load():
    """合并所有分片（同一 id 取 updated_at 最新）"""
    reg = {}
    paths = glob.glob(os.path.join(STATE_DIR, "registry.*.jsonl"))
    if os.path.exists(LEGACY):
        paths.append(LEGACY)
    for p in paths:
        for vid, r in _read_one(p).items():
            cur = reg.get(vid)
            if cur is None or str(r.get("updated_at", "")) >= str(cur.get("updated_at", "")):
                reg[vid] = r
    return reg


def _write_shard(platform, reg):
    """只重写该平台的分片文件（原子替换）"""
    path = _shard(platform)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in reg.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def upsert(vid, platform=None, **kw):
    """写自己平台的分片，不碰别人的文件"""
    # 先看现有记录里有没有平台信息（避免调用方忘传）
    existing = load().get(vid) or {}
    plat = platform or existing.get("platform") or "unknown"

    reg = _read_one(_shard(plat))
    rec = reg.get(vid, {"id": vid, "platform": plat, "added_at": now()})
    rec.update(kw)
    if platform:
        rec["platform"] = platform
    rec["updated_at"] = now()
    reg[vid] = rec
    _write_shard(plat, reg)
    return rec


def stats():
    from collections import Counter
    reg = load()
    by_plat = Counter(r.get("platform") or "unknown" for r in reg.values())
    by_status = Counter(r.get("status") for r in reg.values())
    return len(reg), dict(by_plat), dict(by_status)


def migrate_legacy():
    """把老的单文件登记表拆分到分片"""
    if not os.path.exists(LEGACY):
        return 0
    reg = _read_one(LEGACY)
    n = 0
    for vid, r in reg.items():
        upsert(vid, platform=r.get("platform") or "bilibili",
               **{k: v for k, v in r.items() if k not in ("id", "platform")})
        n += 1
    os.rename(LEGACY, LEGACY + ".migrated")
    return n


if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        print("迁移老登记表：", migrate_legacy(), "条")
    total, bp, bs = stats()
    print(f"登记表总计 {total} 条")
    print(f"  按平台: {bp}")
    print(f"  按状态: {bs}")
