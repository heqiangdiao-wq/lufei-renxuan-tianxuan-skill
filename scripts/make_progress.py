#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 PROGRESS.md —— 采集进度的公开快照。

为什么需要它：
  本仓库刻意「工具开源、素材私有」（见 README），所有语料 / 登记表 / 清单
  都被 .gitignore 排除。结果是仓库里看不到采集进展，定时推送时会长期
  "nothing to commit"。

  本脚本把「进度」与「素材」切开：只统计数量与时长，**不输出任何平台 ID、
  账号名、视频标题或正文**，因此可以作为公开文件进仓库。

用法：
  python scripts/make_progress.py            # 写入 PROGRESS.md
  python scripts/make_progress.py --print    # 只打印，不写文件
"""

import os
import sys
import json
import glob
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
CORPUS = os.path.join(ROOT, "corpus")
OUT = os.path.join(ROOT, "PROGRESS.md")


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def collect():
    dy = _load_jsonl(os.path.join(STATE, "registry.douyin.jsonl"))
    bl = _load_jsonl(os.path.join(STATE, "registry.bilibili.jsonl"))

    dy_done = {str(r.get("id")) for r in dy if r.get("id")}
    bl_done = {str(r.get("id")) for r in bl if r.get("id")}

    # 长视频清单（清单里开头若干条是失败待补项，仍计入「清单」总数）
    long_ids = _load_lines(os.path.join(STATE, "douyin_long_ids.txt"))
    long_total = len(long_ids)
    long_done = len([i for i in long_ids if i in dy_done])
    long_left = long_total - long_done

    # 元数据池（短视频候选池）
    pool = _load_jsonl(os.path.join(STATE, "douyin_videos.jsonl"))
    pool_ids = {str(r.get("id") or r.get("aweme_id")) for r in pool}
    pool_left = len([i for i in pool_ids if i and i not in dy_done])

    # 音频时长：只统计已登记的
    def dur_hours(rows):
        return sum(float(r.get("duration") or 0) for r in rows) / 3600.0

    dy_h = dur_hours(dy)
    bl_h = dur_hours(bl)

    # 清洗稿
    clean_files = [p for p in glob.glob(os.path.join(CORPUS, "clean", "*.md"))
                   if os.path.basename(p) != "ALL.md"]
    clean_chars = 0
    for p in clean_files:
        try:
            with open(p, encoding="utf-8") as f:
                clean_chars += len(f.read())
        except Exception:
            pass

    # 转录产物条数
    tr_files = glob.glob(os.path.join(CORPUS, "transcripts", "*.json"))

    # 去重：切片号数量（作者名不进公开文件，只计数）
    authors = {r.get("author") for r in dy if r.get("author")}

    return {
        "gen_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dy_total": len(dy),
        "dy_long_total": long_total,
        "dy_long_done": long_done,
        "dy_long_left": long_left,
        "dy_pool_total": len(pool_ids),
        "dy_pool_left": pool_left,
        "dy_authors": len(authors),
        "bl_total": len(bl),
        "hours": dy_h + bl_h,
        "transcripts": len(tr_files),
        "clean_files": len(clean_files),
        "clean_chars": clean_chars,
    }


def render(d):
    def pct(a, b):
        return "%.1f%%" % (a * 100.0 / b) if b else "—"

    return f"""# 采集进度

> 本文件由 `scripts/make_progress.py` 自动生成，只含**数量与时长**统计，
> 不含任何平台 ID、账号名、标题或正文（刻意如此，见 README「为什么不收录素材」）。
>
> 最后更新：**{d['gen_at']}**

## 抖音

| 项 | 数值 |
|---|---|
| 已完成登记 | **{d['dy_total']}** 条 |
| 长视频清单（≥10 分钟 / 完整连麦为主） | {d['dy_long_total']} 条 |
| ├ 已完成 | {d['dy_long_done']} 条（{pct(d['dy_long_done'], d['dy_long_total'])}） |
| └ 待采 | **{d['dy_long_left']}** 条 |
| 短视频候选池 | {d['dy_pool_total']} 条 |
| └ 池内待采 | {d['dy_pool_left']} 条 |
| 覆盖切片号 | {d['dy_authors']} 个 |

## B 站

| 项 | 数值 |
|---|---|
| 已完成登记 | **{d['bl_total']}** 条 |

> UP 主清单与视频清单为本地私有数据，未随仓库分发。

## 语料产出

| 项 | 数值 |
|---|---|
| 音频总时长（已登记） | **{d['hours']:.1f}** 小时 |
| 转录 JSON | {d['transcripts']} 份 |
| 清洗稿（`corpus/clean/`） | {d['clean_files']} 份 |
| 清洗稿字数 | 约 {d['clean_chars']:,} 字 |

## 说明

- 音频 / 转录 / 清洗稿 / 登记表**均不在本仓库内**（版权与安全考虑），
  本表只是本地采集状态的镜像。
- 采集为低频节律（每条间隔 20–45 秒），并受本机内存门控约束，
  因此进度是**阶梯式**增长而非匀速。
"""


def main():
    d = collect()
    text = render(d)
    if "--print" in sys.argv:
        sys.stdout.write(text)
        return
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("已写入 %s" % OUT)
    print("抖音 %d 条 | 长视频 %d/%d | B站 %d 条 | 音频 %.1f 小时 | 清洗稿 %d 份"
          % (d["dy_total"], d["dy_long_done"], d["dy_long_total"],
             d["bl_total"], d["hours"], d["clean_files"]))


if __name__ == "__main__":
    main()
