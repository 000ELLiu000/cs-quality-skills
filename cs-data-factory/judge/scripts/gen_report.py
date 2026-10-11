#!/usr/bin/env python3
"""
gen_report.py
-------------
Phase 3 第二步：读取 judge_results.jsonl，生成质检报告 report.md。

用法:
  python judge/scripts/gen_report.py \\
      --round tuning_round_1 \\
      [--pass-threshold 0.6]

选项:
  --round           质检轮次名（默认 tuning_round_1）
  --pass-threshold  session 通过分数线（归一化，默认 0.6，即 1-5分制下约3.4分）
"""

import io
import sys
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import json
import argparse
import datetime
from pathlib import Path
from collections import defaultdict, Counter

# ─── 路径 ──────────────────────────────────────────────────────────────────────
JUDGE_ROOT = Path(__file__).parent.parent   # cs-data-factory/judge/
SKILL_ROOT = JUDGE_ROOT.parent              # cs-data-factory 根目录


# ─── 数据加载 ──────────────────────────────────────────────────────────────────

def load_sessions_index() -> dict:
    """
    遍历 data/runs/ 下所有 final.jsonl，
    按 session_id（即 会话id 去掉末尾 _t\\d+ 后缀）建立索引。
    返回 {session_id: conversation_text}。
    conversation_text 取该 session 最后一条 turn 的 "历史消息" 字段，
    其中已包含到当前轮的完整对话。
    """
    import re
    runs_dir = SKILL_ROOT / "data" / "runs"
    index: dict = {}
    if not runs_dir.exists():
        return index
    for final_file in runs_dir.glob("*/final.jsonl"):
        try:
            with open(final_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    raw_id = row.get("会话id", "")
                    # session_id = 去掉末尾 _t\d+
                    sid = re.sub(r"_t\d+$", "", raw_id)
                    if sid:
                        # 后出现的 turn 覆盖前一条（最后一条 turn 包含最完整的历史消息）
                        index[sid] = row.get("历史消息", "")
        except Exception:
            pass
    return index


def load_results(round_dir: Path) -> list:
    results_file = round_dir / "judge_results.jsonl"
    if not results_file.exists():
        raise FileNotFoundError(f"未找到 judge_results.jsonl: {results_file}\n请先运行 run_judge.py")
    rows = []
    with open(results_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ─── 统计逻辑 ──────────────────────────────────────────────────────────────────

def compute_stats(results: list, pass_threshold: float) -> dict:
    """计算所有统计指标。每个 result 已是 session 级整体评分。"""

    # 每个 result 就是一个 session
    session_scores = {}
    for r in results:
        sid = r.get("session_id", "unknown")
        norm = r.get("normalized_score", 0.0)
        final = r.get("final_score", round(1 + norm * 4, 1))
        session_scores[sid] = {
            "avg_score": round(norm, 3),
            "final_score": final,
            "passed": norm >= pass_threshold,
            "result": r,
            # v1.1 新字段（v1.0 结果中不存在时为 None）
            "score_action": r.get("score_action"),
            "score_H": r.get("score_H"),
            "score_I": r.get("score_I"),
            # v1.2 新字段
            "score_J": r.get("score_J"),
            "score_K": r.get("score_K"),
            "persona_id": r.get("persona_id", ""),
            "path_id": r.get("path_id", ""),
        }

    total_sessions = len(session_scores)
    passed_sessions = sum(1 for v in session_scores.values() if v["passed"])
    pass_rate = passed_sessions / total_sessions if total_sessions else 0.0

    # 维度通过率（归一化 >= 0.6 视为通过）
    dim_scores_all: dict[str, list] = defaultdict(list)
    for r in results:
        for dim, detail in r.get("dimension_scores", {}).items():
            if isinstance(detail, dict):
                s = detail.get("score", 0)
                mx = detail.get("max_score", 1)
                norm = s / mx if mx else 0
            else:
                norm = float(detail)
            dim_scores_all[dim].append(norm)

    dim_pass_rates = {}
    for dim, scores in dim_scores_all.items():
        avg = sum(scores) / len(scores) if scores else 0.0
        passed = sum(1 for s in scores if s >= 0.6)
        dim_pass_rates[dim] = {
            "avg": round(avg, 3),
            "pass_rate": round(passed / len(scores), 3) if scores else 0.0,
            "count": len(scores),
        }

    # fail_tags TOP5
    fail_tag_counter: Counter = Counter()
    for r in results:
        for dim, detail in r.get("dimension_scores", {}).items():
            if isinstance(detail, dict):
                for tag in detail.get("fail_tags", []):
                    fail_tag_counter[tag] += 1

    fail_tags_top5 = fail_tag_counter.most_common(5)

    # action_tags 分布
    action_tag_counter: Counter = Counter()
    for r in results:
        for tag in r.get("action_tags", []):
            action_tag_counter[tag] += 1

    # 最差 sessions（分数最低的 5 个，用于人工决策参考）
    worst_sessions = sorted(
        session_scores.items(), key=lambda x: x[1]["avg_score"]
    )[:5]

    # v1.2: Persona 分组统计
    persona_stats = defaultdict(lambda: {"count": 0, "scores": [], "score_J": [], "score_K": [],
                                           "score_action": [], "score_H": [], "score_I": [],
                                           "passed": 0})
    for sid, info in session_scores.items():
        pid = info.get("persona_id", "")
        if not pid:
            pid = "unknown"
        ps = persona_stats[pid]
        ps["count"] += 1
        ps["scores"].append(info["avg_score"])
        if info.get("passed"):
            ps["passed"] += 1
        for dim_key in ("score_action", "score_H", "score_I", "score_J", "score_K"):
            val = info.get(dim_key)
            if val is not None:
                ps[dim_key].append(val)

    # 计算每个 persona 的汇总
    persona_summary = {}
    for pid, ps in sorted(persona_stats.items()):
        avg_score = round(sum(ps["scores"]) / len(ps["scores"]), 3) if ps["scores"] else 0.0
        pass_rate_p = round(ps["passed"] / ps["count"], 3) if ps["count"] else 0.0
        dim_avgs = {}
        for dim_key in ("score_action", "score_H", "score_I", "score_J", "score_K"):
            vals = ps[dim_key]
            dim_avgs[dim_key] = round(sum(vals) / len(vals), 3) if vals else None
        persona_summary[pid] = {
            "count": ps["count"],
            "avg_score": avg_score,
            "final_score": round(1 + avg_score * 4, 2),
            "pass_rate": pass_rate_p,
            "dim_avgs": dim_avgs,
        }

    return {
        "total_sessions": total_sessions,
        "passed_sessions": passed_sessions,
        "pass_rate": pass_rate,
        "overall_avg_score": round(
            sum(v["avg_score"] for v in session_scores.values()) / total_sessions, 3
        ) if total_sessions else 0.0,
        "dim_pass_rates": dim_pass_rates,
        "fail_tags_top5": fail_tags_top5,
        "action_tag_dist": dict(action_tag_counter.most_common()),
        "worst_sessions": worst_sessions,
        "session_scores": session_scores,
        "pass_threshold": pass_threshold,
        "persona_summary": persona_summary,
    }


# ─── 报告生成辅助 ───────────────────────────────────────────────────────────────

def render_evidence_section(results: list, pass_threshold: float,
                            sessions_index: dict = None) -> str:
    """
    渲染"低分问题清单（附 reason 原文证据 + 对话原文）"章节。
    只列出 normalized_score < pass_threshold 的 session，每条失分子维度输出：
      session_id | dimension | fail_tags | reason
    并在表格后附上对话原文折叠块，供人工 review 对照原文判断 LLM 打分是否准确。
    目的：为后续 analyze_root_cause.py 的根因分析提供结构化原料。
    """
    lines = [
        "## 七、低分问题清单（附 reason 原文证据 + 对话原文）",
        "",
        "> 以下仅列出未通过阈值的 session 中的失分子维度。",
        "> 此清单作为根因分析的原料，可直接传给 `analyze_root_cause.py` 或 Agent 流程 A 使用。",
        "> **对话原文**已附于每个 session 表格下方（折叠块），供人工 review 对照 LLM 打分结论。",
        "",
    ]

    low_score_rows = []
    for r in results:
        sid = r.get("session_id", "unknown")
        norm = r.get("normalized_score", 0.0)
        if norm >= pass_threshold:
            continue

        dim_scores = r.get("dimension_scores", {})
        for dim_key, detail in dim_scores.items():
            if not isinstance(detail, dict):
                continue
            score = detail.get("score", 0)
            max_score = detail.get("max_score", 1)
            if max_score == 0 or score >= max_score:
                continue
            fail_tags = detail.get("fail_tags", [])
            reason = detail.get("reason", "")
            low_score_rows.append({
                "session_id": sid,
                "final_score": r.get("final_score", round(1 + norm * 4, 1)),
                "dimension": dim_key,
                "score": score,
                "max_score": max_score,
                "fail_tags": fail_tags,
                "reason": reason,
            })

    if not low_score_rows:
        lines.append("（无低分 session，无问题证据需列出）")
        return "\n".join(lines)

    # 按 session 分组输出
    from itertools import groupby
    low_score_rows.sort(key=lambda x: (x["session_id"], x["dimension"]))
    for sid, group_rows in groupby(low_score_rows, key=lambda x: x["session_id"]):
        rows = list(group_rows)
        final_score = rows[0]["final_score"]
        lines.append(f"### {sid}（最终得分：{final_score}）")
        lines.append("")
        lines.append("| 子维度 | 得分 | 满分 | Fail Tags | 原文证据（reason） |")
        lines.append("|---|---|---|---|---|")
        for row in rows:
            tags_str = "、".join(row["fail_tags"]) if row["fail_tags"] else "—"
            reason_str = row["reason"].replace("|", "｜")  # 防止 Markdown 表格破坏
            lines.append(
                f"| {row['dimension']} | {row['score']} | {row['max_score']} "
                f"| {tags_str} | {reason_str} |"
            )
        lines.append("")

        # ── 对话原文折叠块（供人工 review）──
        conv_text = ""
        if sessions_index is not None:
            conv_text = sessions_index.get(sid, "")
        if conv_text:
            lines.append("<details>")
            lines.append("<summary>📄 对话原文（点击展开，供人工 review）</summary>")
            lines.append("")
            lines.append("```")
            lines.append(conv_text.rstrip())
            lines.append("```")
            lines.append("")
            lines.append("</details>")
        else:
            lines.append("> ⚠️ 对话原文未找到（请检查 `data/runs/` 目录下是否存在对应的 `final.jsonl`）")
        lines.append("")

    return "\n".join(lines)


# ─── 报告生成 ──────────────────────────────────────────────────────────────────

def render_report(stats: dict, round_name: str, judge_version: str,
                  results: list = None, sessions_index: dict = None) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    pass_threshold = stats["pass_threshold"]
    pass_pct = f"{stats['pass_rate'] * 100:.1f}%"
    overall_final = round(1 + stats["overall_avg_score"] * 4, 2)

    # ── v1.1/v1.2 维度均值（仅在数据中存在时展示）
    session_scores = stats["session_scores"]
    score_action_vals = [v["score_action"] for v in session_scores.values() if v.get("score_action") is not None]
    score_H_vals      = [v["score_H"]      for v in session_scores.values() if v.get("score_H")      is not None]
    score_I_vals      = [v["score_I"]      for v in session_scores.values() if v.get("score_I")      is not None]
    score_J_vals      = [v["score_J"]      for v in session_scores.values() if v.get("score_J")      is not None]
    score_K_vals      = [v["score_K"]      for v in session_scores.values() if v.get("score_K")      is not None]
    has_v11 = bool(score_H_vals)
    has_v12 = bool(score_J_vals)

    lines = [
        f"# 质检报告 — {round_name}",
        f"",
        f"> 生成时间：{now}  |  Judge 版本：{judge_version}  |  通过阈值：{pass_threshold}（归一化）",
        f"",
        f"---",
        f"",
        f"## 一、总览",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 评估 session 数 | {stats['total_sessions']} |",
        f"| 整体平均得分（1-5） | **{overall_final}** |",
        f"| Session 通过率 | **{pass_pct}** （{stats['passed_sessions']}/{stats['total_sessions']}） |",
    ]

    if has_v11:
        avg_action = round(sum(score_action_vals) / len(score_action_vals), 3) if score_action_vals else "-"
        avg_H      = round(sum(score_H_vals)      / len(score_H_vals),      3) if score_H_vals      else "-"
        avg_I      = round(sum(score_I_vals)       / len(score_I_vals),       3) if score_I_vals      else "-"
        lines += [
            f"| 动作维度均值（A-G，归一化） | {avg_action} |",
            f"| 话术策略均值（H，归一化）  | {avg_H} |",
            f"| 口语自然度均值（I，归一化） | {avg_I} |",
        ]

    if has_v12:
        avg_J = round(sum(score_J_vals) / len(score_J_vals), 3) if score_J_vals else "-"
        avg_K = round(sum(score_K_vals) / len(score_K_vals), 3) if score_K_vals else "-"
        lines += [
            f"| Persona 忠诚度均值（J，归一化） | {avg_J} |",
            f"| 骨架合规度均值（K，归一化）    | {avg_K} |",
        ]

    lines += [
        f"",
        f"**快速结论：**",
    ]

    # 快速结论
    if stats["pass_rate"] >= 0.8:
        lines.append(f"✅ 通过率 {pass_pct}，质量良好，可继续生产。")
    elif stats["pass_rate"] >= 0.6:
        lines.append(f"⚠️ 通过率 {pass_pct}，有改进空间，建议针对低分维度优化 Rubric/Prompt。")
    else:
        lines.append(f"❌ 通过率 {pass_pct}，质量偏低，建议暂停生产，先排查低分原因。")

    lines += [
        f"",
        f"---",
        f"",
        f"## 二、各维度通过率",
        f"",
        f"| 维度 | 平均分（归一化） | 通过率 | 样本数 |",
        f"|---|---|---|---|",
    ]

    # 按通过率升序排列（低的在前，方便看问题）
    sorted_dims = sorted(
        stats["dim_pass_rates"].items(), key=lambda x: x[1]["pass_rate"]
    )
    for dim, info in sorted_dims:
        flag = "🔴" if info["pass_rate"] < 0.6 else ("🟡" if info["pass_rate"] < 0.8 else "🟢")
        lines.append(
            f"| {flag} {dim} | {info['avg']:.3f} | {info['pass_rate']*100:.1f}% | {info['count']} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 三、Fail Tags TOP5",
        f"",
    ]

    if stats["fail_tags_top5"]:
        lines += [
            f"| # | Fail Tag | 出现次数 |",
            f"|---|---|---|",
        ]
        for i, (tag, cnt) in enumerate(stats["fail_tags_top5"], 1):
            lines.append(f"| {i} | {tag} | {cnt} |")
    else:
        lines.append("（无 fail_tags 数据）")

    lines += [
        f"",
        f"---",
        f"",
        f"## 四、动作标签分布",
        f"",
        f"| 动作标签 | 出现次数 |",
        f"|---|---|",
    ]
    for tag, cnt in stats["action_tag_dist"].items():
        lines.append(f"| {tag} | {cnt} |")

    lines += [
        f"",
        f"---",
        f"",
        f"## 五、最差 Sessions（待人工复核）",
        f"",
        f"| Session ID | 平均分（归一化） | 最终得分（1-5） |",
        f"|---|---|---|",
    ]
    for sid, info in stats["worst_sessions"]:
        lines.append(
            f"| {sid} | {info['avg_score']} | {info['final_score']} |"
        )

    # ── v1.2: Persona 分组统计 ──
    persona_summary = stats.get("persona_summary", {})
    if persona_summary and any(pid != "unknown" for pid in persona_summary):
        lines += [
            f"",
            f"---",
            f"",
            f"## Persona 分组统计",
            f"",
            f"> 按 Persona 类型分组展示各维度平均得分，帮助定位特定人设下的生产质量问题。",
            f"",
        ]

        # 动态决定表头：是否包含 J/K
        if has_v12:
            lines += [
                f"| Persona | Sessions | 均分(1-5) | 通过率 | A-G | H | I | J | K |",
                f"|---|---|---|---|---|---|---|---|---|",
            ]
            for pid, ps in sorted(persona_summary.items()):
                if pid == "unknown":
                    continue
                da = ps["dim_avgs"]
                lines.append(
                    f"| {pid} | {ps['count']} | {ps['final_score']} | {ps['pass_rate']*100:.1f}% "
                    f"| {da.get('score_action', '-') or '-'} | {da.get('score_H', '-') or '-'} "
                    f"| {da.get('score_I', '-') or '-'} | {da.get('score_J', '-') or '-'} "
                    f"| {da.get('score_K', '-') or '-'} |"
                )
        else:
            lines += [
                f"| Persona | Sessions | 均分(1-5) | 通过率 | A-G | H | I |",
                f"|---|---|---|---|---|---|---|",
            ]
            for pid, ps in sorted(persona_summary.items()):
                if pid == "unknown":
                    continue
                da = ps["dim_avgs"]
                lines.append(
                    f"| {pid} | {ps['count']} | {ps['final_score']} | {ps['pass_rate']*100:.1f}% "
                    f"| {da.get('score_action', '-') or '-'} | {da.get('score_H', '-') or '-'} "
                    f"| {da.get('score_I', '-') or '-'} |"
                )

        # 如果有 unknown persona，单独注释
        if "unknown" in persona_summary:
            uk = persona_summary["unknown"]
            lines += [
                f"",
                f"> ⚠️ {uk['count']} 个 session 未能解析 Persona（session_id 格式不匹配），均分 {uk['final_score']}",
            ]

    lines += [
        f"",
        f"---",
        f"",
        f"## 六、待人工决策",
        f"",
        f"- [ ] **整体质量判断**：通过 / 不通过 / 部分通过",
        f"- [ ] **低分维度归因**：是 Rubric 标准问题？还是 Judge Prompt 问题？还是生产数据本身问题？",
        f"- [ ] **是否启动根因分析**：运行 `analyze_root_cause.py --round {round_name}` 生成分析 Prompt",
        f"- [ ] **是否需要人工标注**：从最差 sessions 中挑样本，走流程 B？",
        f"",
        f"---",
        f"",
    ]

    # ── 低分问题清单（附 reason 原文证据 + 对话原文）── v1.1 新增
    if results is not None:
        evidence_section = render_evidence_section(results, stats["pass_threshold"],
                                                   sessions_index=sessions_index)
        lines.append(evidence_section)
        lines += [
            f"",
            f"---",
            f"",
        ]

    lines.append(f"*由 gen_report.py 自动生成*")

    return "\n".join(lines)


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-judge 生成质检报告")
    parser.add_argument("--round", default="tuning_round_1", help="质检轮次名（默认 tuning_round_1）")
    parser.add_argument("--pass-threshold", type=float, default=0.6,
                        help="session 通过归一化分数线（默认 0.6）")
    args = parser.parse_args()

    round_dir = SKILL_ROOT / "data" / "judge_runs" / args.round
    if not round_dir.exists():
        print(f"❌ 轮次目录不存在: {round_dir}")
        return

    # 读取 judge_version
    progress_file = round_dir / "judge_progress.json"
    judge_version = "v1.0"
    if progress_file.exists():
        with open(progress_file, encoding="utf-8") as f:
            progress = json.load(f)
        judge_version = progress.get("judge_version", "v1.0")

    print(f"📂 加载打分结果: {round_dir / 'judge_results.jsonl'}")
    results = load_results(round_dir)
    print(f"   共 {len(results)} 条 session 结果")

    print(f"📊 计算统计...")
    stats = compute_stats(results, args.pass_threshold)

    print(f"📂 加载对话原文索引（用于低分 session 原文附录）...")
    sessions_index = load_sessions_index()
    print(f"   共索引 {len(sessions_index)} 个 session")

    print(f"📝 生成报告...")
    report_md = render_report(stats, args.round, judge_version,
                              results=results, sessions_index=sessions_index)

    report_file = round_dir / "report.md"
    report_file.write_text(report_md, encoding="utf-8")

    abs_path = report_file.resolve()
    print(f"\n✅ 报告已生成")
    print(f"   绝对路径：{abs_path}")
    print(f"\n--- 摘要 ---")
    print(f"  Sessions: {stats['total_sessions']}  通过: {stats['passed_sessions']}  通过率: {stats['pass_rate']*100:.1f}%")
    print(f"  整体均分（1-5）: {round(1 + stats['overall_avg_score'] * 4, 2)}")
    if stats["fail_tags_top5"]:
        print(f"  Top Fail Tag: {stats['fail_tags_top5'][0][0]} ({stats['fail_tags_top5'][0][1]}次)")
    print(f"\n下一步：查看报告并做人工决策")
    print(f"   {abs_path}")


if __name__ == "__main__":
    main()
