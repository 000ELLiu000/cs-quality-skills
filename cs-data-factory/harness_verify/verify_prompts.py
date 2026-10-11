#!/usr/bin/env python3
"""
verify_prompts.py
-----------------
Phase 1 节点校验：检查 generate_prompts.py 的产物完整性。

格式层校验（不做语义判断）：
  1. prompts_todo.jsonl 存在且每行字段完整（task_id / path_id / persona_id / batch / sop_id / output_count / order_signals）
  2. subagent_prompts/ 目录存在
  3. 每个 task_id 对应的 .prompt.txt 文件存在且非空
  4. .prompt.txt 中不含未替换的模板占位符（如 {PATH_CONTENT} / {PERSONA_NAME} 等残留）
  5. prompts_todo.jsonl 行数与 .prompt.txt 文件数一致

用法：
python harness_verify/verify_prompts.py --run-dir data/runs/run_YYYYMMDD_NNN
python harness_verify/verify_prompts.py --run-dir run_YYYYMMDD_NNN   # 自动补全 data/runs/
"""

import json
import sys
import argparse
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))
from lib import task_id_to_path_persona

DATA_DIR = SKILL_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"

# 模板中可能残留的占位符（generate_prompts.py 应全部替换掉）
TEMPLATE_PLACEHOLDERS = re.compile(
    r"\{(?:PATH_CONTENT|PERSONA_NAME|PERSONA_DESC|PERSONA_FEATURES|"
    r"PERSONA_EXAMPLES|SPEECH_GUIDELINES|PATH_ID|PERSONA_ID|BATCH|SEQ|OUTPUT_COUNT|"
    r"SIGNAL_MOCK_RULES|EMOTION_GUIDELINES)\}"
)
# 注：REF_SIGNALS 是 v10.11.0 之前的旧占位符名，已重命名为 SIGNAL_MOCK_RULES，不再检查

REQUIRED_TASK_FIELDS = {
    "task_id", "path_id", "persona_id", "batch", "sop_id", "output_count", "order_signals"
}
BATCH_RE = re.compile(r"^\d{3}$")


def validate_task_contract(task: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one prompts_todo task record."""
    errors = []
    warnings = []
    task_id = task.get("task_id", "?")

    batch = task.get("batch")
    if not isinstance(batch, str) or not BATCH_RE.match(batch):
        errors.append(f"task={task_id} 的 batch 非法：{batch!r}（期望三位字符串，如 '001'）")

    output_count = task.get("output_count")
    if not isinstance(output_count, int) or output_count <= 0:
        errors.append(f"task={task_id} 的 output_count 非法：{output_count!r}（期望正整数）")

    order_signals = task.get("order_signals")
    if not isinstance(order_signals, dict):
        errors.append(f"task={task_id} 的 order_signals 非法：{type(order_signals).__name__}（期望 dict）")
    elif not order_signals:
        warnings.append(f"task={task_id} 的 order_signals 为空，请确认该路径是否确实不存在稳定判断信号")

    path_id = task.get("path_id")
    persona_id = task.get("persona_id")
    if all(isinstance(v, str) and v for v in (task_id, path_id, persona_id, batch)):
        parsed_path_id, parsed_persona_id, parsed_batch = task_id_to_path_persona(task_id)
        if (parsed_path_id, parsed_persona_id, parsed_batch) != (path_id, persona_id, batch):
            errors.append(
                "task_id 与 path_id/persona_id/batch 不一致："
                f"{task_id!r} -> ({parsed_path_id!r}, {parsed_persona_id!r}, {parsed_batch!r})"
            )

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description="Phase 1 节点校验：Prompt 产物完整性检查")
    parser.add_argument("--run-dir", required=True,
                        help="运行目录（支持 data/runs/ 自动补全）")
    parser.add_argument("--strict", action="store_true",
                        help="发现 warning 也返回非零退出码")
    args = parser.parse_args()

    # 路径解析（支持自动补全）
    raw = args.run_dir
    if not Path(raw).is_absolute() and not raw.startswith("data/runs"):
        raw = f"data/runs/{raw}"
    run_dir = SKILL_ROOT / raw

    print(f"🔍 校验 Phase 1 产物：{run_dir}")

    error_count = 0
    warning_count = 0

    # ── 1. 检查 prompts_todo.jsonl ──
    todo_file = run_dir / "prompts_todo.jsonl"
    if not todo_file.exists():
        print(f"❌ prompts_todo.jsonl 不存在：{todo_file}")
        print("   请先运行：python scripts/generate_prompts.py --sop-id <sop_id> --run-dir <run_dir>")
        sys.exit(1)

    tasks = []
    parse_errors = 0
    with open(todo_file, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                tasks.append(t)
            except json.JSONDecodeError as e:
                print(f"   ❌ prompts_todo.jsonl 第{i+1}行 JSON 格式错误：{e}")
                parse_errors += 1

    error_count += parse_errors
    print(f"   prompts_todo.jsonl：{len(tasks)} 条任务（解析失败 {parse_errors} 行）")

    # ── 2. 检查每条任务的字段完整性与关键契约 ──
    missing_fields_count = 0
    contract_error_count = 0
    contract_warning_count = 0
    for t in tasks:
        missing = REQUIRED_TASK_FIELDS - set(t.keys())
        if missing:
            print(f"   ❌ task={t.get('task_id', '?')} 缺少字段：{sorted(missing)}")
            missing_fields_count += 1
            continue

        task_errors, task_warnings = validate_task_contract(t)
        for msg in task_errors:
            print(f"   ❌ {msg}")
        for msg in task_warnings:
            print(f"   ⚠️  {msg}")
        contract_error_count += len(task_errors)
        contract_warning_count += len(task_warnings)

    error_count += missing_fields_count + contract_error_count
    warning_count += contract_warning_count
    if missing_fields_count == 0 and contract_error_count == 0:
        print(
            "   ✅ 所有任务字段与关键契约完整"
            "（task_id / path_id / persona_id / batch / sop_id / output_count / order_signals）"
        )

    # ── 3. 检查 subagent_prompts 目录 ──
    subagent_dir = run_dir / "subagent_prompts"
    if not subagent_dir.exists():
        print(f"   ❌ subagent_prompts/ 目录不存在")
        error_count += 1
        print(f"\n{'❌ 校验失败' if error_count > 0 else '✅ 校验通过'}（error={error_count}，warning={warning_count}）")
        sys.exit(1)

    # ── 4. 检查每个 task_id 对应的 .prompt.txt ──
    missing_files = []
    empty_files = []
    placeholder_files = []

    for t in tasks:
        tid = t.get("task_id", "")
        if not tid:
            continue
        prompt_file = subagent_dir / f"{tid}.prompt.txt"
        if not prompt_file.exists():
            missing_files.append(tid)
        elif prompt_file.stat().st_size == 0:
            empty_files.append(tid)
        else:
            content = prompt_file.read_text(encoding="utf-8")
            if TEMPLATE_PLACEHOLDERS.search(content):
                found = TEMPLATE_PLACEHOLDERS.findall(content)
                placeholder_files.append((tid, found))

    if missing_files:
        print(f"   ❌ 以下 {len(missing_files)} 个 task_id 对应的 .prompt.txt 文件缺失：")
        for tid in missing_files[:10]:
            print(f"      - {tid}.prompt.txt")
        if len(missing_files) > 10:
            print(f"      ... 另有 {len(missing_files) - 10} 个（已省略）")
        error_count += len(missing_files)
    else:
        print(f"   ✅ 所有 {len(tasks)} 个 .prompt.txt 文件均存在")

    if empty_files:
        print(f"   ❌ 以下 {len(empty_files)} 个 .prompt.txt 文件为空：")
        for tid in empty_files:
            print(f"      - {tid}.prompt.txt")
        error_count += len(empty_files)

    if placeholder_files:
        print(f"   ❌ 以下 {len(placeholder_files)} 个 .prompt.txt 含未替换的模板占位符：")
        for tid, placeholders in placeholder_files[:5]:
            print(f"      - {tid}.prompt.txt：{placeholders}")
        if len(placeholder_files) > 5:
            print(f"      ... 另有 {len(placeholder_files) - 5} 个（已省略）")
        error_count += len(placeholder_files)

    # ── 5. 检查文件数与 task 数一致性 ──
    actual_prompt_files = list(subagent_dir.glob("*.prompt.txt"))
    if len(actual_prompt_files) != len(tasks):
        print(f"   ⚠️  .prompt.txt 文件数（{len(actual_prompt_files)}）"
              f"与 prompts_todo.jsonl 行数（{len(tasks)}）不一致")
        warning_count += 1
    else:
        print(f"   ✅ 文件数与任务数一致（{len(tasks)} 个）")

    # ── 结果 ──
    has_failure = error_count > 0 or (args.strict and warning_count > 0)
    print(f"\n{'❌ 校验失败' if has_failure else '✅ 校验通过'}（error={error_count}，warning={warning_count}）")
    if not has_failure:
        print(f"   可以启动 SubAgent 并发生产（共 {len(tasks)} 个任务）")

    sys.exit(1 if has_failure else 0)


if __name__ == "__main__":
    main()
