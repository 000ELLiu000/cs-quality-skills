#!/usr/bin/env python3
"""
collect_subagent_results.py
----------------------------
从 SubAgent 写入的 result.txt 文件汇总成 judge_results.jsonl。
评分粒度：session 维度，每个 result.txt 对应一个 session 的整体评分。

用法:
  python judge/scripts/collect_subagent_results.py --round r1

说明:
  - 扫描 data/judge_runs/<round>/subagent_prompts/*.result.txt
  - 自动修复常见 JSON 错误（reason/summary 字段中的裸双引号）
  - 写入 data/judge_runs/<round>/judge_results.jsonl
"""

import io
import json
import re
import sys
import argparse
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

JUDGE_ROOT = Path(__file__).parent.parent
SKILL_ROOT = JUDGE_ROOT.parent


def fix_json_string_quotes(raw: str) -> str:
    """
    修复 JSON 中 reason/summary/fail_reason 等字段值里的裸双引号。
    例如: "reason": "用户说"你好"，..." → "reason": "用户说\\"你好\\"，..."

    策略：逐行扫描，对 string value 行（key: "value" 格式）内部的裸引号转义。
    """
    lines = raw.split('\n')
    fixed_lines = []
    string_value_keys = {'reason', 'summary', 'fail_reason', 'comment', 'note'}

    for line in lines:
        stripped = line.lstrip()
        # 识别 "<key>": "<value>" 形式的行
        m = re.match(r'^"([^"]+)":\s*"(.*)"(,?)$', stripped)
        if m and m.group(1) in string_value_keys:
            indent = line[:len(line) - len(stripped)]
            key = m.group(1)
            value = m.group(2)
            trailing = m.group(3)
            # 将 value 中未转义的 " 转义
            fixed_value = re.sub(r'(?<!\\)"', '\\"', value)
            fixed_lines.append(f'{indent}"{key}": "{fixed_value}"{trailing}')
            continue
        fixed_lines.append(line)

    return '\n'.join(fixed_lines)


def normalize_quotes(raw: str) -> str:
    """
    将 LLM 输出中常见的 Unicode 弯引号替换为标准 ASCII 引号，防止 JSON 解析失败。
    处理：
      \u2018 \u2019  →  '   (左右单弯引号 → 直单引号)
      \u201c \u201d  →  "   (左右双弯引号 → 直双引号，谨慎：仅在字符串值内部)
    """
    raw = raw.replace('\u2018', "'").replace('\u2019', "'")
    # 双弯引号不能无脑替换（会破坏 JSON key/value 边界），仅在字符串值内部出现时替换
    # 实际 LLM 输出中双弯引号极少，仅记录提示，不自动替换
    return raw


def load_result_file(path: Path):
    """
    加载并解析 result.txt，自动修复常见 JSON 问题。
    支持两种格式：
      - dict（单 session 评分）→ 返回 dict
      - list（批次评分，多个 session）→ 返回 list[dict]
    """
    raw = path.read_text(encoding='utf-8').strip()

    # 去掉 markdown code block 包裹（部分 LLM 输出会有）
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    # 预处理：统一弯引号 → 直引号（SubAgent 输出中常见）
    raw = normalize_quotes(raw)

    # 先尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 尝试修复 reason/summary 中的裸双引号
    fixed = fix_json_string_quotes(raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败（修复后仍错）: {e}") from e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", default="tuning_round_1", help="质检轮次目录名（默认 tuning_round_1）")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已有的 judge_results.jsonl（默认追加新记录）")
    args = parser.parse_args()

    round_dir = SKILL_ROOT / "data" / "judge_runs" / args.round
    prompt_dir = round_dir / "subagent_prompts"

    # ── exec_mode.json 校验：API 模式下无需此脚本 ──
    mode_file = round_dir / "exec_mode.json"
    if mode_file.exists():
        mode_data = json.loads(mode_file.read_text(encoding="utf-8"))
        if mode_data.get("mode") == "api":
            print(f"ℹ️  本轮 judge 使用了 API 模式（{mode_data.get('model', '')}）。")
            print(f"   API 模式下结果已由 run_judge.py 直接写入 judge_results.jsonl，无需执行此步骤。")
            print(f"   下一步：python judge/scripts/gen_report.py --round {args.round}")
            sys.exit(0)

    if not prompt_dir.exists():
        print(f"ERROR: {prompt_dir} 不存在")
        return

    # 兼容两种命名：单 session（{session_id}.result.txt）和批次（batch_XXX.result.txt）
    result_files = sorted(prompt_dir.glob("*.result.txt"))
    print(f"发现 {len(result_files)} 个 result 文件")

    # 检查已存在的 judge_results.jsonl，避免重复写入
    output = round_dir / "judge_results.jsonl"
    existing_ids: set[str] = set()
    if output.exists() and not args.overwrite:
        with open(output, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        row = json.loads(line)
                        existing_ids.add(row.get("session_id", ""))
                    except Exception:
                        pass
        if existing_ids:
            print(f"   已有 {len(existing_ids)} 条记录，将跳过重复（用 --overwrite 强制覆盖）")

    records = []
    failed = []
    fixed_count = 0

    for rf in result_files:
        # 文件名格式有两种：
        #   单 session：{session_id}.result.txt → task_id = session_id
        #   批次：batch_XXX.result.txt → task_id = batch_XXX（内含多个 session）
        stem = rf.name
        if stem.endswith(".result.txt"):
            task_id = stem[:-len(".result.txt")]
        else:
            task_id = rf.stem

        raw_before_fix = rf.read_text(encoding='utf-8').strip()
        try:
            data = load_result_file(rf)

            # 检测是否经历了修复（重新序列化后写回）
            try:
                json.loads(raw_before_fix)
            except Exception:
                rf.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding='utf-8'
                )
                fixed_count += 1

            # 统一处理：data 可能是 dict（单 session）或 list（批次）
            items = data if isinstance(data, list) else [data]
            for item in items:
                # 批次格式中每个 item 的 session_id 应已内含
                sid = item.get("session_id") or task_id
                if sid in existing_ids:
                    continue
                item["session_id"] = sid
                records.append(item)
        except Exception as e:
            print(f"  SKIP {task_id}: {e}")
            failed.append(task_id)

    # 写入
    mode = "w" if args.overwrite else "a"
    with open(output, mode, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"写入 {len(records)} 条 → {output}")
    if fixed_count:
        print(f"自动修复 JSON {fixed_count} 个文件（reason/summary 字段裸引号）")
    if failed:
        print(f"跳过 {len(failed)} 条（解析失败）: {failed}")


if __name__ == "__main__":
    main()
