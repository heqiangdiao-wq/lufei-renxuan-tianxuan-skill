"""从 Windows 回收站查找（并可选恢复）被误删的文件

背景：2026-10-15 项目里的 _probe/ 目录被整体清空（疑似与沙盒「安全删除」守卫
的 trash 机制有关）。Windows 回收站里可能有 $R/$I 形式的残留，本脚本解析
$I 记录还原原始路径，用于确认与恢复。

用法：
  python restore_from_recycle.py            # 只列出（默认，安全）
  python restore_from_recycle.py --restore  # 恢复匹配到的文件
  python restore_from_recycle.py --grep probe   # 只看路径含 probe 的
"""
import os, struct, sys, datetime, shutil

RB = "C:/$Recycle.Bin"


def candidate_dirs():
    out = []
    if not os.path.isdir(RB):
        return out
    for user in os.listdir(RB):
        d = os.path.join(RB, user)
        if os.path.isdir(d):
            out.append(d)
    return out


def parse_i(path):
    """解析 $I 元数据文件：返回 (原路径, 大小, 删除时间)"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 28:
            return None
        size = struct.unpack("<Q", data[8:16])[0]
        ts = struct.unpack("<Q", data[16:24])[0]
        ver = struct.unpack("<I", data[0:8][4:8])[0]
        # Win10 格式：路径长度 4 字节 + UTF-16 路径
        plen = struct.unpack("<I", data[24:28])[0]
        raw = data[28:28 + plen * 2]
        orig = raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
        dt = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ts / 10)
        return orig, size, dt
    except Exception:
        return None


def main():
    args = sys.argv[1:]
    do_restore = "--restore" in args
    grep = None
    for i, a in enumerate(args):
        if a == "--grep" and i + 1 < len(args):
            grep = args[i + 1].lower()

    dirs = candidate_dirs()
    if not dirs:
        print("找不到回收站目录")
        return
    print(f"回收站用户目录: {len(dirs)} 个\n")

    found = []
    for d in dirs:
        try:
            items = os.listdir(d)
        except Exception:
            continue
        for it in items:
            if not it.startswith("$I"):
                continue
            info = parse_i(os.path.join(d, it))
            if not info:
                continue
            orig, size, dt = info
            if grep and grep not in orig.lower():
                continue
            r_file = os.path.join(d, "$R" + it[2:])
            found.append((dt, size, orig, r_file, os.path.exists(r_file)))

    found.sort(reverse=True)
    print(f"匹配条目: {len(found)}\n")
    for dt, size, orig, r_file, exists in found[:60]:
        flag = "✅" if exists else "⚠️无实体"
        print(f"  {flag} {dt.strftime('%m-%d %H:%M')}  {size:>12,}  {orig}")
    if len(found) > 60:
        print(f"  ... 其余 {len(found)-60} 条")

    if do_restore and found:
        print("\n=== 开始恢复 ===")
        n = 0
        for dt, size, orig, r_file, exists in found:
            if not exists:
                print(f"  ✗ 无实体文件: {orig}")
                continue
            try:
                os.makedirs(os.path.dirname(orig), exist_ok=True)
                if os.path.exists(orig):
                    base, ext = os.path.splitext(orig)
                    orig = f"{base}.restored{ext}"
                shutil.move(r_file, orig)
                print(f"  ✅ {orig}")
                n += 1
            except Exception as e:
                print(f"  ✗ {orig}: {type(e).__name__} {e}")
        print(f"\n恢复 {n} 个文件")


if __name__ == "__main__":
    main()
