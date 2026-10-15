"""把 state/ 的关键数据备份到「项目外」的独立目录

背景：2026-10-15 项目内的 `_probe/` 目录被外部机制永久清空（不走回收站、不走沙盒 trash），
      损失 5647 条视频元数据、待采清单、19 个号的 sec_uid、登录态与术语表。
      `corpus/` 与 `state/` 当时未受影响，但为防再次发生同类事故，关键数据必须
      **在项目目录之外**再留一份。

备份内容（只备"可再生成本高"的小文件，不备音视频大文件）：
  state/real_uids.txt        号清单（sec_uid）
  state/douyin_videos.jsonl  视频元数据
  state/douyin_ids.txt       待采清单
  state/douyin_long_ids.txt  长视频清单
  state/terms.yaml           术语校正表
  state/registry.*.jsonl     采集登记表

用法：python scripts/backup_state.py
"""
import os, sys, shutil, time, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
# 备份目录刻意放在**项目目录之外**（项目内目录可能被清理机制误删）
# 默认取项目上一级的 _lufei_backup，可用环境变量 LUFEI_BACKUP 覆盖
BACKUP_ROOT = os.environ.get("LUFEI_BACKUP") or os.path.join(os.path.dirname(ROOT), "_lufei_backup")

KEEP = ["real_uids.txt", "douyin_videos.jsonl", "douyin_ids.txt",
        "douyin_long_ids.txt", "terms.yaml", "dy_profile_info.json",
        "registry.douyin.jsonl", "registry.bilibili.jsonl"]
KEEP_GLOB = ["userpage_done.txt", "related_seeds_done.txt"]

# 登录态核心文件（整个 profile 有 350MB+，但真正决定登录的只有这几个文件，约 100KB）
# 丢了就要重新扫码，所以必须备份。
PROFILE = os.path.join(STATE, "dy_profile")
COOKIE_FILES = [
    ("Default/Network/Cookies", "profile_Cookies"),
    ("Local State",             "profile_LocalState"),
    ("Default/Preferences",     "profile_Preferences"),
]


def main():
    if not os.path.isdir(STATE):
        print("state/ 不存在"); return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(dest, exist_ok=True)

    total = 0
    copied = []
    names = list(KEEP)
    for g in KEEP_GLOB:
        names += [os.path.basename(p) for p in glob.glob(os.path.join(STATE, g))]

    for n in names:
        src = os.path.join(STATE, n)
        if os.path.exists(src) and os.path.getsize(src) > 0:
            shutil.copyfile(src, os.path.join(dest, n))
            sz = os.path.getsize(src)
            total += sz
            copied.append(f"{n} ({sz:,}B)")

    # 登录态核心文件（抖音采集依赖它；丢了要重新扫码，所以单独挑出来备份）
    for rel, out_name in COOKIE_FILES:
        src = os.path.join(PROFILE, rel.replace("/", os.sep))
        if os.path.exists(src) and os.path.getsize(src) > 0:
            try:
                shutil.copyfile(src, os.path.join(dest, out_name))
                sz = os.path.getsize(src)
                total += sz
                copied.append(f"🔑 {out_name} ({sz:,}B)")
            except Exception as e:
                copied.append(f"🔑 {out_name} 跳过（{type(e).__name__}，浏览器可能正在占用）")

    # 保留最近 10 份，更早的删掉
    old = sorted([d for d in glob.glob(os.path.join(BACKUP_ROOT, "*")) if os.path.isdir(d)])
    removed = 0
    for d in old[:-10]:
        try:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        except Exception:
            pass

    print(f"✅ 备份完成 → {dest}")
    print(f"   文件 {len(copied)} 个，合计 {total/1024:.1f} KB")
    for c in copied:
        print(f"     {c}")
    if removed:
        print(f"   清理旧备份 {removed} 份（保留最近 10 份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
