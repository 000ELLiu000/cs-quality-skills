#!/usr/bin/env python3
"""
gen_custom_batch.py
-------------------
按自定义分配表（path_id × persona_id）生成 prompts_todo.jsonl，
不做全排列，每条 path×persona 组合只生成一个 task。

用法:
  python scripts/gen_custom_batch.py \
      --sop-id xiangxiang-no-rider \
      --run-dir data/runs/run_xiangxiang_20tasks \
      --assignments "P008:P2,P008:P4,P008:P3,P009:P2,P009:P4,P009:P1,P010:P2,P010:P2b,P010:P8,P013:P2,P013:P4,P014:P2,P014:P3,P015:P4,P015:P5,P007:P2,P007:P9,P012:P2,P012:P1,P006:P3" \
      [--samples 5]
"""

import re
import sys
import argparse
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from lib import (
    build_order_signals,
    build_prompt,
    build_signal_mock_rules,
    compute_seq_map,
    extract_prompt_template,
    load_paths_for_sop,
    load_personas,
    load_signal_rules_for_sop,
    load_sop_graph_for_sop,
    next_seq,
    resolve_project_path,
    resolve_run_dir,
    write_prompt_bundle,
)

REFERENCES_DIR = SKILL_ROOT / "references"
ASSETS_DIR = SKILL_ROOT / "assets"
DATA_DIR = SKILL_ROOT / "data"
GENERATED_DIR = DATA_DIR / "generated"
SOP_GRAPH_DIR = DATA_DIR / "sop"
PERSONA_FILE = REFERENCES_DIR / "persona.md"
EXPAND_PROMPT_FILE = REFERENCES_DIR / "expand-prompt.md"
SPEECH_GUIDELINES_FILE = ASSETS_DIR / "speech-guidelines.md"


def main():
    parser = argparse.ArgumentParser(description="按自定义分配表生成 prompts_todo.jsonl")
    parser.add_argument("--sop-id", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--assignments", required=True,
                        help="path:persona 对，逗号分隔，如 P008:P2,P009:P4")
    parser.add_argument("--samples", type=int, default=5, help="每 task 生成条数（默认5）")
    parser.add_argument("--persona-file", default="",
                        help="场景定制化 Persona 文件路径（覆盖默认 references/persona.md）")
    args = parser.parse_args()

    raw_run_dir, run_dir = resolve_run_dir(SKILL_ROOT, args.run_dir)

    # ── 解析分配表；允许同一 path×persona 重复出现（P2b → P2，靠 batch 号区分）──
    raw_assignments = [a.strip() for a in args.assignments.split(",") if a.strip()]
    assignments = []
    for entry in raw_assignments:
        parts = entry.split(":")
        if len(parts) != 2:
            print(f"⚠️ 格式错误，跳过：{entry}", file=sys.stderr)
            continue
        path_id, persona_raw = parts[0].strip(), parts[1].strip()
        persona_id = re.sub(r'[a-z]+$', '', persona_raw)
        assignments.append((path_id, persona_id))

    print(f"📋 共 {len(assignments)} 条分配")

    # ── 加载材料 ──
    print("📂 加载材料...")
    all_paths = load_paths_for_sop(SKILL_ROOT, args.sop_id)
    graph = load_sop_graph_for_sop(SKILL_ROOT, args.sop_id)

    # 确定 Persona 文件
    if args.persona_file:
        persona_file_path = resolve_project_path(SKILL_ROOT, args.persona_file)
        if not persona_file_path.exists():
            print(f"❌ 指定的 Persona 文件不存在：{persona_file_path}", file=sys.stderr)
            sys.exit(1)
        print(f"  使用定制化 Persona 文件：{persona_file_path}")
    else:
        persona_file_path = PERSONA_FILE

    personas = load_personas(persona_file_path)
    expand_prompt_md = EXPAND_PROMPT_FILE.read_text(encoding="utf-8")
    prompt_template = extract_prompt_template(expand_prompt_md)
    speech_guidelines = SPEECH_GUIDELINES_FILE.read_text(encoding="utf-8") \
        if SPEECH_GUIDELINES_FILE.exists() else ""

    # 情绪处理规范（可选）
    emotion_file = SOP_GRAPH_DIR / f"{args.sop_id}_emotion.md"
    if emotion_file.exists():
        emotion_guidelines = emotion_file.read_text(encoding="utf-8")
        print(f"  已加载情绪处理规范：{emotion_file.name}")
    else:
        emotion_guidelines = ""
        print(f"  未配置情绪处理规范（可选：data/sop/{args.sop_id}_emotion.md）")

    # ── 加载信号规则（新架构：LLM 内部 mock，不再从 Excel 直接取值）──
    signal_rules = load_signal_rules_for_sop(SKILL_ROOT, args.sop_id, graph=graph)
    if signal_rules is not None:
        slot_fields = graph.get("slot_fields", [])
        signal_fields = graph.get("signal_fields", [])
        mock_count = sum(
            1 for k, v in signal_rules.get("signals", {}).items()
            if v.get("type") == "mock" and (k in slot_fields or k in signal_fields)
        )
        print(f"  已加载信号规则：data/sop/{args.sop_id}_signal_rules.json"
              f"（{len(signal_rules.get('signals', {}))} 个信号，{mock_count} 个需要 mock）")
    else:
        print(f"  未配置信号规则（可选：data/sop/{args.sop_id}_signal_rules.json）")

    path_map = {p["path_id"]: p for p in all_paths}

    # ── 生成 ──
    # ── 计算 BATCH 号（避免与历史数据冲突）──
    seq_map = compute_seq_map(GENERATED_DIR)

    tasks = []
    for path_id, persona_id in assignments:
        if path_id not in path_map:
            print(f"⚠️ 路径不存在，跳过：{path_id}", file=sys.stderr)
            continue
        if persona_id not in personas:
            print(f"⚠️ Persona 不存在，跳过：{persona_id}", file=sys.stderr)
            continue

        path = path_map[path_id]
        persona = personas[persona_id]
        batch = next_seq(seq_map, path_id, persona_id)
        task_id = f"{path_id}_{persona_id}_{batch}"

        order_signals = build_order_signals(path)
        signal_mock_rules_text = build_signal_mock_rules(signal_rules, path, graph)

        prompt = build_prompt(
            path=path,
            persona_id=persona_id,
            persona=persona,
            batch=batch,
            graph=graph,
            prompt_template=prompt_template,
            speech_guidelines=speech_guidelines,
            emotion_guidelines=emotion_guidelines,
            signal_mock_rules=signal_mock_rules_text,
            output_count=args.samples,
        )

        tasks.append({
            "task_id": task_id,
            "path_id": path_id,
            "persona_id": persona_id,
            "batch": batch,
            "sop_id": args.sop_id,
            "output_count": args.samples,
            "order_signals": order_signals,
            "prompt": prompt,
        })
        print(f"  ✅ {task_id}")

    out_file, subagent_dir, ids_file = write_prompt_bundle(run_dir, tasks)
    count = len(tasks)

    print(f"\n✅ 完成：{count} 条 → {out_file}")
    print(f"   独立 prompt 文件 → {subagent_dir}/{{task_id}}.prompt.txt")
    print(f"   检查无误后，告知主 Agent 对 {raw_run_dir} 启动 SubAgent 并发生产")
    print(f"   SubAgent 生产完成后运行：python scripts/postprocess.py --run-dir {raw_run_dir}")


if __name__ == "__main__":
    main()
