#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L3 主题库的第一步：把 617 条素材按「来问的人是什么处境」归类。

为什么按处境而不按概念分桶
  实测语料里现实类词覆盖远高于抽象类（关系 1084 句 / 痛苦 968 / 工作 843
  vs 觉醒 11 / 余量 3）。他强在「拿框架解释具体处境」，不在讲抽象概念。
  所以桶名要长得像用户的提问，不像教科书目录。

本脚本只做**关键词初筛**，不追求准确率 —— 目的是把 384.9 万字压成一张
「主题地图」，让人一眼看出分桶合不合理、哪桶空、哪桶过载。
真正的归类要靠后续人工/模型复核。

产出
  corpus/TOPIC_MAP.md   人读：每桶句数/条数/饱和度，未归类占比
  corpus/topics.json    机器用：每条素材的主/次主题
"""
import os
import re
import json
import glob
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "corpus", "processed")
OUT_MD = os.path.join(ROOT, "corpus", "TOPIC_MAP.md")
OUT_JSON = os.path.join(ROOT, "corpus", "topics.json")

# 桶 = 用户的处境，不是学术概念。关键词尽量具体，避免「关系」「价值」这类泛词吃满
TOPICS = {
    "原生家庭与父母": ["父母", "爸妈", "我爸", "我妈", "原生家庭", "父母亲", "父亲", "母亲", "亲情"],
    "孩子与教育": ["孩子", "儿子", "女儿", "教育", "高考", "补课", "学校", "老师", "家长", "叛逆"],
    "婚恋与伴侣": ["结婚", "婚姻", "老婆", "老公", "女朋友", "男朋友", "谈恋爱", "恋爱", "相亲", "离婚", "分手", "爱情"],
    "职场与打工": ["上班", "打工", "老板", "同事", "领导", "辞职", "员工", "面试", "加班", "裁员", "打工"],
    "创业与生意": ["创业", "生意", "合伙", "开店", "客户", "供应链", "商业模式", "项目"],
    "赚钱与财富": ["赚钱", "财富", "有钱", "财富自由", "收入", "工资", "发财", "挣钱"],
    "缺钱与负债": ["负债", "欠钱", "没钱", "借钱", "贷款", "破产", "穷", "还债"],
    "阶层与出身": ["阶层", "出身", "阶级", "底层", "富人", "穷人", "圈子", "寒门"],
    "情绪与痛苦": ["痛苦", "难受", "抑郁", "内耗", "崩溃", "委屈", "愤怒", "生气", "伤心", "煎熬"],
    "焦虑与恐惧": ["焦虑", "恐惧", "害怕", "担心", "不安", "压力", "慌张"],
    "欲望与贪婪": ["欲望", "贪婪", "贪心", "执念", "想得到", "舍不得"],
    "选择与决策": ["选择", "决定", "取舍", "纠结", "犹豫", "选错", "两条路"],
    "命运与人选天选": ["人选", "天选", "命运", "注定", "运气", "命好", "命不好"],
    "认知与思维": ["认知", "思维", "框架", "逻辑", "看透", "本质", "想明白", "理解"],
    "人性与识人": ["人性", "看人", "识人", "人品", "判断人", "看穿"],
    "社交与人际": ["社交", "朋友", "人脉", "人际关系", "人情", "得罪"],
    "读书与学习": ["读书", "学习", "看书", "知识", "学历", "考试", "自学"],
    "时间与年龄": ["时间", "年龄", "年轻", "老了", "二十岁", "三十岁", "四十岁", "岁数"],
    "自由与束缚": ["自由", "束缚", "限制", "枷锁", "被困", "逃不出"],
    "规则与道德": ["规则", "道德", "底线", "法律", "规矩", "合规"],
    "善恶与因果": ["善恶", "好人", "坏人", "报应", "因果", "善良"],
    "自我与意义": ["存在感", "自我", "意义", "活着为什么", "价值观", "自我价值"],
    "国家与社会": ["国家", "社会", "中国", "美国", "政府", "时代", "政策"],
    "移民与出海": ["移民", "出国", "非洲", "菲律宾", "香港", "签证", "绿卡", "海外"],
    "管理与团队": ["管理", "团队", "带人", "下属", "组织", "扁平化"],
    "投资与资产": ["投资", "股票", "房产", "买房", "基金", "资产", "楼市"],
    "表达与沟通": ["表达", "沟通", "说话方式", "演讲", "谈判", "说服"],
    "身体与健康": ["身体", "健康", "生病", "医院", "健身", "睡眠", "熬夜"],
}

MIN_LEN = 8   # 太短的句子不参与判定（「对」「是的」这类）


def main():
    topic_hits = Counter()        # 主题 -> 命中句数
    topic_items = defaultdict(set)  # 主题 -> 素材 vid 集合
    per_item = {}
    total_sent = 0
    unlabeled = 0

    for p in sorted(glob.glob(os.path.join(PROC, "*.jsonl"))):
        vid = os.path.splitext(os.path.basename(p))[0]
        rows = []
        with open(p, encoding="utf-8") as f:
            for l in f:
                if l.strip():
                    rows.append(json.loads(l))
        if not rows:
            continue
        cnt = Counter()
        for r in rows:
            s = r.get("clean") or ""
            total_sent += 1
            if len(s) < MIN_LEN:
                continue
            hit = False
            for t, kws in TOPICS.items():
                if any(k in s for k in kws):
                    cnt[t] += 1
                    hit = True
            if not hit:
                unlabeled += 1
        for t, c in cnt.items():
            topic_hits[t] += c
            topic_items[t].add(vid)
        ranked = cnt.most_common(3)
        per_item[vid] = {
            "primary": ranked[0][0] if ranked else "未归类",
            "scores": dict(ranked),
            "chars": sum(r.get("chars", 0) for r in rows),
        }
        del rows

    # 按命中句数排序
    order = sorted(TOPICS, key=lambda t: -topic_hits.get(t, 0))

    lines = [
        "# L3 主题地图（关键词初筛）",
        "",
        f"- 素材：**{len(per_item)}** 条　句子：**{total_sent:,}** 句",
        f"- 未命中任何主题：**{unlabeled:,}** 句（{unlabeled * 100.0 / max(total_sent, 1):.1f}%）",
        "",
        "> 本表是**初筛**，用途是看分桶合不合理：哪桶过载要拆、哪桶太瘦要并、",
        "> 未归类占比太高说明关键词漏了。**不是最终归类结果。**",
        "",
        "## 各桶饱和度",
        "",
        "| 主题 | 命中句数 | 覆盖素材 | 状态 |",
        "|---|---|---|---|",
    ]
    for t in order:
        n = topic_hits.get(t, 0)
        items = len(topic_items.get(t, ()))
        if n == 0:
            state = "🔴 空桶"
        elif items >= 80:
            state = "过载·建议拆"
        elif items < 12:
            state = "偏瘦·建议并"
        else:
            state = "正常"
        lines.append(f"| {t} | {n} | {items} 条 | {state} |")

    # 主导主题分布
    prim = Counter(v["primary"] for v in per_item.values())
    lines += ["", "## 每条素材的主导主题", "", "| 主导主题 | 素材条数 |", "|---|---|"]
    for t, c in prim.most_common():
        lines.append(f"| {t} | {c} |")

    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"per_item": per_item,
                   "topic_hits": dict(topic_hits),
                   "unlabeled": unlabeled,
                   "total_sent": total_sent}, f, ensure_ascii=False, indent=1)

    print(f"素材 {len(per_item)} 条 / 句子 {total_sent:,}")
    print(f"未归类 {unlabeled:,} 句（{unlabeled * 100.0 / total_sent:.1f}%）")
    print(f"空桶 {sum(1 for t in TOPICS if topic_hits.get(t, 0) == 0)} 个")
    print("\nTop 12 桶：")
    for t in order[:12]:
        print(f"  {t:<14} {topic_hits.get(t, 0):>6} 句  {len(topic_items.get(t, ())):>4} 条")
    print(f"\n报告 -> {os.path.relpath(OUT_MD, ROOT)}")


if __name__ == "__main__":
    main()
