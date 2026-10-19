#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重复检测：找出切片号之间互相搬运的内容。

必要性：抖音切片号大量互相搬运同一场直播的同一段，若不去重，
「智能体数据库」里同一段话会出现十几次，直接污染检索与风格模仿。

两级检测
  L1 句子级完全重复 —— 归一化（去标点/空白/表情）后哈希相同。
     这是搬运最硬的证据，代价极低，先跑它。
  L2 整条近似重复 —— 字符 5-gram Jaccard，找出「同一场直播被切成
     多条重复上传」的情况，按组保留信息最全的一份。

产出
  corpus/DEDUP_REPORT.md     人读的报告
  corpus/clean/dedup_map.json 机器用的映射（重复件 -> 保留件）

只做**报告与标记**，不删除任何原始文件 —— 是否真的剔除由人决定。
"""
import os
import re
import json
import glob
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "corpus", "processed")
OUT_MD = os.path.join(ROOT, "corpus", "DEDUP_REPORT.md")
OUT_MAP = os.path.join(ROOT, "corpus", "clean", "dedup_map.json")

PUNCT = re.compile(r"[\s\u3000-\u303f\uff00-\uffef,.!?;:\"'`~@#$%^&*()\-_=+\[\]{}|\\/<>]+")


def norm(s):
    return PUNCT.sub("", s or "").lower()


def load_all():
    """{vid: [(idx, clean, start, end), ...]}"""
    out = {}
    for p in sorted(glob.glob(os.path.join(PROC, "*.jsonl"))):
        vid = os.path.splitext(os.path.basename(p))[0]
        rows = []
        with open(p, encoding="utf-8") as f:
            for l in f:
                if not l.strip():
                    continue
                r = json.loads(l)
                rows.append((len(rows), r.get("clean") or "",
                             (r.get("time") or {}).get("start"),
                             (r.get("time") or {}).get("end")))
        if rows:
            out[vid] = rows
    return out


def l1_sentences(data):
    """句子级完全重复"""
    norm2vid = defaultdict(set)
    norm2meta = {}
    total = 0
    for vid, rows in data.items():
        for idx, clean, st, en in rows:
            n = norm(clean)
            total += 1
            # 太短的句子（<10 字）重复是正常口语，不算搬运
            if len(n) < 10:
                continue
            norm2vid[n].add(vid)
            norm2meta.setdefault(n, (clean, st, en))

    cross = {n: v for n, v in norm2vid.items() if len(v) > 1}
    dup_units = sum(len(v) - 1 for v in cross.values())
    return total, len(cross), dup_units, cross, norm2meta


def shingles(text, k=5):
    t = norm(text)
    return {t[i:i + k] for i in range(max(0, len(t) - k + 1))}


def l2_videos(data, min_len=400):
    """整条近似重复：5-gram Jaccard"""
    texts = {}
    for vid, rows in data.items():
        full = "".join(c for _, c, _, _ in rows)
        if len(norm(full)) >= min_len:
            texts[vid] = shingles(full)
    vids = list(texts)
    n = len(vids)
    # 倒排：shingle -> 出现过的视频下标（用于候选筛选，避免 O(n^2) 全比）
    inv = defaultdict(list)
    for i, v in enumerate(vids):
        for s in texts[v]:
            inv[s].append(i)

    pairs = []
    for i in range(n):
        cnt = defaultdict(int)
        for s in texts[vids[i]]:
            for j in inv[s]:
                if j > i:
                    cnt[j] += 1
        for j, c in cnt.items():
            a, b = texts[vids[i]], texts[vids[j]]
            jac = c / (len(a) + len(b) - c)
            if jac >= 0.55:
                pairs.append((jac, vids[i], vids[j]))

    # 并查集成组
    parent = {v: v for v in vids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for jac, a, b in pairs:
        union(a, b)
    groups = defaultdict(list)
    for v in vids:
        groups[find(v)].append(v)
    multi = [sorted(g) for g in groups.values() if len(g) > 1]
    multi.sort(key=lambda g: -len(g))
    return multi, pairs


def chars_of(rows):
    return sum(len(c) for _, c, _, _ in rows)


def main():
    data = load_all()
    print(f"载入 {len(data)} 条素材")

    total, ncross, dup_units, cross, norm2meta = l1_sentences(data)
    clean_units = total - dup_units
    print(f"L1 句子级：共 {total} 句，跨视频重复 {ncross} 句，可剔除 {dup_units} 句")

    multi, pairs = l2_videos(data)
    print(f"L2 整条级：{len(multi)} 组近似重复")

    # 每组保留信息量最大（字数最多）的一条
    reserve = {}
    drop = []
    for g in multi:
        g2 = sorted(g, key=lambda v: -chars_of(data[v]))
        reserve[g2[0]] = {"group_size": len(g2), "members": g2}
        for v in g2[1:]:
            drop.append({"vid": v, "keep": g2[0], "chars": chars_of(data[v])})

    # top 搬运句
    top = sorted(cross.items(), key=lambda kv: -len(kv[1]))[:30]

    lines = ["# 重复检测报告", "",
             f"素材总数：**{len(data)}** 条　句子总数：**{total}** 句", "",
             "## L1 句子级完全重复", "",
             f"- 跨视频出现的句子：**{ncross}** 句",
             f"- 因重复可剔除：**{dup_units}** 句"
             f"（占 {dup_units * 100.0 / max(total, 1):.1f}%）",
             f"- 去重后有效句子：**{clean_units}** 句", "",
             "### 被搬运最多的 30 句", "",
             "| 出现在几个视频 | 句子（截断） |", "|---|---|"]
    for n, vids in top:
        txt = norm2meta[n][0].replace("|", "｜")[:50]
        lines.append(f"| {len(vids)} | {txt}… |")

    lines += ["", "## L2 整条近似重复（同一场直播被重复上传）", "",
              f"识别出 **{len(multi)}** 组，可精简 **{len(drop)}** 条", ""]
    if multi:
        lines += ["| 组 | 条数 | 保留（字数最多） | 建议剔除 |", "|---|---|---|---|"]
        for i, g in enumerate(multi[:20], 1):
            g2 = sorted(g, key=lambda v: -chars_of(data[v]))
            lines.append(f"| {i} | {len(g)} | `{g2[0]}`（{chars_of(data[g2[0]])} 字） "
                         f"| {', '.join('`%s`' % v for v in g2[1:4])}"
                         f"{' …' if len(g2) > 4 else ''} |")
    else:
        lines.append("（无）")

    lines += ["", "## 说明", "",
              "- 本报告只做**标记**，不删除任何原始文件。",
              "- `dedup_map.json` 给出「重复件 -> 保留件」映射，供下游选择性剔除。",
              "- L1 只统计 ≥10 字的句子：更短的口头重复（「对吧」「就是」）属正常口语，不视为搬运。",
              ""]

    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    with open(OUT_MAP, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"drop": drop, "reserve": reserve,
                   "l1": {"total": total, "cross": ncross, "dup_units": dup_units}},
                  f, ensure_ascii=False, indent=1)

    print(f"报告 -> {os.path.relpath(OUT_MD, ROOT)}")
    print(f"映射 -> {os.path.relpath(OUT_MAP, ROOT)}")


if __name__ == "__main__":
    main()
