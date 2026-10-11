#!/usr/bin/env python3
"""
run_regression.py
-----------------
Phase 3 回归测试：对 regression_set.jsonl 运行两个版本的 Judge Prompt，输出 diff 报告。

用法:
  python judge/scripts/run_regression.py \\
      --version-a v1.0 \\
      --version-b v1.1 \\
      --appid <模型 API Key> \\
      [--model your-model] \\
      [--workers 4] \\
      [--dry-run]

  # 只跑单个版本（建立基线）
  python judge/scripts/run_regression.py \\
      --version-a v1.0 \\
      --appid <AppId>

选项:
  --version-a     基线版本（必须）
  --version-b     对比版本（可选，不传则只建立 version-a 基线）
  --appid         模型 API Key（必须，用于调用 LLM 打分）
  --model         模型名（默认 your-model）
  --workers       并发线程数（默认 4）
  --dry-run       打印前 2 条 prompt，不调用 API

回归集：judge/regression/regression_set.jsonl
  每条格式：session 完整对话数据 + ground_truth 字段
  ground_truth: {"normalized_score": 0.8, "dimension_scores": {...}, "notes": "..."}

  若回归集为空，脚本会提示并退出（框架就位，等第一轮调优后建立回归集）。
"""

import json
import re
import sys
import argparse
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# ─── 路径 ──────────────────────────────────────────────────────────────────────
JUDGE_ROOT = Path(__file__).parent.parent   # cs-data-factory/judge/
SKILL_ROOT = JUDGE_ROOT.parent              # cs-data-factory 根目录

# lib/api_client 供 API 调用使用
sys.path.insert(0, str(SKILL_ROOT))
from lib.api_client import call_api as _call_api

# Judge Prompt 加载复用 run_judge.py 的实现（保证提取逻辑一致）
sys.path.insert(0, str(Path(__file__).parent))
from run_judge import load_judge_prompt as _load_judge_prompt_impl

# ─── 回归目录 ──────────────────────────────────────────────────────────────────
REGRESSION_DIR = JUDGE_ROOT / "regression"
RESULTS_DIR = REGRESSION_DIR / "results"


# ─── 模块级 API 配置（由 main() 初始化）──────────────────────────────────────────
_appid: str = ""
_model: str = ""


def call_llm(prompt: str, task_id: str, temperature: float = 0.0) -> str:
    """调用 兼容 Chat Completions 的模型 API 执行打分。appid/model 由 main() 初始化后使用。"""
    if not _appid:
        raise RuntimeError(
            "API 未配置，请通过 --appid 参数指定 AppId。"
        )
    try:
        return _call_api(
            prompt=prompt,
            appid=_appid,
            model=_model,
            temperature=temperature,
            max_tokens=2048,
        )
    except Exception as e:
        raise RuntimeError(f"任务 {task_id} API 调用失败: {e}")


# ─── Judge Prompt 加载（复用 run_judge.py，确保提取逻辑与正式质检一致）───────────

def load_judge_prompt(version: str) -> str:
    """从 run_judge.py 复用，保证 export-prompts 和回归测试使用完全相同的 prompt 提取逻辑。"""
    return _load_judge_prompt_impl(version)


def build_judge_input(turn_row: dict, template: str) -> str:
    prompt = template
    for key, value in turn_row.items():
        placeholder = "{{" + key + "}}"
        prompt = prompt.replace(placeholder, str(value) if value else "")
    return prompt


def parse_judge_result(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ─── 回归集加载 ────────────────────────────────────────────────────────────────

def load_regression_set() -> list:
    reg_file = REGRESSION_DIR / "regression_set.jsonl"
    if not reg_file.exists():
        return []
    rows = []
    with open(reg_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ─── 单任务执行 ────────────────────────────────────────────────────────────────

def run_task(turn_row: dict, template: str, version: str, dry_run: bool) -> tuple:
    task_id = turn_row.get("会话id", "unknown")
    try:
        prompt = build_judge_input(turn_row, template)
        if dry_run:
            return task_id, None, None, prompt
        raw = call_llm(prompt, task_id)
        result = parse_judge_result(raw)
        result["_version"] = version
        result["_task_id"] = task_id
        return task_id, result, None, None
    except Exception as e:
        return task_id, None, str(e), None


def run_version(rows: list, version: str, workers: int, dry_run: bool) -> dict:
    """对所有回归样本跑一个版本，返回 {task_id: result}"""
    template = load_judge_prompt(version)
    results = {}
    failed = []

    print(f"\n  🔄 运行版本 {version}（{len(rows)} 条）...")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_task, row, template, version, dry_run): row for row in rows}
        for future in as_completed(futures):
            task_id, result, error, prompt_preview = future.result()
            if dry_run and prompt_preview:
                print(f"\n{'='*50}\nDRY-RUN: {task_id} [{version}]\n{'='*50}")
                print(prompt_preview[:1500])
                print("...")
            elif error:
                print(f"    ❌ {task_id}: {error[:80]}")
                failed.append(task_id)
            else:
                score = result.get("normalized_score", "?")
                print(f"    ✅ {task_id}: score={score}")
                results[task_id] = result

    if failed:
        print(f"  ⚠️  {len(failed)} 条失败: {failed}")
    return results


# ─── Diff 计算 ─────────────────────────────────────────────────────────────────

REGRESSION_THRESHOLD = 0.1  # 退化阈值


def compute_diff(results_a: dict, results_b: dict, regression_set: list) -> dict:
    """对比两个版本的打分结果，计算 diff"""
    diffs = []
    dim_diffs: dict[str, list] = defaultdict(list)

    for row in regression_set:
        task_id = row.get("会话id", "")
        gt = row.get("ground_truth", {})
        ra = results_a.get(task_id)
        rb = results_b.get(task_id)

        if not ra or not rb:
            continue

        score_a = ra.get("normalized_score", 0.0)
        score_b = rb.get("normalized_score", 0.0)
        score_delta = round(score_b - score_a, 3)

        gt_score = gt.get("normalized_score") if gt else None
        gt_delta_a = round(score_a - gt_score, 3) if gt_score is not None else None
        gt_delta_b = round(score_b - gt_score, 3) if gt_score is not None else None

        # 各维度 diff
        dim_detail = {}
        dims_a = ra.get("dimension_scores", {})
        dims_b = rb.get("dimension_scores", {})
        gt_dims = gt.get("dimension_scores", {}) if gt else {}
        all_dims = set(list(dims_a.keys()) + list(dims_b.keys()))
        for dim in all_dims:
            da = dims_a.get(dim, {})
            db = dims_b.get(dim, {})
            na = (da.get("score", 0) / da.get("max_score", 1)) if isinstance(da, dict) else float(da)
            nb = (db.get("score", 0) / db.get("max_score", 1)) if isinstance(db, dict) else float(db)
            delta = round(nb - na, 3)
            dim_diffs[dim].append(delta)
            regressed = delta < -REGRESSION_THRESHOLD
            dim_detail[dim] = {
                "score_a": round(na, 3),
                "score_b": round(nb, 3),
                "delta": delta,
                "regressed": regressed,
            }
            if gt_dims.get(dim) is not None:
                gt_d = gt_dims[dim]
                gt_n = (gt_d.get("score", 0) / gt_d.get("max_score", 1)) if isinstance(gt_d, dict) else float(gt_d)
                dim_detail[dim]["gt"] = round(gt_n, 3)

        diffs.append({
            "task_id": task_id,
            "score_a": score_a,
            "score_b": score_b,
            "score_delta": score_delta,
            "gt_score": gt_score,
            "gt_delta_a": gt_delta_a,
            "gt_delta_b": gt_delta_b,
            "regressed": score_delta < -REGRESSION_THRESHOLD,
            "dimensions": dim_detail,
            "notes": gt.get("notes", "") if gt else "",
        })

    # 维度级别汇总
    dim_summary = {}
    for dim, deltas in dim_diffs.items():
        avg_delta = round(sum(deltas) / len(deltas), 3) if deltas else 0.0
        regressed_count = sum(1 for d in deltas if d < -REGRESSION_THRESHOLD)
        dim_summary[dim] = {
            "avg_delta": avg_delta,
            "regressed_count": regressed_count,
            "total": len(deltas),
            "regressed": avg_delta < -REGRESSION_THRESHOLD,
        }

    regressed_sessions = [d for d in diffs if d["regressed"]]
    improved_sessions = [d for d in diffs if d["score_delta"] > REGRESSION_THRESHOLD]

    return {
        "total": len(diffs),
        "regressed_count": len(regressed_sessions),
        "improved_count": len(improved_sessions),
        "stable_count": len(diffs) - len(regressed_sessions) - len(improved_sessions),
        "avg_delta": round(sum(d["score_delta"] for d in diffs) / len(diffs), 3) if diffs else 0.0,
        "dim_summary": dim_summary,
        "details": diffs,
    }


# ─── 报告生成 ──────────────────────────────────────────────────────────────────

def render_diff_report(diff: dict, version_a: str, version_b: str) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 回归测试报告 — {version_a} vs {version_b}",
        f"",
        f"> 生成时间：{now}  |  退化阈值：{REGRESSION_THRESHOLD}",
        f"",
        f"---",
        f"",
        f"## 一、总览",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 测试样本数 | {diff['total']} |",
        f"| 平均分变化（B - A） | **{diff['avg_delta']:+.3f}** |",
        f"| 退化样本数（delta < -{REGRESSION_THRESHOLD}） | **{diff['regressed_count']}** |",
        f"| 改善样本数（delta > +{REGRESSION_THRESHOLD}） | {diff['improved_count']} |",
        f"| 稳定样本数 | {diff['stable_count']} |",
        f"",
    ]

    # 快速结论
    if diff["regressed_count"] == 0:
        lines.append(f"✅ 无退化，{version_b} 可安全替换 {version_a}。")
    elif diff["regressed_count"] <= diff["total"] * 0.1:
        lines.append(f"⚠️ {diff['regressed_count']} 条轻微退化，建议人工复核后再决定是否切换。")
    else:
        lines.append(f"❌ {diff['regressed_count']} 条退化（>{diff['total']*0.1:.0f}条），**不建议切换**，需排查原因。")

    lines += [
        f"",
        f"---",
        f"",
        f"## 二、各维度 Diff",
        f"",
        f"| 维度 | 平均 delta | 退化数/{diff['total']} | 结论 |",
        f"|---|---|---|---|",
    ]

    sorted_dims = sorted(
        diff["dim_summary"].items(), key=lambda x: x[1]["avg_delta"]
    )
    for dim, info in sorted_dims:
        flag = "🔴 退化" if info["regressed"] else ("🟢 改善" if info["avg_delta"] > 0 else "➡️ 稳定")
        lines.append(
            f"| {dim} | {info['avg_delta']:+.3f} | {info['regressed_count']}/{info['total']} | {flag} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 三、逐样本对比",
        f"",
        f"| Task ID | 得分 A | 得分 B | Delta | GT 得分 | 结论 |",
        f"|---|---|---|---|---|---|",
    ]

    for d in sorted(diff["details"], key=lambda x: x["score_delta"]):
        flag = "🔴" if d["regressed"] else ("🟢" if d["score_delta"] > REGRESSION_THRESHOLD else "➡️")
        gt_str = f"{d['gt_score']:.3f}" if d["gt_score"] is not None else "—"
        lines.append(
            f"| {d['task_id']} | {d['score_a']:.3f} | {d['score_b']:.3f} | "
            f"{d['score_delta']:+.3f} | {gt_str} | {flag} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 四、退化样本详情",
        f"",
    ]

    regressed = [d for d in diff["details"] if d["regressed"]]
    if not regressed:
        lines.append("（无退化样本）")
    else:
        for d in regressed:
            lines += [
                f"### {d['task_id']}",
                f"- 得分：A={d['score_a']:.3f} → B={d['score_b']:.3f}（delta={d['score_delta']:+.3f}）",
            ]
            if d["gt_score"] is not None:
                lines.append(f"- GT 参考：{d['gt_score']:.3f}（A 偏差={d['gt_delta_a']:+.3f}，B 偏差={d['gt_delta_b']:+.3f}）")
            if d["notes"]:
                lines.append(f"- 备注：{d['notes']}")
            # 退化维度
            regressed_dims = [(dim, info) for dim, info in d["dimensions"].items() if info["regressed"]]
            if regressed_dims:
                lines.append(f"- 退化维度：")
                for dim, info in regressed_dims:
                    lines.append(f"  - {dim}: {info['score_a']:.3f} → {info['score_b']:.3f}（delta={info['delta']:+.3f}）")
            lines.append("")

    lines += [
        f"---",
        f"",
        f"*由 run_regression.py 自动生成*",
    ]

    return "\n".join(lines)


def render_baseline_report(results_a: dict, version_a: str, regression_set: list) -> str:
    """只跑单版本时，生成基线报告（与 ground truth 对比）"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 回归基线报告 — {version_a}",
        f"",
        f"> 生成时间：{now}",
        f"",
        f"---",
        f"",
        f"## 样本得分",
        f"",
        f"| Task ID | Judge 得分 | GT 得分 | 偏差 |",
        f"|---|---|---|---|",
    ]

    for row in regression_set:
        task_id = row.get("会话id", "")
        gt = row.get("ground_truth", {})
        result = results_a.get(task_id)
        if not result:
            continue
        score = result.get("normalized_score", 0.0)
        gt_score = gt.get("normalized_score") if gt else None
        gt_str = f"{gt_score:.3f}" if gt_score is not None else "—"
        delta_str = f"{score - gt_score:+.3f}" if gt_score is not None else "—"
        lines.append(f"| {task_id} | {score:.3f} | {gt_str} | {delta_str} |")

    lines += [
        f"",
        f"---",
        f"",
        f"*基线报告，用于后续版本回归对比*",
    ]
    return "\n".join(lines)


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-judge 回归测试")
    parser.add_argument("--version-a", required=True, help="基线 Judge Prompt 版本（如 v1.0）")
    parser.add_argument("--version-b", default=None, help="对比版本（不传则只建立基线）")
    parser.add_argument("--appid", default="", help="模型 API Key（dry-run 时可不填）")
    parser.add_argument("--model", default="your-model", help="模型名（默认 your-model）")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数（默认 4）")
    parser.add_argument("--dry-run", action="store_true", help="打印前 2 条 prompt，不调用 API")
    args = parser.parse_args()

    # ── 初始化 API 配置 ──
    global _appid, _model
    from lib.api_client import resolve_appid
    try:
        _appid = resolve_appid(args.appid)
    except ValueError:
        _appid = ""
    _model = args.model

    if not args.dry_run and not _appid:
        print("❌ 请通过 --appid 参数指定 模型 API Key")
        print("   示例：python judge/scripts/run_regression.py --version-a v1.0 --appid <AppId>")
        return

    # ── 加载回归集 ──
    regression_set = load_regression_set()
    if not regression_set:
        print("⚠️  回归集为空（judge/regression/regression_set.jsonl 不存在或无数据）")
        print("   请在第一轮调优结束后，从最差 sessions 中挑 10-15 条建立回归集。")
        print("   回归集格式：turn 行数据 + ground_truth 字段")
        print("   示例：{\"会话id\": \"S001_t1\", ..., \"ground_truth\": {\"normalized_score\": 0.8}}")
        return

    print(f"📂 回归集：{len(regression_set)} 条样本")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── 运行 version-a ──
    results_a = run_version(regression_set, args.version_a, args.workers, args.dry_run)
    if args.dry_run:
        return

    if not args.version_b:
        # 只建基线
        report = render_baseline_report(results_a, args.version_a, regression_set)
        out_file = RESULTS_DIR / f"{timestamp}_{args.version_a}_baseline.md"
        out_file.write_text(report, encoding="utf-8")
        print(f"\n✅ 基线报告已生成: {out_file}")
        return

    # ── 运行 version-b ──
    results_b = run_version(regression_set, args.version_b, args.workers, args.dry_run)

    # ── 计算 diff ──
    print(f"\n📊 计算 diff（{args.version_a} vs {args.version_b}）...")
    diff = compute_diff(results_a, results_b, regression_set)

    # ── 生成报告 ──
    report = render_diff_report(diff, args.version_a, args.version_b)
    out_file = RESULTS_DIR / f"{timestamp}_{args.version_a}_vs_{args.version_b}.md"
    out_file.write_text(report, encoding="utf-8")

    print(f"\n✅ 回归报告已生成: {out_file}")
    print(f"\n--- 摘要 ---")
    print(f"  样本数: {diff['total']}  平均 delta: {diff['avg_delta']:+.3f}")
    print(f"  退化: {diff['regressed_count']}  改善: {diff['improved_count']}  稳定: {diff['stable_count']}")
    if diff["regressed_count"] == 0:
        print(f"  ✅ 无退化，{args.version_b} 可安全切换")
    else:
        print(f"  ⚠️  有退化，建议人工复核后再决定")


if __name__ == "__main__":
    main()
