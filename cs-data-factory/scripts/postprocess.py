#!/usr/bin/env python3
"""
postprocess.py
--------------
Phase 2 后处理：把主 Agent 编排 SubAgent 产出的 result.txt 文件展平成 final.jsonl。

SubAgent 生产流程（Agent 直接编排）：
  1. generate_prompts.py → prompts_todo.jsonl
  2. Agent sessions_spawn SubAgent × N（并发）→ subagent_prompts/{task_id}.result.txt
  3. postprocess.py → final.jsonl  ← 本脚本负责这一步

用法:
  python scripts/postprocess.py --run-dir data/runs/run_YYYYMMDD_NNN [--dry-run]

选项:
  --run-dir    运行目录（必须已包含 prompts_todo.jsonl 和 subagent_prompts/*.result.txt）
  --dry-run    只打印统计信息，不写 final.jsonl

「所有信号」字段组装规则（v2.0 模板替换架构）：
  来源：
    1. LLM 输出的 mocked_signals（判断信号 + 参考信号）
    2. signal_rules.json 中的 signal_template（真实样本格式模板）
    3. signal_rules.json 中的 other_signal_pool（补集信号值池，round-robin 取用）
  组装：用模板替换方式，将生成的信号值 + 补集信号值填入真实样本格式模板
  输出格式：与真实样本完全一致（含 ### 用户信息、### 支付信息 等 section headers）

主链路约定（当前正式格式）：
  - prompts_todo.jsonl 每条 task 含 task_id / sop_id / order_signals
  - data/sop/{sop_id}_signal_rules.json 含 signal_template 与 other_signal_pool

legacy 回退（仅兜底，不作为正式链路）：
  - 缺少 sop_id / signal_template 时，回退为简化「所有信号」格式
  - 缺少 order_signals 时，回退使用轨迹内自带的「所有信号」字段
"""

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

# ─── 路径 ─────────────────────────────────────────────────────────────────────
SKILL_ROOT = Path(__file__).parent.parent
DATA_DIR = SKILL_ROOT / "data"
GENERATED_DIR = DATA_DIR / "generated"  # 归档区：data/generated/
sys.path.insert(0, str(SKILL_ROOT))

from lib import fill_signal_template, load_signal_rules_for_sop, resolve_run_dir


# ─── run 元数据加载 ───────────────────────────────────────────────────────────


def load_run_tasks(prompts_file: Path) -> tuple[dict[str, dict], str | None, list[str]]:
    """
    加载 prompts_todo.jsonl，并抽取本 run 的 SOP / task 元数据。

    正式主链路要求每条 task 至少包含：
      - task_id
      - sop_id
      - order_signals

    legacy run 若缺少 sop_id / order_signals，会记录兼容提示，后续回退到 legacy 逻辑。
    """
    tasks: dict[str, dict] = {}
    sop_ids: set[str] = set()
    legacy_warnings: list[str] = []

    with open(prompts_file, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            task = json.loads(line)
            task_id = task.get("task_id")
            if not task_id:
                raise ValueError(f"{prompts_file} 第{line_no}行缺少 task_id")
            if task_id in tasks:
                raise ValueError(f"{prompts_file} 中 task_id 重复：{task_id}")

            tasks[task_id] = task

            sop_id = task.get("sop_id")
            if sop_id:
                sop_ids.add(sop_id)
            else:
                legacy_warnings.append(f"{task_id}: prompts_todo 缺少 sop_id，postprocess 将回退为 legacy 模式")

            if "order_signals" not in task:
                legacy_warnings.append(f"{task_id}: prompts_todo 缺少 order_signals，postprocess 将回退使用轨迹内的 `所有信号` 字段")

    if len(sop_ids) > 1:
        raise ValueError(f"prompts_todo.jsonl 中检测到多个 sop_id：{sorted(sop_ids)}")

    return tasks, next(iter(sop_ids), None), legacy_warnings



def load_signal_context(sop_id: str | None) -> tuple[str | None, list[dict], list[str]]:
    """
    加载当前 run 对应的 signal_rules 上下文。

    返回：
      - signal_template: 正式模板模式使用的模板；缺失时进入 legacy 简化格式
      - other_signal_pool: 模板模式下用于 round-robin 的补集信号池
      - warnings: run 级别的兼容提示
    """
    if not sop_id:
        return None, [], ["当前 run 未解析出 sop_id，postprocess 将回退为 legacy 简化格式"]

    signal_rules = load_signal_rules_for_sop(SKILL_ROOT, sop_id)
    if not signal_rules:
        return None, [], [f"sop_id={sop_id} 未找到 signal_rules.json，postprocess 将回退为 legacy 简化格式"]

    signal_template = signal_rules.get("signal_template")
    other_signal_pool = signal_rules.get("other_signal_pool", [])
    if not signal_template:
        return None, other_signal_pool, [
            f"sop_id={sop_id} 的 signal_rules.json 缺少 signal_template，postprocess 将回退为 legacy 简化格式"
        ]

    return signal_template, other_signal_pool, []


# ─── 轨迹解析 + 拆分 ───────────────────────────────────────────────────────


def _fix_content_quotes(raw: str) -> str:
    """
    修复自由文本字段中的 JSON 非法字符。

    处理范围：content / reason / summary / fail_reason / comment / 话术回复 / 历史消息
    修复内容：
      - 弯引号统一
      - 裸双引号转义
      - 字符串中的原始换行替换为 `\\n`
    """
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")

    content_keys = "|".join(
        re.escape(key)
        for key in ("content", "reason", "summary", "fail_reason", "comment", "话术回复", "历史消息")
    )
    key_pattern = re.compile(rf'"(?P<key>{content_keys})"\s*:\s*"')

    fixed_parts = []
    cursor = 0
    while True:
        match = key_pattern.search(raw, cursor)
        if not match:
            fixed_parts.append(raw[cursor:])
            break

        value_start = match.end()
        fixed_parts.append(raw[cursor:value_start])

        i = value_start
        value_chars = []
        while i < len(raw):
            ch = raw[i]
            if ch == "\\":
                if i + 1 < len(raw):
                    value_chars.append(raw[i:i + 2])
                    i += 2
                    continue
                value_chars.append("\\\\")
                i += 1
                continue

            if ch == '"':
                j = i + 1
                while j < len(raw) and raw[j] in " \t\r\n":
                    j += 1
                if j >= len(raw) or raw[j] in ",}]":
                    break
                value_chars.append('\\"')
                i += 1
                continue

            if ch == "\r":
                i += 1
                continue
            if ch == "\n":
                value_chars.append("\\n")
                i += 1
                continue

            value_chars.append(ch)
            i += 1

        fixed_parts.append("".join(value_chars))
        cursor = i

    return "".join(fixed_parts)



def parse_trajectory(raw_text: str) -> list:
    """
    解析 LLM 输出的轨迹。
    支持：
      - JSON array（一次多条会话）→ list[dict]
      - JSON object（单条）→ [dict]
      - 带 ```json ... ``` 包裹 → 自动剥离
    包含多层降级修复：直接解析 → 修复文本字段中的裸引号/原始换行 → regex 提取 → 错误
    """
    raw_text = raw_text.strip()
    raw_text = re.sub(r"^```json\s*", "", raw_text, flags=re.MULTILINE)
    raw_text = re.sub(r"^```\s*", "", raw_text, flags=re.MULTILINE)
    raw_text = re.sub(r"\s*```$", "", raw_text)
    raw_text = raw_text.strip()

    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass

    fixed = _fix_content_quotes(raw_text)
    try:
        parsed = json.loads(fixed)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass

    arr_match = re.search(r"(\[[\s\S]*\])", raw_text)
    if arr_match:
        try:
            parsed = json.loads(arr_match.group(1))
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            fixed2 = _fix_content_quotes(arr_match.group(1))
            try:
                parsed = json.loads(fixed2)
                return parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                pass

    raise ValueError("无法从 LLM 输出中提取 JSON array（已尝试直接解析/裸引号修复/regex 提取）")



def _merge_generated_signals(order_signals: dict, mocked_signals: dict | None) -> dict[str, str]:
    """
    合并骨架判断信号 + LLM mocked 信号为扁平 {key: value} dict。

    优先级：骨架 order_signals 中的判断信号值 > LLM mocked_signals.判断信号
    （骨架是权威来源，防止 LLM 误改）
    """
    merged: dict[str, str] = {}

    slot_signals = (mocked_signals or {}).get("参考信号", {})
    for key, value in slot_signals.items():
        merged[key] = str(value)

    llm_judgment = (mocked_signals or {}).get("判断信号", {})
    for key, value in llm_judgment.items():
        merged[key] = str(value)

    for key, value in order_signals.items():
        if key.startswith("用户反应@") or value == "__mock__":
            continue
        if isinstance(value, list):
            chosen = merged.get(key, value[0])
            if chosen not in value:
                chosen = value[0]
            merged[key] = str(chosen)
        else:
            merged[key] = str(value)

    return merged



def build_legacy_all_signals_text(order_signals: dict, mocked_signals: dict | None, generated: dict[str, str]) -> str:
    """legacy 兜底：仅输出 `### 路径信号 + ### 参考信号` 简化格式。"""
    judgment_lines = []
    for key, value in order_signals.items():
        if key.startswith("用户反应@") or value == "__mock__":
            continue
        resolved_value = generated.get(key, str(value[0]) if isinstance(value, list) else str(value))
        judgment_lines.append(f"{{{key}:{resolved_value}}}")

    slot_lines = []
    slot_signals = (mocked_signals or {}).get("参考信号", {})
    for key, value in slot_signals.items():
        if key not in order_signals:
            slot_lines.append(f"{{{key}:{value}}}")

    parts = []
    if judgment_lines:
        parts.append("### 路径信号\n" + "\n".join(judgment_lines))
    if slot_lines:
        parts.append("### 参考信号\n" + "\n".join(slot_lines))
    return "\n\n".join(parts) if parts else ""



def build_all_signals_text(
    order_signals: dict,
    mocked_signals: dict | None,
    signal_template: str | None = None,
    other_values: dict | None = None,
) -> str:
    """
    组装「所有信号」文本。

    正式主链路：signal_template 存在时走模板替换。
    legacy 兜底：signal_template 缺失时退回简化格式。
    """
    generated = _merge_generated_signals(order_signals, mocked_signals)
    if signal_template is None:
        return build_legacy_all_signals_text(order_signals, mocked_signals, generated)

    return fill_signal_template(
        template=signal_template,
        generated_signals=generated,
        other_values=other_values or {},
    )



def split_trajectory(
    traj: dict,
    *,
    task_id: str,
    order_signals: dict | None,
    signal_template: str | None,
    other_values: dict | None,
    legacy_warnings: list[str],
) -> list[dict]:
    """把单条会话（turns 嵌套）展平为逐轮预测行。"""
    turns = traj.get("turns") or traj.get("conversation", [])
    session_id = traj.get("会话id") or traj.get("session_id", "unknown")
    base_time = datetime.datetime.now()
    mocked_signals = traj.get("mocked_signals")

    if order_signals is None:
        legacy_warnings.append(
            f"{task_id}/{session_id}: 缺少 order_signals，回退使用轨迹内的 `所有信号` 字段"
        )
        all_signals_text = traj.get("所有信号", "")
    else:
        all_signals_text = build_all_signals_text(
            order_signals,
            mocked_signals,
            signal_template=signal_template,
            other_values=other_values,
        )

    rows = []
    history_parts = []
    agent_turn_idx = 0

    for i, turn in enumerate(turns):
        role = turn["role"]
        content = turn["content"]
        ts = (base_time + datetime.timedelta(seconds=i * 10)).strftime("%Y-%m-%d %H:%M:%S")

        if role == "user":
            history_parts.append(f"用户：{content}")
            continue

        if role not in ("agent", "assistant"):
            continue

        agent_turn_idx += 1
        history_str = "\n".join(history_parts) + "\n" if history_parts else ""
        outbound_update = turn.get("outbound_update")
        # 非累积模式：只有本轮触发外呼才有外呼信号，其他轮均为"无外呼记录"
        outbound_signal = outbound_update if outbound_update else "无外呼记录"

        clean_content = re.sub(r"\s*\n\s*", " ", content).strip()
        row = {
            "会话id": f"{session_id}_t{agent_turn_idx}",
            "消息时间": ts,
            "历史消息": history_str,
            "话术回复": clean_content,
            "话术关联动作": turn.get("话术关联动作", "无"),
            "话术共识动作": turn.get("话术共识动作", "无"),
            "所有信号": all_signals_text,
            "外呼信号": outbound_signal,
        }
        rows.append(row)
        history_parts.append(f"客服：{clean_content}")

    return rows


# ─── 主流程 ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="cs-data-factory Phase 2 后处理")
    parser.add_argument("--run-dir", required=True, help="运行目录（需已有 prompts_todo.jsonl 和 subagent_prompts/*.result.txt）")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写 final.jsonl")
    args = parser.parse_args()

    raw_run_dir, run_dir = resolve_run_dir(SKILL_ROOT, args.run_dir)
    result_dir = run_dir / "subagent_prompts"
    prompts_file = run_dir / "prompts_todo.jsonl"

    if not prompts_file.exists():
        print(f"❌ 找不到 prompts_todo.jsonl：{prompts_file}", file=sys.stderr)
        sys.exit(1)
    if not result_dir.exists():
        print(f"❌ 找不到 subagent_prompts 目录：{result_dir}", file=sys.stderr)
        sys.exit(1)

    exec_mode_file = run_dir / "exec_mode.json"
    if not exec_mode_file.exists():
        print(f"❌ 生产模式未确认：{exec_mode_file} 不存在", file=sys.stderr)
        print("   请先运行以下命令展示模式选项，收到用户确认后再指定 --mode：", file=sys.stderr)
        print(f"   python scripts/confirm_production_mode.py --run-dir {raw_run_dir}", file=sys.stderr)
        print(f"   （用户选 A 后）python scripts/confirm_production_mode.py --run-dir {raw_run_dir} --mode A", file=sys.stderr)
        sys.exit(1)
    exec_mode = json.loads(exec_mode_file.read_text(encoding="utf-8"))
    print(
        f"  生产模式：{exec_mode.get('mode', '?').upper()}"
        + (f"（model={exec_mode.get('model', '')}）" if exec_mode.get("mode") == "api" else "")
    )

    try:
        tasks, sop_id, legacy_warnings = load_run_tasks(prompts_file)
    except Exception as exc:
        print(f"❌ prompts_todo.jsonl 元数据加载失败：{exc}", file=sys.stderr)
        sys.exit(1)

    signal_template, other_signal_pool, context_warnings = load_signal_context(sop_id)
    legacy_warnings.extend(context_warnings)

    if signal_template is not None:
        print(f"  📋 已加载信号模板：sop_id={sop_id}（v2.0 模板替换模式）")
        print(f"  📋 补集信号池大小：{len(other_signal_pool)} 个 session")
    else:
        print("  ⚠️  当前 run 未满足 v2.0 模板替换前置条件，已切换到 legacy 简化格式")

    result_files = sorted(result_dir.glob("*.result.txt"))
    result_task_ids = {fpath.stem.replace(".result", "") for fpath in result_files}
    print(f"📂 找到 {len(result_files)} 个 result.txt 文件（共 {len(tasks)} 个任务）")

    all_rows = []
    errors = []
    parse_warnings = []
    pool_counter = 0

    missing_results = sorted(set(tasks) - result_task_ids)
    for task_id in missing_results:
        errors.append(f"{task_id}: 缺少对应的 result.txt")

    for fpath in result_files:
        task_id = fpath.stem.replace(".result", "")
        if task_id not in tasks:
            errors.append(f"{task_id}: result.txt 在 prompts_todo.jsonl 中不存在对应 task 元数据")
            print(f"  ❌ {task_id}: 缺少 task 元数据")
            continue

        raw = fpath.read_text(encoding="utf-8")
        try:
            sessions = parse_trajectory(raw)
        except Exception as exc:
            errors.append(f"{task_id}: JSON解析失败 → {exc}")
            print(f"  ❌ {task_id}: JSON解析失败 → {exc}")
            continue

        task_meta = tasks[task_id]
        order_signals = task_meta.get("order_signals")
        task_rows = []

        for sess in sessions:
            other_values = {}
            if signal_template is not None and other_signal_pool:
                other_values = other_signal_pool[pool_counter % len(other_signal_pool)]
                pool_counter += 1

            try:
                task_rows.extend(
                    split_trajectory(
                        sess,
                        task_id=task_id,
                        order_signals=order_signals,
                        signal_template=signal_template,
                        other_values=other_values,
                        legacy_warnings=legacy_warnings,
                    )
                )
            except Exception as exc:
                parse_warnings.append(f"{task_id}/{sess.get('会话id', '?')}: {exc}")

        if task_rows:
            print(f"  ✅ {task_id}: {len(task_rows)} 逐轮行")
            all_rows.extend(task_rows)
        else:
            errors.append(f"{task_id}: 展平后0行")
            print(f"  ❌ {task_id}: 展平后0行")

    print(f"\n📊 汇总：{len(all_rows)} 逐轮行 | 解析错误 {len(errors)} 个 | 解析警告 {len(parse_warnings)} 个")

    if legacy_warnings:
        print("ℹ️  legacy 兼容提示：")
        for warning in legacy_warnings:
            print(f"   {warning}")

    if parse_warnings:
        print("⚠️  解析警告：")
        for warning in parse_warnings:
            print(f"   {warning}")

    if errors:
        print("❌ 错误列表：")
        for error in errors:
            print(f"   {error}")

    if args.dry_run:
        print("\n[dry-run] 不写入文件")
        return

    if errors:
        print("❌ 存在错误，已停止写入 final.jsonl；请先补齐/修复后重跑。", file=sys.stderr)
        sys.exit(1)

    if not all_rows:
        print("❌ 无有效数据，跳过写入", file=sys.stderr)
        sys.exit(1)

    final_file = run_dir / "final.jsonl"
    with open(final_file, "w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"✅ 写入：{final_file}  ({len(all_rows)} 行)")

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = GENERATED_DIR / (run_dir.name + "_final.jsonl")
    shutil.copy(final_file, archive_path)
    print(f"📦 已归档：{archive_path}")


if __name__ == "__main__":
    main()
