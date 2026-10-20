#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
修正来源分类：把「第三方」拆成「朗读原文」与「解读评论」。

为什么必须拆
  third_party 原来是个大筐，混了两类性质完全不同的东西：
    · 他人朗读/复述他的原文 —— 文本可信，但不能标成「他说」
    · 他人自己的解读评论   —— 内容不可当作他的观点，只能当线索
  在主题 04 里已经实拦下两条（福星2077 的「吃苦享福」「穷人思维」被误当他的话）。

判定顺序（数据驱动，不靠猜）
  1. 账号名含「切片」→ author_voice_repost
     这类账号就是搬运他直播录音的，名字已经说明一切。
     （旧 ORIGIN_MAP 是手工按 B站 UP 主映射的，把这批漏进了 third_party）
  2. 与原声/切片语料的 4-gram 重合度 ≥ 60% → repost_read
     重合这么高，只可能是照着他的原话念。
  3. 其余 → commentary

产出 state/origin_fix.json，由 process.py 优先读取。
"""
import os
import re
import json
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "corpus", "processed")
STATE = os.path.join(ROOT, "state")
OUT = os.path.join(STATE, "origin_fix.json")

PUNCT = re.compile(r"[\s\u3000-\u303f\uff00-\uffef,.!?;:\"'()\[\]{}|/\\]+")
VOICE = ("author_voice", "author_voice_repost")
READ_TH = 0.60          # 朗读原文的重合度门槛
SLICE_HINT = ("切片",)


def norm(s):
    return PUNCT.sub("", s or "").lower()


def grams(t, k=4):
    t = norm(t)
    return {hash(t[i:i + k]) for i in range(max(0, len(t) - k + 1))}


def main():
    orig = set()
    items = []
    for p in sorted(glob.glob(os.path.join(PROC, "*.jsonl"))):
        rows = []
        with open(p, encoding="utf-8") as f:
            for l in f:
                if l.strip():
                    rows.append(json.loads(l))
        if not rows:
            continue
        s = rows[0]["source"]
        vid = os.path.basename(p)[:-6]
        g = grams("".join(r["clean"] for r in rows))
        if s.get("origin") in VOICE:
            orig |= g
        else:
            items.append((vid, s.get("author") or "", s.get("origin"), g))

    fix = {}
    stat = {"author_voice_repost": 0, "repost_read": 0, "commentary": 0}
    detail = []

    for vid, author, old, g in items:
        ov = len(g & orig) / max(len(g), 1)
        if any(h in author for h in SLICE_HINT):
            new, why = "author_voice_repost", "账号名含「切片」"
        elif ov >= READ_TH:
            new, why = "repost_read", f"与原声重合 {ov * 100:.0f}%"
        else:
            new, why = "commentary", f"与原声重合仅 {ov * 100:.0f}%"
        fix[vid] = {"from": old, "to": new, "author": author,
                    "overlap": round(ov, 3), "why": why}
        stat[new] += 1
        detail.append((ov, vid, author, old, new, why))

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fix, f, ensure_ascii=False, indent=1)

    detail.sort(reverse=True)
    print(f"待修正 {len(items)} 条\n")
    print(f"{'重合':>6}  {'素材':<26} {'账号':<22} {'修正为':<20} 依据")
    print("-" * 104)
    for ov, vid, author, old, new, why in detail:
        print(f"{ov * 100:5.1f}%  {vid:<26} {author[:20]:<22} {new:<20} {why}")
    print()
    print("汇总：")
    for k, v in stat.items():
        print(f"  {k:<22} {v:>3} 条")
    print(f"\n写入 {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
