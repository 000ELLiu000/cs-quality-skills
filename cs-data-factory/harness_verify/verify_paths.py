#!/usr/bin/env python3
"""
verify_paths.py
---------------
Phase 0 节点校验：检查路径骨架 JSONL（{sop_id}_paths.jsonl）的格式完整性。

格式层校验（不做语义判断）：
  1. 每条记录必须字段完整（path_id / sop_id / signals / actions / turns / has_outbound）
  2. path_id 格式为 PNNN，唯一无重复
  3. actions 列表非空，每条 action 有 action_id / desc / 关联动作 / 共识动作
  4. turns 与 actions 列表长度一致
  5. has_outbound 与 actions 中含 outbound=True 的情况一致
  6. signals 字典非空（至少含一个信号字段）

用法：
python harness_verify/verify_paths.py --sop-id invoice
python harness_verify/verify_paths.py --paths data/paths/invoice_paths.jsonl   # 路径不变
"""

import json
import re
import sys
import argparse
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
PATHS_DIR = SKILL_ROOT / "data" / "paths"

REQUIRED_FIELDS = {"path_id", "sop_id", "signals", "actions", "turns", "has_outbound"}
REQUIRED_ACTION_FIELDS = {"action_id", "desc", "关联动作", "共识动作"}


def verify_record(record: dict, idx: int) -> list:
    issues = []
    row_label = f"第{idx+1}条（{record.get('path_id', '?')}）"

    # 必须字段
    missing = REQUIRED_FIELDS - set(record.keys())
    if missing:
        issues.append(f"[缺字段] {row_label} 缺少：{sorted(missing)}")
        return issues  # 无法继续

    # path_id 格式
    if not re.match(r"^P\d{3,}$", record["path_id"]):
        issues.append(f"[格式错误] {row_label} path_id 格式不符（期望 PNNN）：{record['path_id']!r}")

    # signals 非空
    if not isinstance(record["signals"], dict) or not record["signals"]:
        issues.append(f"[空信号] {row_label} signals 为空字典")

    # actions 非空
    actions = record["actions"]
    if not isinstance(actions, list) or not actions:
        issues.append(f"[空动作] {row_label} actions 为空列表")
        return issues

    # action 字段完整
    for j, act in enumerate(actions):
        missing_act = REQUIRED_ACTION_FIELDS - set(act.keys())
        if missing_act:
            issues.append(f"[缺字段] {row_label} actions[{j}] 缺少：{sorted(missing_act)}")

    # turns 与 actions 长度一致
    expected_turns = len(actions)
    actual_turns = record["turns"]
    if actual_turns != expected_turns:
        issues.append(
            f"[不一致] {row_label} turns={actual_turns}，但 actions 有 {expected_turns} 条"
        )

    # has_outbound 一致性
    has_outbound_actual = any(a.get("outbound", False) for a in actions)
    if record["has_outbound"] != has_outbound_actual:
        issues.append(
            f"[不一致] {row_label} has_outbound={record['has_outbound']}，"
            f"但 actions 中 outbound={'存在' if has_outbound_actual else '不存在'}"
        )

    return issues


def main():
    parser = argparse.ArgumentParser(description="Phase 0 节点校验：paths.jsonl 格式检查")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--sop-id", help="SOP 标识（自动定位 data/paths/{sop_id}_paths.jsonl）")
    grp.add_argument("--paths", help="直接指定 paths.jsonl 路径")
    args = parser.parse_args()

    if args.paths:
        paths_file = Path(args.paths)
        if not paths_file.is_absolute():
            paths_file = SKILL_ROOT / paths_file
    else:
        paths_file = PATHS_DIR / f"{args.sop_id}_paths.jsonl"

    print(f"🔍 校验：{paths_file}")
    if not paths_file.exists():
        print(f"❌ 文件不存在：{paths_file}", file=sys.stderr)
        print("   请先执行 Phase 0：python scripts/enumerate_paths.py --sop-id <sop_id>", file=sys.stderr)
        sys.exit(1)

    records = []
    with open(paths_file, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"❌ 第{i+1}行 JSON 格式错误：{e}", file=sys.stderr)
                sys.exit(1)

    print(f"   共 {len(records)} 条路径")

    all_issues = []

    # path_id 重复检查
    seen_ids = {}
    for i, rec in enumerate(records):
        pid = rec.get("path_id", "")
        if pid in seen_ids:
            all_issues.append(f"[重复ID] path_id={pid!r} 在第{seen_ids[pid]+1}条和第{i+1}条重复")
        else:
            seen_ids[pid] = i

    # 逐条校验
    for i, rec in enumerate(records):
        issues = verify_record(rec, i)
        all_issues.extend(issues)

    errors = [i for i in all_issues if not i.startswith("[警告]")]
    warnings = [i for i in all_issues if i.startswith("[警告]")]

    if warnings:
        print(f"\n⚠️  警告（{len(warnings)} 条）：")
        for w in warnings:
            print(f"   {w}")

    if errors:
        print(f"\n❌ 校验失败（{len(errors)} 个错误）：")
        for e in errors:
            print(f"   {e}")
        sys.exit(1)
    else:
        print(f"✅ 校验通过（{len(records)} 条路径，字段完整，path_id 无重复）")
        sys.exit(0)


if __name__ == "__main__":
    main()
