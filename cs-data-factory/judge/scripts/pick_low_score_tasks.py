#!/usr/bin/env python3
"""
pick_low_score_tasks.py
-----------------------
迭代验证辅助脚本：从本轮质检结果中找出低分 session，
反解析出对应的生产 task_id，并读取原始生产模式（SubAgent/API），
输出 rerun_task_ids.json 供验证重跑使用。

用法:
  python judge/scripts/pick_low_score_tasks.py \\
      --round tuning_round_1 \\
      --batch data/runs/run_20260414_x1 \\
      [--threshold 0.6] \\
      [--top-n 10]

选项:
  --round       质检轮次名（必须，对应 data/judge_runs/<round>/judge_results.jsonl）
  --batch       原始生产 run 目录（相对 cs-data-factory/，必须）
  --threshold   normalized_score 低于此值视为低分（默认 0.6）
  --top-n       最多取多少条低分 session（默认 10，-1 表示全部）

v1.2 新增：persona/dimension 过滤以函数参数形式提供，由 Agent 在调用时传入。
用户通过自然语言交互即可（如"帮我重跑 P1 下 score_J 低的"），无需感知 CLI 参数。

输出:
  data/judge_runs/<round>/rerun_task_ids.json

格式:
  [
    {
      "session_id": "gen_P001_P1_001_s1",
      "task_id": "P001_P1_001",
      "score": 0.52,
      "persona_id": "P1",
      "path_id": "P001",
      "production_run_dir": "data/runs/run_20260414_x1",
      "production_mode": "subagent"  // 或 "api"
    },
    ...
  ]

下一步（根据 production_mode 分叉）:
  SubAgent 模式（production_mode == "subagent"）:
    python scripts/generate_prompts.py --sop-id <sop_id> --run-dir <rerun_dir> --task-ids P001_P1_001,...
    → Agent spawn SubAgent 生产 → postprocess.py

  API 模式（production_mode == "api"）:
    python scripts/generate_prompts.py --sop-id <sop_id> --run-dir <rerun_dir> --task-ids P001_P1_001,...
    python scripts/run_batch_api.py --run-dir <rerun_dir> --appid <AppId> --model <model>
    → postprocess.py
"""

import json
import re
import sys
import argparse
import datetime
from pathlib import Path

# ─── 路径 ──────────────────────────────────────────────────────────────────────
JUDGE_ROOT = Path(__file__).parent.parent   # cs-data-factory/judge/
SKILL_ROOT = JUDGE_ROOT.parent              # cs-data-factory/


# ─── session_id → task_id 反解析 ───────────────────────────────────────────────

def session_id_to_task_id(session_id: str) -> str:
    """
    将 session_id 反解析为 task_id。
    session_id 格式：gen_{task_id}_s{seq}_t{turn}
    task_id 格式：  {path_id}_{persona_id}_{batch}（如 P001_P1_001）

    注意：此函数与 analyze_root_cause.py 中的同名函数实现完全相同。
    如需修改逻辑，两处需同步更新。
    """
    m = re.match(r"gen_(.+)_s\d+_t\d+$", session_id)
    if m:
        return m.group(1)
    # session 级（已去掉 _t{turn} 后缀）
    m2 = re.match(r"gen_(.+)_s\d+$", session_id)
    if m2:
        return m2.group(1)
    # 备用：无法解析时返回 session_id 本身（调用方需容错）
    return session_id


def parse_session_meta(session_id: str) -> dict:
    """
    从 session_id 中解析 persona_id 和 path_id。
    返回: {"persona_id": "P7", "path_id": "path001"}
    """
    m = re.match(r"gen_(.+?)_(P\d+)_", session_id)
    if m:
        return {"path_id": m.group(1), "persona_id": m.group(2)}
    return {"path_id": "", "persona_id": ""}


# ─── 数据加载 ──────────────────────────────────────────────────────────────────

def load_judge_results(round_dir: Path) -> list:
    results_file = round_dir / "judge_results.jsonl"
    if not results_file.exists():
        raise FileNotFoundError(
            f"未找到 judge_results.jsonl: {results_file}\n"
            f"请先运行 run_judge.py 并完成质检汇总。"
        )
    rows = []
    with open(results_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_production_mode(production_run_dir: Path) -> str:
    """
    从生产 run 目录的 exec_mode.json 读取执行模式。
    返回 "api" 或 "subagent"（文件不存在时默认 "subagent"）。
    """
    mode_file = production_run_dir / "exec_mode.json"
    if not mode_file.exists():
        # 早期批次没有 exec_mode.json，默认为 SubAgent 模式
        return "subagent"
    try:
        data = json.loads(mode_file.read_text(encoding="utf-8"))
        return data.get("mode", "subagent")
    except Exception:
        return "subagent"


# ─── v1.2: Agent 可调用的筛选函数（非 CLI）────────────────────────────────────

def pick_low_score_tasks(results: list, threshold: float = 0.6, top_n: int = 10,
                         persona_filter: str = None, dimension_filter: str = None) -> list:
    """
    v1.2 新增：供 Agent 程序化调用的低分 session 筛选函数。

    参数:
      results:          judge_results.jsonl 加载后的 list
      threshold:        低分阈值（normalized_score）
      top_n:            最多返回条数（-1=全部）
      persona_filter:   可选，仅保留指定 Persona 的 session（如 "P1"）
      dimension_filter:  可选，仅保留在指定维度上低分的 session（如 "score_J"、"score_K"）
                        判断标准：该维度得分 < 0.6

    返回:
      筛选后的 session 列表，每个元素含 session_id, task_id, score, persona_id, path_id

    用法示例（Agent 在 phase3-judge.md Flow E 中调用）：
      from judge.scripts.pick_low_score_tasks import pick_low_score_tasks, load_judge_results
      results = load_judge_results(round_dir)
      items = pick_low_score_tasks(results, threshold=0.6, persona_filter="P1", dimension_filter="score_J")
    """
    low_sessions = []
    for r in results:
        norm = r.get("normalized_score", 0.0)
        if norm >= threshold:
            continue

        session_id = r.get("session_id", "")
        meta = parse_session_meta(session_id)
        persona_id = r.get("persona_id", meta.get("persona_id", ""))
        path_id = r.get("path_id", meta.get("path_id", ""))

        # persona 过滤
        if persona_filter and persona_id != persona_filter:
            continue

        # dimension 过滤：检查指定维度得分是否低于 0.6
        if dimension_filter:
            dim_score = r.get(dimension_filter)
            if dim_score is not None and dim_score >= 0.6:
                continue  # 该维度得分在可接受范围内，跳过

        low_sessions.append({
            "session_id": session_id,
            "task_id": session_id_to_task_id(session_id),
            "score": round(norm, 4),
            "persona_id": persona_id,
            "path_id": path_id,
            "score_J": r.get("score_J"),
            "score_K": r.get("score_K"),
        })

    low_sessions.sort(key=lambda x: x["score"])
    if top_n > 0:
        low_sessions = low_sessions[:top_n]

    return low_sessions


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-judge 低分 task 反查工具（迭代验证辅助）")
    parser.add_argument("--round", required=True,
                        help="质检轮次名（如 tuning_round_1）")
    parser.add_argument("--batch", required=True,
                        help="原始生产 run 目录（相对 cs-data-factory/，如 data/runs/run_20260414_x1）")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="normalized_score 低于此值视为低分（默认 0.6）")
    parser.add_argument("--top-n", type=int, default=10,
                        help="最多取多少条低分 session（默认 10，-1=全部）")
    args = parser.parse_args()

    round_dir = SKILL_ROOT / "data" / "judge_runs" / args.round
    production_run_dir = SKILL_ROOT / args.batch

    if not round_dir.exists():
        print(f"❌ 质检轮次目录不存在: {round_dir}")
        sys.exit(1)

    if not production_run_dir.exists():
        print(f"❌ 生产 run 目录不存在: {production_run_dir}")
        sys.exit(1)

    # ── 加载质检结果 ──
    print(f"📂 加载质检结果...")
    results = load_judge_results(round_dir)
    print(f"   共 {len(results)} 条 session 结果")

    # ── 筛选低分 session（复用 pick_low_score_tasks 函数）──
    low_items = pick_low_score_tasks(results, threshold=args.threshold, top_n=args.top_n)

    if not low_items:
        print(f"✅ 无低分 session（全部 >= {args.threshold}），无需重跑")
        return

    print(f"🔍 低分 session 数（threshold={args.threshold}）：{len(low_items)}")

    # ── 读取生产模式 ──
    production_mode = read_production_mode(production_run_dir)
    print(f"   生产模式：{production_mode}（来自 {production_run_dir}/exec_mode.json）")

    # ── 构建 rerun_task_ids 列表 ──
    rerun_items = []
    for item in low_items:
        rerun_items.append({
            "session_id": item["session_id"],
            "task_id": item["task_id"],
            "score": item["score"],
            "persona_id": item.get("persona_id", ""),
            "path_id": item.get("path_id", ""),
            "production_run_dir": args.batch,
            "production_mode": production_mode,
        })
        print(f"   [{item['score']:.3f}] {item['session_id']} → task_id: {item['task_id']}")

    # ── 输出 rerun_task_ids.json ──
    out_file = round_dir / "rerun_task_ids.json"
    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "round": args.round,
        "batch": args.batch,
        "threshold": args.threshold,
        "production_mode": production_mode,
        "count": len(rerun_items),
        "items": rerun_items,
    }
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 已输出 {len(rerun_items)} 条 → {out_file}")

    # ── 打印下一步指令 ──
    task_ids_str = ",".join(item["task_id"] for item in rerun_items)

    print(f"\n下一步（验证重跑流程）：")
    print(f"  1. 执行优化（按 root_cause_analysis.md 修改 expand-prompt.md / persona / signal_rules 等）")
    print(f"  2. 生成重跑 prompts（只针对低分 task）：")
    print(f"     python scripts/generate_prompts.py \\")
    print(f"         --sop-id <sop_id> \\")
    print(f"         --run-dir data/runs/verify_{args.round}_v1 \\")
    print(f"         --task-ids {task_ids_str}")
    print()

    if production_mode == "api":
        print(f"  3. API 模式重跑生产：")
        print(f"     python scripts/run_batch_api.py \\")
        print(f"         --run-dir data/runs/verify_{args.round}_v1 \\")
        print(f"         --appid <AppId> --model <model>")
    else:
        print(f"  3. SubAgent 模式重跑：Agent spawn SubAgent 对 data/runs/verify_{args.round}_v1 生产")

    print(f"  4. 后处理：python scripts/postprocess.py --run-dir data/runs/verify_{args.round}_v1")
    print(f"  5. 对新批次跑质检：")
    print(f"     python judge/scripts/run_judge.py --batch data/runs/verify_{args.round}_v1 \\")
    print(f"         --round verify_{args.round}_v1 --judge-version v1.1 -n -1 --export-prompts")
    print(f"  6. 质检汇总后，让 Agent 读两份 report.md + final.jsonl 对比话术，生成 compare_report.md")


if __name__ == "__main__":
    main()
