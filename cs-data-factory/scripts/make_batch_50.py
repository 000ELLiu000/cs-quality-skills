#!/usr/bin/env python3
"""
make_batch_50.py
----------------
Legacy helper: 生成 50 条任务分配（25 路径 × 2 Persona），8 个 Persona 尽量均匀覆盖。

说明：
- 该脚本保留给历史人工抽样工作流使用
- Phase 1 主链路优先使用 `generate_prompts.py` 或 `gen_custom_batch.py`

规则：
1. 每条路径分配 2 个不同 Persona
2. 8 个 Persona 尽量均匀（每个出现 6~7 次）
3. 同一路径的 2 个 Persona 不重复

输出：
- 写入 `batch50_assignments.jsonl` 供人工检查
- 打印 `gen_custom_batch.py` 的调用命令，作为下一步入口

用法：
  python scripts/make_batch_50.py --sop-id invoice --run-dir data/runs/run_20260326_050
"""

import json
import sys
import argparse
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from lib import load_paths_for_sop, resolve_run_dir

ALL_PERSONAS = ["P1", "P2", "P3", "P4", "P5", "P7", "P8", "P9"]


def assign_personas(path_ids: list) -> list:
    """
    为每条路径分配 2 个不重复的 Persona，均匀覆盖所有 8 个 Persona。
    策略：
    - 先按 8 个 Persona 循环配对，每轮生成 4 对（8个每次取2个不重叠对）
    - 然后按路径顺序分配
    返回：[(path_id, persona1, persona2), ...]
    """
    n = len(path_ids)  # 25
    # 生成足够的不重复 Persona 对，均匀覆盖
    # 方案：构造一个循环，每次从8个Persona中取一对，轮转
    
    # 预先生成配对序列（25对），尽量均匀
    pairs = []
    # 构造8个Persona的循环配对
    # 用轮转法：第i对取 Persona[i%8] 和 Persona[(i+4)%8]（保证不重复且均匀）
    for i in range(n):
        p1 = ALL_PERSONAS[i % 8]
        p2 = ALL_PERSONAS[(i + 3) % 8]  # 错开3位，保证不同且分布均匀
        if p1 == p2:
            p2 = ALL_PERSONAS[(i + 4) % 8]
        pairs.append((p1, p2))
    
    # 统计覆盖
    coverage = {}
    for p1, p2 in pairs:
        coverage[p1] = coverage.get(p1, 0) + 1
        coverage[p2] = coverage.get(p2, 0) + 1
    
    return list(zip(path_ids, [p[0] for p in pairs], [p[1] for p in pairs])), coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sop-id", default="invoice")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--samples", type=int, default=1, help="每个任务生成几条样本（默认1）")
    args = parser.parse_args()

    all_paths = load_paths_for_sop(SKILL_ROOT, args.sop_id)
    path_ids = [path["path_id"] for path in all_paths]
    
    print(f"📋 共 {len(path_ids)} 条路径：{', '.join(path_ids)}")
    
    assignments, coverage = assign_personas(path_ids)
    
    print(f"\n📊 Persona 覆盖统计：")
    for p in ALL_PERSONAS:
        print(f"  {p}: {coverage.get(p, 0)} 次")
    
    print(f"\n📝 分配明细（共 {len(assignments)*2} 个任务，{len(assignments)} 条路径）：")
    for path_id, p1, p2 in assignments:
        print(f"  {path_id}: {p1}, {p2}")
    
    raw_run_dir, run_dir = resolve_run_dir(SKILL_ROOT, args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    config_file = run_dir / "batch50_assignments.jsonl"
    with open(config_file, "w", encoding="utf-8") as f:
        for path_id, p1, p2 in assignments:
            f.write(json.dumps({"path_id": path_id, "personas": [p1, p2], "samples": args.samples}, ensure_ascii=False) + "\n")
    
    # 将 assignments.jsonl 转换为 gen_custom_batch.py 接受的逗号分隔格式
    assignment_pairs = []
    for path_id, p1, p2 in assignments:
        assignment_pairs.append(f"{path_id}:{p1}")
        assignment_pairs.append(f"{path_id}:{p2}")
    assignments_str = ",".join(assignment_pairs)

    print(f"\n✅ 分配结果已写入：{config_file}")
    print("\nℹ️  该脚本为 legacy 辅助脚本；正式生产仍请走 gen_custom_batch.py。")
    print(f"\n下一步：运行 gen_custom_batch.py 生成 prompts_todo.jsonl")
    print(f"  python scripts/gen_custom_batch.py \\")
    print(f"    --sop-id {args.sop_id} \\")
    print(f"    --run-dir {raw_run_dir} \\")
    print(f'    --assignments "{assignments_str}" \\')
    print(f"    --samples {args.samples}")


if __name__ == "__main__":
    main()
