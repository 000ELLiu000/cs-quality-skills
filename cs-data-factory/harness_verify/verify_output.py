#!/usr/bin/env python3
"""
verify_output.py
----------------
Phase 2 节点校验：检查 final.jsonl 的格式完整性。

格式层校验（不做语义判断）：
  1. 必须字段完整（会话id / 消息时间 / 历史消息 / 话术回复 / 话术关联动作 / 话术共识动作 / 所有信号 / 外呼信号）
  2. 会话id 格式（gen_PXXX_PX_NNN_sN_tN 或 gen_PXXX_PX_NNN_tN）
  3. 话术回复非空，不含换行符
  4. 话术关联动作 / 话术共识动作 值在合法动作列表中（从 `{sop_id}_graph.json` 动态加载，支持 X 占位符模式匹配）
  5. 消息时间格式（YYYY-MM-DD HH:MM:SS）
  6. 统计：session 覆盖数 / 总 turn 数 / 平均 turn 数

合并了原 scripts/pipeline_check.py，新增 --json 报告输出和目录批量扫描。

用法：
  # 单文件校验
  python harness_verify/verify_output.py --file data/runs/run_xxx/final.jsonl --sop-id invoice
  python harness_verify/verify_output.py --run-dir data/runs/run_xxx --sop-id invoice

  # 目录批量扫描（扫描目录下所有 final.jsonl）
  python harness_verify/verify_output.py --scan-dir data/runs --sop-id invoice

  # 输出 JSON 格式报告（供自动化流程消费）
  python harness_verify/verify_output.py --file data/runs/run_xxx/final.jsonl --sop-id invoice --json

  # strict 模式：warning 也视为失败
  python harness_verify/verify_output.py --file data/runs/run_xxx/final.jsonl --sop-id invoice --strict
"""

import json
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

SKILL_ROOT = Path(__file__).parent.parent
DATA_DIR = SKILL_ROOT / "data"

REQUIRED_FIELDS = [
    "会话id", "消息时间", "历史消息", "话术回复",
    "话术关联动作", "话术共识动作", "所有信号", "外呼信号",
]

SESSION_ID_RE = re.compile(r"^gen_P\d+_P\d+_\d+(_s\d+)?_t\d+$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# 外呼信号格式校验：非空时须含对话格式特征（每行以「客服:」或「商家:」开头）
OUTBOUND_EMPTY = "无外呼记录"
OUTBOUND_DIALOG_RE = re.compile(r"^(客服:|商家:)", re.MULTILINE)


# ─── 动作加载 ───────────────────────────────────────────────────────────────────

def load_valid_actions(sop_id: str):
    """从 `{sop_id}_graph.json` 动态加载合法动作值，包含「无」。

    返回 tuple(exact_set, pattern_list)：
      - exact_set: 不含占位符的精确匹配集合
      - pattern_list: 含 X 占位符的动作模式，已编译为正则
        规则：X 匹配任意非空字符序列，以支持「操作赔付-优惠券-X元」→「操作赔付-优惠券-5元」
    """
    graph_file = DATA_DIR / "sop" / f"{sop_id}_graph.json"
    if not graph_file.exists():
        return {"无"}, []
    with open(graph_file, encoding="utf-8") as f:
        graph = json.load(f)
    exact_set = {"无"}
    pattern_list = []
    for action in graph.get("actions", {}).values():
        for tag_key in ("action_tag", "consensus_tag"):
            tag = action.get(tag_key, "")
            if not tag or tag == "无":
                continue
            if "X" in tag:
                # 注：re.escape() 不会转义字母 X，所以直接替换 X 为 .+
                escaped = re.escape(tag).replace("X", ".+")
                pat = re.compile("^" + escaped + "$")
                if pat not in pattern_list:
                    pattern_list.append(pat)
            else:
                exact_set.add(tag)
    return exact_set, pattern_list


def _action_is_valid(action_value: str, exact_set: set, pattern_list: list) -> bool:
    """检查动作值是否合法（精确匹配 or 模式匹配）。"""
    if action_value in exact_set:
        return True
    for pat in pattern_list:
        if pat.match(action_value):
            return True
    return False


# ─── 行校验 ────────────────────────────────────────────────────────────────────

def check_row(row: dict, valid_actions) -> list:
    """校验单行，返回 issue 列表。

    valid_actions: tuple(exact_set, pattern_list) 或 set（兼容旧用法）
    """
    if isinstance(valid_actions, set):
        exact_set, pattern_list = valid_actions, []
    else:
        exact_set, pattern_list = valid_actions

    issues = []

    # 1. 必须字段
    for field in REQUIRED_FIELDS:
        if field not in row:
            issues.append({"field": field, "issue": "字段缺失", "severity": "error"})

    # 2. 会话id 格式
    session_id = row.get("会话id", "")
    if session_id and not SESSION_ID_RE.match(session_id):
        issues.append({
            "field": "会话id",
            "issue": f"格式不符（期望 gen_PXXX_PX_NNN_sN_tN，实际 {session_id!r}）",
            "severity": "warning",
        })

    # 3. 话术回复非空 + 无换行
    reply = row.get("话术回复", "")
    if not reply.strip():
        issues.append({"field": "话术回复", "issue": "话术回复为空", "severity": "error"})
    elif "\n" in reply:
        issues.append({
            "field": "话术回复",
            "issue": "含换行符（应为一段连续文本）",
            "severity": "warning",
        })

    # 4. 动作合规
    for field in ("话术关联动作", "话术共识动作"):
        val = row.get(field, "")
        if val and not _action_is_valid(val, exact_set, pattern_list):
            issues.append({
                "field": field,
                "issue": f"值「{val}」不在合法动作列表中",
                "severity": "error",
            })

    # 5. 消息时间格式
    ts = row.get("消息时间", "")
    if ts and not TIME_RE.match(ts):
        issues.append({
            "field": "消息时间",
            "issue": f"格式不符（期望 YYYY-MM-DD HH:MM:SS，实际 {ts!r}）",
            "severity": "warning",
        })

    # 6. 外呼信号格式软校验（非空时须含对话格式特征）
    outbound = row.get("外呼信号", "")
    if outbound and outbound != OUTBOUND_EMPTY:
        if not OUTBOUND_DIALOG_RE.search(outbound):
            issues.append({
                "field": "外呼信号",
                "issue": (
                    f"格式疑似摘要而非对话记录（期望每行以「客服:」或「商家:」开头的多轮对话；"
                    f"实际内容开头：{outbound[:50]!r}）"
                ),
                "severity": "warning",
            })

    return issues


# ─── 文件校验 ──────────────────────────────────────────────────────────────────

def check_file(target: Path, sop_id: str, strict: bool = False,
               output_json: bool = False, verbose: bool = True) -> dict:
    """校验单个 final.jsonl 文件，返回报告 dict。"""
    valid_actions = load_valid_actions(sop_id)
    exact_set, pattern_list = valid_actions

    if verbose:
        print(f"🔍 校验：{target}")
        print(f"   sop_id={sop_id}  合法动作数={len(exact_set)}（精确）+ {len(pattern_list)}（模式）")

    if not target.exists():
        msg = f"文件不存在：{target}"
        if verbose:
            print(f"❌ {msg}", file=sys.stderr)
        return {"file": str(target), "status": "error", "message": msg,
                "error_count": 0, "warning_count": 0, "details": []}

    rows = []
    parse_errors = 0
    with open(target, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append((i + 1, json.loads(line)))
            except json.JSONDecodeError as e:
                parse_errors += 1
                if verbose:
                    print(f"   ❌ 第{i+1}行 JSON 格式错误：{e}")

    if verbose:
        print(f"   共 {len(rows)} 行（JSON 解析失败 {parse_errors} 行）")

    # session 统计
    session_map = defaultdict(list)
    for _, row in rows:
        base = re.sub(r"_t\d+$", "", row.get("会话id", "unknown"))
        session_map[base].append(row)

    all_issues = []
    error_count = parse_errors
    warning_count = 0

    for row_num, row in rows:
        issues = check_row(row, valid_actions)
        if issues:
            all_issues.append({
                "row": row_num,
                "session_id": row.get("会话id", "?"),
                "issues": issues,
            })
            for iss in issues:
                if iss["severity"] == "error":
                    error_count += 1
                else:
                    warning_count += 1

    session_count = len(session_map)
    turn_count = len(rows)
    avg_turns = turn_count / session_count if session_count else 0

    if verbose:
        print(f"\n📊 统计：{session_count} 个 session，{turn_count} 条 turn，平均 {avg_turns:.1f} turn/session")

    if verbose and all_issues:
        print(f"\n{'⚠️ ' if not error_count else '❌ '}问题明细（{error_count} 个 error，{warning_count} 个 warning）：")
        for item in all_issues[:20]:
            for iss in item["issues"]:
                icon = "❌" if iss["severity"] == "error" else "⚠️"
                print(f"   {icon} 第{item['row']}行 [{item['session_id']}] {iss['field']}: {iss['issue']}")
        if len(all_issues) > 20:
            print(f"   ... 另有 {len(all_issues) - 20} 行有问题（已省略）")

    has_failure = error_count > 0 or (strict and warning_count > 0)
    status = "failed" if has_failure else "passed"

    if verbose:
        print(f"\n{'❌ 校验失败' if has_failure else '✅ 校验通过'}"
              f"（error={error_count}，warning={warning_count}）")

    return {
        "file": str(target),
        "status": status,
        "session_count": session_count,
        "turn_count": turn_count,
        "avg_turns": round(avg_turns, 1),
        "error_count": error_count,
        "warning_count": warning_count,
        "details": all_issues,
    }


# ─── 目录批量扫描 ───────────────────────────────────────────────────────────────

def scan_dir(scan_path: Path, sop_id: str, strict: bool = False) -> list:
    """递归扫描目录下所有 final.jsonl，返回报告列表。"""
    results = []
    for f in sorted(scan_path.rglob("final.jsonl")):
        report = check_file(f, sop_id, strict=strict, verbose=False)
        icon = "✅" if report["status"] == "passed" else "❌"
        print(f"  {icon} {f.relative_to(scan_path)}  "
              f"session={report['session_count']} turn={report['turn_count']} "
              f"error={report['error_count']} warning={report['warning_count']}")
        results.append(report)
    return results


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 2 节点校验：final.jsonl 格式检查")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--file", help="直接指定 final.jsonl 文件路径")
    grp.add_argument("--run-dir", help="运行目录（自动读取 final.jsonl，支持 data/runs/ 自动补全）")
    grp.add_argument("--scan-dir", help="扫描目录，递归找所有 final.jsonl 批量检验")
    parser.add_argument("--sop-id", default="invoice",
                        help="SOP 标识（用于加载合法动作列表，默认 invoice）")
    parser.add_argument("--strict", action="store_true", help="warning 也视为失败")
    parser.add_argument("--json", dest="output_json", action="store_true",
                        help="以 JSON 格式输出完整报告（供自动化流程消费）")
    args = parser.parse_args()

    # ── 批量扫描模式 ──
    if args.scan_dir:
        scan_path = Path(args.scan_dir)
        if not scan_path.is_absolute():
            scan_path = SKILL_ROOT / scan_path
        print(f"\n📂 批量扫描：{scan_path}")
        results = scan_dir(scan_path, args.sop_id, strict=args.strict)
        total = len(results)
        passed = sum(1 for r in results if r["status"] == "passed")
        failed = total - passed
        if args.output_json:
            print(json.dumps(
                {"total": total, "passed": passed, "failed": failed, "results": results},
                ensure_ascii=False, indent=2,
            ))
        else:
            print(f"\n{'='*50}")
            print(f"总计：{total}  通过：{passed}  失败：{failed}")
            print(f"{'='*50}\n")
        sys.exit(1 if failed > 0 else 0)

    # ── 单文件模式 ──
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = SKILL_ROOT / target
    else:
        raw_run_dir = args.run_dir
        if not Path(raw_run_dir).is_absolute() and not raw_run_dir.startswith("data/runs"):
            raw_run_dir = f"data/runs/{raw_run_dir}"
        run_dir = SKILL_ROOT / raw_run_dir
        target = run_dir / "final.jsonl"

    report = check_file(target, args.sop_id, strict=args.strict,
                        output_json=args.output_json, verbose=not args.output_json)

    if args.output_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    sys.exit(1 if report["status"] in ("failed", "error") else 0)


if __name__ == "__main__":
    main()
