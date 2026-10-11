#!/usr/bin/env python3
"""
generate_prompts.py
-------------------
Phase 1 第一步：纯 Prompt 拼装，不调用 API。

读取 `{sop_id}_graph.json`（路径骨架）× persona.md（Persona 定义）× expand-prompt.md（模板），
为每个选定的路径 × Persona 组合生成完整 Prompt，输出到 prompts_todo.jsonl。

用法:
  python scripts/generate_prompts.py \\
      --sop-id invoice \\
      --run-dir data/runs/run_20260325_001 \\
      [--paths P001,P003,P005] \\
      [--personas P1,P3]

选项:
  --sop-id     SOP 标识，用于定位路径文件（必填）
  --run-dir    运行目录（会自动创建）
  --paths      路径 ID 子集（逗号分隔，默认全部）
  --personas   Persona ID 子集（逗号分隔，默认全部）
  --dry-run    打印前3条 prompt，不写文件

输出:
  run-dir/prompts_todo.jsonl  每行一条：{"task_id", "path_id", "persona_id", "batch", "sop_id", "output_count", "order_signals"}
"""

import sys
import argparse
from pathlib import Path

# ─── 路径 ──────────────────────────────────────────────────────────────────────
SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from lib import (
    build_order_signals,
    build_prompt as build_prompt_shared,
    build_signal_mock_rules as build_signal_mock_rules_shared,
    compute_seq_map,
    extract_prompt_template,
    load_paths_for_sop,
    load_personas,
    load_signal_rules_for_sop,
    load_sop_graph_for_sop,
    next_seq,
    resolve_project_path,
    resolve_run_dir,
    task_id_to_path_persona,
    write_prompt_bundle,
)

REFERENCES_DIR = SKILL_ROOT / "references"
ASSETS_DIR = SKILL_ROOT / "assets"
DATA_DIR = SKILL_ROOT / "data"
GENERATED_DIR = DATA_DIR / "generated"
SOP_GRAPH_DIR = DATA_DIR / "sop"      # Phase 0 产物：data/sop/{sop_id}_graph.json
PATHS_DIR = DATA_DIR / "paths"        # Phase 0 产物：data/paths/{sop_id}_paths.jsonl
RUNS_DIR = DATA_DIR / "runs"          # Phase 1/2 产物：data/runs/{run_id}/

PERSONA_FILE = REFERENCES_DIR / "persona.md"
EXPAND_PROMPT_FILE = REFERENCES_DIR / "expand-prompt.md"
SPEECH_GUIDELINES_FILE = ASSETS_DIR / "speech-guidelines.md"


# ─── 加载工具 ──────────────────────────────────────────────────────────────────

def load_paths(sop_id: str) -> list:
    return load_paths_for_sop(SKILL_ROOT, sop_id)


def load_sop_graph(sop_id: str) -> dict:
    return load_sop_graph_for_sop(SKILL_ROOT, sop_id)


def load_signal_rules(sop_id: str, graph: dict | None = None) -> dict | None:
    return load_signal_rules_for_sop(SKILL_ROOT, sop_id, graph=graph)


def build_signal_mock_rules(signal_rules: dict | None, path: dict, graph: dict) -> str:
    return build_signal_mock_rules_shared(signal_rules, path, graph)


def build_prompt(
    path: dict,
    persona_id: str,
    persona: dict,
    batch: str,
    graph: dict,
    prompt_template: str,
    speech_guidelines: str,
    emotion_guidelines: str = "",
    signal_mock_rules: str = "",
    output_count: int = 5,
) -> str:
    return build_prompt_shared(
        path=path,
        persona_id=persona_id,
        persona=persona,
        batch=batch,
        graph=graph,
        prompt_template=prompt_template,
        speech_guidelines=speech_guidelines,
        emotion_guidelines=emotion_guidelines,
        signal_mock_rules=signal_mock_rules,
        output_count=output_count,
    )


# ─── 主流程 ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="cs-data-factory Phase 1 Prompt 生成")
    parser.add_argument("--sop-id", default="",
                        help="SOP 标识（如 dcdcdc）。留空时自动从 data/paths/ 下推断："
                             "若只有一个 *_paths.jsonl 则直接使用；若有多个则列出候选并报错提示。")
    parser.add_argument("--run-dir", required=True, help="运行目录（自动创建）")
    parser.add_argument("--paths", default="", help="路径 ID 子集（逗号分隔，空=全部）")
    parser.add_argument("--personas", default="", help="Persona ID 子集（逗号分隔，空=全部）")
    parser.add_argument("--task-ids", default="",
                        help="仅重新生成指定 task_id 列表（逗号分隔，如 P001_P1_001,P002_P2_002）。"
                             "用于迭代验证重跑场景，batch 号取原 task_id 中的值（不递增），"
                             "与 --paths/--personas 互斜（指定 --task-ids 时忽略 --paths/--personas/--sample-mode）")
    parser.add_argument("--samples", type=int, default=5, help="每次调用生成的样本数（默认 5）")
    parser.add_argument("--sample-mode", default="all",
                        choices=["all", "one-per-path"],
                        help="采样模式：all=全排列（路径×Persona），one-per-path=每路径只选 1 个 Persona（轮换分配）")
    parser.add_argument("--dry-run", action="store_true", help="打印前 3 条 prompt，不写文件")
    parser.add_argument(
        "--confirmed", action="store_true",
        help=(
            "用户已明确确认参数后才可传入此标志，脚本才会真正执行生成。\n"
            "不传此标志时，脚本仅打印参数摘要后退出（exit 0），不生成任何文件。\n"
            "这是防止 Agent 静默跳过参数确认步骤的强制门槛。"
        ),
    )
    parser.add_argument("--expand-prompt", default="",
                        help="自定义 expand-prompt 模板文件路径（覆盖默认 references/expand-prompt.md），"
                             "用于验证优化版 prompt 而不影响正式文件。"
                             "路径可以是相对于项目根目录的相对路径，也可以是绝对路径。")
    parser.add_argument("--persona-file", default="",
help="场景定制化 Persona 文件路径（覆盖默认 references/persona.md），"
        "通常由 analyze_persona.py 生成（如 data/sop/{sop_id}_persona.md）")
    args = parser.parse_args()

    # ── 自动推断 sop-id ──
    if not args.sop_id:
        paths_files = sorted(PATHS_DIR.glob("*_paths.jsonl"))
        if not paths_files:
            print("❌ --sop-id 未指定，且 data/paths/ 下没有找到任何 *_paths.jsonl 文件。", file=sys.stderr)
            print("   请先执行 Phase 0 初始化，或手动指定 --sop-id", file=sys.stderr)
            sys.exit(1)
        if len(paths_files) == 1:
            inferred = paths_files[0].stem.replace("_paths", "")
            print(f"  ℹ️  未指定 --sop-id，自动推断为：{inferred}（来自 {paths_files[0].name}）")
            args.sop_id = inferred
        else:
            candidates = [p.stem.replace('_paths', '') for p in paths_files]
            print(f"❌ --sop-id 未指定，且 data/paths/ 下存在多个路径文件，请手动指定 --sop-id：", file=sys.stderr)
            for c in candidates:
                print(f"   --sop-id {c}", file=sys.stderr)
            sys.exit(1)

    raw_run_dir, run_dir = resolve_run_dir(SKILL_ROOT, args.run_dir)

    # ── 参数确认门槛（防止 Agent 静默执行，必须带 --confirmed 才会真正运行）──
    # 若未传 --confirmed，仅打印参数摘要后退出（exit 0），不生成任何文件。
    # Agent 应先以不带 --confirmed 运行本脚本，将输出内容展示给用户，
    # 收到用户明确确认后，再加 --confirmed 重新运行。
    if not args.confirmed and not args.dry_run:
        paths_desc = args.paths if args.paths else "全部"
        personas_desc = args.personas if args.personas else "全部"
        sop_id_desc = args.sop_id if args.sop_id else "（自动推断）"
        print("=" * 60)
        print("⚠️  Phase 1 参数摘要（尚未执行，请将以下内容展示给用户确认）")
        print("=" * 60)
        print(f"  sop_id          : {sop_id_desc}")
        print(f"  run_dir         : {raw_run_dir}")
        print(f"  路径集           : {paths_desc}")
        print(f"  Persona 集       : {personas_desc}")
        print(f"  采样模式         : {args.sample_mode}")
        print(f"  每 task 生成条数  : {args.samples}")
        if args.task_ids:
            print(f"  --task-ids       : {args.task_ids}")
        print("=" * 60)
        print("")
        print("用户确认参数无误后，在命令末尾加上 --confirmed 重新执行：")
        print("")
        # 重建确认命令，供 Agent 复制展示
        cmd_parts = ["python scripts/generate_prompts.py"]
        if args.sop_id:
            cmd_parts.append(f"--sop-id {args.sop_id}")
        cmd_parts.append(f"--run-dir {raw_run_dir}")
        if args.paths:
            cmd_parts.append(f"--paths {args.paths}")
        if args.personas:
            cmd_parts.append(f"--personas {args.personas}")
        if args.task_ids:
            cmd_parts.append(f"--task-ids {args.task_ids}")
        cmd_parts.append(f"--sample-mode {args.sample_mode}")
        cmd_parts.append(f"--samples {args.samples}")
        cmd_parts.append("--confirmed")
        print("  " + " \\\n    ".join(cmd_parts))
        print("")
        sys.exit(0)

    path_ids_filter = [p.strip() for p in args.paths.split(",") if p.strip()]
    persona_ids_filter = [p.strip() for p in args.personas.split(",") if p.strip()]
    task_ids_filter = [t.strip() for t in args.task_ids.split(",") if t.strip()]

    # ── 确定 Persona 文件路径 ──
    if args.persona_file:
        persona_file_path = resolve_project_path(SKILL_ROOT, args.persona_file)
        if not persona_file_path.exists():
            print(f"❌ 指定的 Persona 文件不存在：{persona_file_path}", file=sys.stderr)
            sys.exit(1)
        print(f"  使用定制化 Persona 文件：{persona_file_path}")
    else:
        persona_file_path = PERSONA_FILE

    # ── 加载材料 ──
    print("📂 加载材料...")
    all_paths = load_paths(args.sop_id)
    graph = load_sop_graph(args.sop_id)
    personas = load_personas(persona_file_path)
    if args.expand_prompt:
        ep_path = resolve_project_path(SKILL_ROOT, args.expand_prompt)
        if not ep_path.exists():
            print(f"❌ 指定的 expand-prompt 文件不存在：{ep_path}", file=sys.stderr)
            sys.exit(1)
        print(f"  使用自定义 expand-prompt：{ep_path}")
        expand_prompt_md = ep_path.read_text(encoding="utf-8")
    else:
        expand_prompt_md = EXPAND_PROMPT_FILE.read_text(encoding="utf-8")
    prompt_template = extract_prompt_template(expand_prompt_md)
    speech_guidelines = SPEECH_GUIDELINES_FILE.read_text(encoding="utf-8") \
        if SPEECH_GUIDELINES_FILE.exists() else ""
    emotion_file = SOP_GRAPH_DIR / f"{args.sop_id}_emotion.md"
    if emotion_file.exists():
        emotion_guidelines = emotion_file.read_text(encoding="utf-8")
        print(f"  已加载情绪处理规范：{emotion_file.name}")
    else:
        emotion_guidelines = ""
        print(f"  未配置情绪处理规范（可选：data/sop/{args.sop_id}_emotion.md）")

    # ── 加载信号规则（Phase 0 产物，可选）──
    signal_rules = load_signal_rules(args.sop_id, graph=graph)
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
        print(f"  未配置信号规则（可选：data/sop/{args.sop_id}_signal_rules.json）"
              f"\n  提示：执行 python scripts/analyze_signals.py --sop-id {args.sop_id} 生成草稿后审核入库")

    output_count = args.samples

    # ── --task-ids 模式：直接指定重跑的 task，batch 号复用原值（不递增） ──
    if task_ids_filter:
        print(f"  模式: --task-ids（重跑指定 task），共 {len(task_ids_filter)} 个")
        path_map = {p["path_id"]: p for p in all_paths}
        path_persona_pairs_with_batch = []
        for task_id in task_ids_filter:
            path_id, persona_id, batch = task_id_to_path_persona(task_id)
            if path_id not in path_map:
                print(f"  ⚠️ 路径不存在，跳过：{task_id} → path_id={path_id}")
                continue
            if persona_id not in personas:
                print(f"  ⚠️ Persona 不存在，跳过：{task_id} → persona_id={persona_id}")
                continue
            path_persona_pairs_with_batch.append((path_map[path_id], persona_id, batch))
        print(f"  匹配到 {len(path_persona_pairs_with_batch)} 个 task")
    else:
        # ── 常规模式：路径×Persona 过滤 ──
        if path_ids_filter:
            all_paths = [p for p in all_paths if p["path_id"] in path_ids_filter]
        selected_personas = {pid: personas[pid] for pid in (persona_ids_filter or list(personas.keys()))
                             if pid in personas}
        if not selected_personas:
            print(f"❌ 没有匹配的 Persona：{persona_ids_filter}，可用：{list(personas.keys())}", file=sys.stderr)
            sys.exit(1)

        sample_mode = args.sample_mode

        # ── one-per-path 模式：为每个路径轮换分配一个 Persona ──
        if sample_mode == "one-per-path":
            persona_list = list(selected_personas.keys())
            _pairs = [
                (path, persona_list[i % len(persona_list)])
                for i, path in enumerate(all_paths)
            ]
            print(f"  路径数: {len(all_paths)}  Persona数: {len(selected_personas)}  模式: one-per-path")
            print(f"  总任务: {len(_pairs)}  每任务样本数: {output_count}")
        else:
            _pairs = [
                (path, pid)
                for path in all_paths
                for pid in selected_personas.keys()
            ]
            print(f"  路径数: {len(all_paths)}  Persona数: {len(selected_personas)}  模式: all（全排列）")
            print(f"  总任务: {len(_pairs)}  每任务样本数: {output_count}")

        # ── 计算 BATCH 号 ──
        seq_map = compute_seq_map(GENERATED_DIR)
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        path_persona_pairs_with_batch = [
            (path, pid, next_seq(seq_map, path["path_id"], pid))
            for path, pid in _pairs
        ]

    # ── 生成 Prompt ──
    tasks = []
    for path, pid, batch in path_persona_pairs_with_batch:
        persona = personas[pid]
        task_id = f"{path['path_id']}_{pid}_{batch}"

        # ── 提取路径判断信号（骨架锁定值）──
        order_signals = build_order_signals(path)

        # ── 构造信号 Mock 规则块（注入骨架之后）──
        signal_mock_rules_text = build_signal_mock_rules(signal_rules, path, graph)

        prompt = build_prompt(path, pid, persona, batch, graph, prompt_template,
                              speech_guidelines, emotion_guidelines=emotion_guidelines,
                              signal_mock_rules=signal_mock_rules_text,
                              output_count=output_count)
        tasks.append({
            "task_id": task_id,
            "path_id": path["path_id"],
            "persona_id": pid,
            "batch": batch,
            "sop_id": args.sop_id,
            "output_count": output_count,
            "order_signals": order_signals,
            "prompt": prompt,
        })

    if args.dry_run:
        print(f"\n🔍 DRY-RUN：前3条 prompt")
        for t in tasks[:3]:
            print(f"\n{'='*60}")
            print(f"task_id: {t['task_id']}")
            print(f"{'='*60}")
            print(t["prompt"][:2000])
            print("...")
        return

    # ── 写文件 ──
    out_file, subagent_dir, ids_file = write_prompt_bundle(run_dir, tasks)
    task_ids_list = [t["task_id"] for t in tasks]

    print(f"\n✅ 已生成 {len(tasks)} 条 prompt → {out_file}")
    print(f"   独立 prompt 文件 → {subagent_dir}/{{task_id}}.prompt.txt")
    print(f"   task_ids.json → {ids_file}（{len(task_ids_list)} 个 task）")
    print(f"   检查无误后，告知主 Agent 对 {args.run_dir} 启动 SubAgent 并发生产")
    print(f"   SubAgent 生产完成后运行：python scripts/postprocess.py --run-dir {raw_run_dir}")


if __name__ == "__main__":
    main()
