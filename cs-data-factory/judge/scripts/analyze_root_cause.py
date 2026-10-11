#!/usr/bin/env python3
"""
analyze_root_cause.py
---------------------
Phase 3 辅助步骤：根因分析 Prompt 生成器。

读取：
  - data/judge_runs/<round>/judge_results.jsonl（打分结果，含 reason 原文证据）
  - data/runs/<batch>/subagent_prompts/<task_id>.prompt.txt（低分 session 对应的生产 prompt）
  - data/judge_runs/<round>/report.md（质检报告，含低分问题清单）

生成：
  - data/judge_runs/<round>/root_cause_prompt.txt（根因分析 Prompt，交给主 Agent 执行）

主 Agent 执行分析后写入：
  - data/judge_runs/<round>/root_cause_analysis.md（根因分析结果 + 最小优化方案草案）

用法:
  python judge/scripts/analyze_root_cause.py \\
      --round tuning_round_1 \\
      --batch data/runs/run_YYYYMMDD_001 \\
      [--pass-threshold 0.6] \\
      [--top-n 8]  # 最多取几条低分 session 进行根因分析（默认 8）

注意：
  --batch 指定生产 run 目录（相对 cs-data-factory/）。脚本会将 judge_results 中的 session_id
  反解析为 task_id（格式：gen_{{task_id}}_s\\d+_t\\d+），再到生产 run 中读取对应的 prompt 文件。
  如果不传 --batch，会设法读取到生产 prompt（证据段将显示为不可用）。

根因分类（多标签，5类）:
  - skeleton        : 骨架问题（路径节点定义/检查点/话术示例本身）
  - prompt_template : Prompt 模板问题（expand-prompt.md 约束/规则/示例不清晰）
  - signal          : 信号问题（mock 规则偏差导致场景不真实）
  - persona         : Persona 问题（描述不准确/覆盖场景缺失）
  - general_guideline: 通用规范问题（speech-guidelines.md 话术规范描述不足）
"""

import io
import sys
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import json
import argparse
from pathlib import Path
from collections import defaultdict

# ─── 路径 ──────────────────────────────────────────────────────────────────────
JUDGE_ROOT = Path(__file__).parent.parent   # cs-data-factory/judge/
SKILL_ROOT = JUDGE_ROOT.parent              # cs-data-factory/


# ─── 根因分析 Prompt 模板 ───────────────────────────────────────────────────────

ROOT_CAUSE_PROMPT_TEMPLATE = """\
你是一名专业的 AI 数据生产质量分析师。你的任务是基于以下质检报告和低分对话证据，\
对问题进行根因分类，并输出最小化优化方案草案，供用户审核后决策是否实施。

---

## 一、背景：本轮质检概要

{report_summary}

---

## 二、低分 Session 失分证据

以下是本轮未通过阈值的 session 中各子维度的失分 reason（LLM 评分时写的原文说明）：

{evidence_table}

---

## 三、低分 Session 对应的生产 Prompt（骨架 + 规则上下文）

以下是各低分 session 在生产时使用的 prompt 摘要（含骨架检查点、信号规则块、persona 块）。\
这是判断问题根源在"生产侧配置"还是"规范侧指令"的关键依据：

{prompt_excerpts}

---

## 四、你的分析任务

### Step 1：对每条失分子维度，判断根因类别（可多选）

根因类别定义：
| 类别 | 含义 |
|---|---|
| skeleton | 骨架问题：路径节点定义/检查点/话术示例本身有问题（改 graph.json 或 sop 骨架） |
| prompt_template | Prompt 模板问题：expand-prompt.md 的约束/规则/示例表达不清晰或缺失 |
| signal | 信号问题：signal_rules.json 的 mock 规则偏差，导致 LLM 生成场景不真实 |
| persona | Persona 问题：persona.md 描述不准确，或缺少覆盖该情绪/场景的 Persona 类型 |
| general_guideline | 通用规范问题：speech-guidelines.md 的话术规范表述不足，或某类问题未覆盖 |

### Step 2：合并同类根因，输出问题摘要

将归类一致的失分点合并，说明：
- 这类问题的共性表现（用 1-2 句话描述）
- 在几个 session 中出现（出现频次越高，优先级越高）
- 判断这是"系统性问题"（多个 session 都有）还是"偶发问题"（1 个 session）

### Step 3：生成最小优化方案草案

只针对"系统性问题"（出现 ≥ 2 个 session）生成优化方案，偶发问题暂不处理。

每条优化方案包含：
- **优化目标**：解决什么问题
- **修改文件**：具体是哪个文件（如 `references/expand-prompt.md` 某段话 / `data/sop/{{sop_id}}_signal_rules.json` 某字段 / `references/persona.md` 某 Persona）
- **修改建议**：具体怎么改（不需要给完整文本，说清楚方向即可）
- **根因类别**：对应的根因标签（多选）
- **优先级**：高（影响所有 session）/ 中（影响 >30% session）/ 低（影响少数 session）

### Step 4：输出结构化结果

请按以下 Markdown 格式输出分析结果：

---

## 根因分析结果

### 问题分类汇总

| 根因类别 | 出现次数（子维度级） | 涉及 Session 数 | 系统性/偶发 |
|---|---|---|---|
| ... | ... | ... | ... |

### 各问题详情

#### [问题编号]：[问题简述]
- **共性表现**：...
- **涉及 session**：...（n 个）
- **失分子维度**：...
- **根因类别**：...（多标签）
- **是否系统性**：是/否

---

## 最小优化方案草案

> 只列出系统性问题（出现 ≥ 2 个 session）的优化建议。

### 优化项 [编号]

| 字段 | 内容 |
|---|---|
| 优化目标 | ... |
| 修改文件 | ... |
| 修改建议 | ... |
| 根因类别 | ... |
| 优先级 | 高/中/低 |
| 验证方式 | 取低分 case 重跑，对比前后得分和话术 |

---

## 用户决策项

- [ ] 确认上述根因分析是否准确（如有偏差，请指出具体哪条）
- [ ] 选择要实施的优化项（可全选 / 部分选 / 都不选）
- [ ] 确认后运行流程 D 进行验证重跑

"""


# ─── 辅助函数 ──────────────────────────────────────────────────────────────────

def load_results(round_dir: Path) -> list:
    results_file = round_dir / "judge_results.jsonl"
    if not results_file.exists():
        raise FileNotFoundError(f"未找到 judge_results.jsonl: {results_file}")
    rows = []
    with open(results_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def get_low_score_sessions(results: list, pass_threshold: float, top_n: int) -> list:
    """返回最差的 top_n 个 session（normalized_score 升序）。"""
    low = [(r.get("session_id", "?"), r.get("normalized_score", 0.0), r)
           for r in results
           if r.get("normalized_score", 0.0) < pass_threshold]
    low.sort(key=lambda x: x[1])
    return low[:top_n]


def build_evidence_table(low_sessions: list) -> str:
    """将低分 session 的失分子维度整理为文本表格。"""
    lines = ["| Session ID | 得分(归一化) | 子维度 | Fail Tags | Reason（原文证据） |",
             "|---|---|---|---|---|"]
    for sid, norm, r in low_sessions:
        dim_scores = r.get("dimension_scores", {})
        score_str = f"{norm:.3f}"
        for dim_key, detail in sorted(dim_scores.items()):
            if not isinstance(detail, dict):
                continue
            score = detail.get("score", 0)
            max_score = detail.get("max_score", 1)
            if max_score == 0 or score >= max_score:
                continue
            fail_tags = "、".join(detail.get("fail_tags", [])) or "—"
            reason = detail.get("reason", "").replace("|", "｜")
            lines.append(f"| {sid} | {score_str} | {dim_key} | {fail_tags} | {reason} |")
    return "\n".join(lines)


def extract_prompt_excerpt(prompt_path: Path, max_chars: int = 800) -> str:
    """
    从生产 prompt 文件中提取关键段落：检查点骨架 + 信号规则块 + Persona 块。
    如果文件不存在则返回提示。
    """
    if not prompt_path.exists():
        return f"（prompt 文件不存在：{prompt_path}）"

    text = prompt_path.read_text(encoding="utf-8")

    # 按关键词段落截取（优先提取骨架、信号、persona 三个块）
    excerpts = []
    for marker in ["## 路径骨架", "## 信号 Mock 规则", "## Persona", "当前场景信号"]:
        idx = text.find(marker)
        if idx != -1:
            chunk = text[idx: idx + 400].strip()
            excerpts.append(f"[{marker}...]\n{chunk}\n...")

    if excerpts:
        combined = "\n\n".join(excerpts)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n...（已截断）"
        return combined

    # fallback：取前 max_chars 字符
    return text[:max_chars] + "\n...（已截断）"


def session_id_to_task_id(session_id: str) -> str:
    """
    将 session_id 反解析为 task_id。
    session_id 格式： gen_{task_id}_s{seq}_t{turn}
    task_id 格式： {path_id}_{persona_id}_{batch}

    注意：此函数与 pick_low_score_tasks.py 中的同名函数实现完全相同。
    如需修改逻辑，两处需同步更新。
    """
    import re
    m = re.match(r"gen_(.+)_s\d+_t\d+$", session_id)
    if m:
        return m.group(1)
    # 备用：无法解析时直接返回 session_id
    return session_id


def build_prompt_excerpts(low_sessions: list, production_prompts_dir: Path | None) -> str:
    """
    为每条低分 session 提取生产 prompt 摘要。
    production_prompts_dir: 生产 run 的 subagent_prompts/ 目录（可为 None 表示不可用）
    """
    lines = []
    for sid, norm, r in low_sessions:
        if production_prompts_dir is None:
            excerpt = "(未指定生产 run 目录，无法读取生产 prompt。请使用 --batch 参数指定对应的生产 run 目录)"
        else:
            task_id = session_id_to_task_id(sid)
            prompt_file = production_prompts_dir / f"{task_id}.prompt.txt"
            excerpt = extract_prompt_excerpt(prompt_file)
        lines.append(f"### Session: {sid}（得分 {norm:.3f}）\n\n{excerpt}\n")
    return "\n---\n".join(lines)


def build_report_summary(round_dir: Path) -> str:
    """从 report.md 中提取总览段落（§一）作为背景。"""
    report_file = round_dir / "report.md"
    if not report_file.exists():
        return "（report.md 不存在，请先运行 gen_report.py）"

    text = report_file.read_text(encoding="utf-8")
    # 提取"## 一、总览"到"## 二"之间的内容
    start = text.find("## 一、总览")
    end = text.find("## 二、")
    if start != -1 and end != -1:
        return text[start:end].strip()
    return text[:600].strip() + "\n...（已截断）"


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-judge 根因分析 Prompt 生成器")
    parser.add_argument("--round", default="tuning_round_1", help="质检轮次名")
    parser.add_argument("--batch", default="",
                        help="生产 run 目录（相对 cs-data-factory/，如 data/runs/run_20260414_x1）。"
                             "用于定位低分 session 对应的生产 prompt 文件（选填，不填则证据段显示为不可用）")
    parser.add_argument("--pass-threshold", type=float, default=0.6,
                        help="session 通过归一化分数线（默认 0.6）")
    parser.add_argument("--top-n", type=int, default=8,
                        help="取分数最低的 N 个 session 进行根因分析（默认 8）")
    args = parser.parse_args()

    round_dir = SKILL_ROOT / "data" / "judge_runs" / args.round

    # ── 确定生产 prompt 目录 ──
    if args.batch:
        production_prompts_dir = SKILL_ROOT / args.batch / "subagent_prompts"
        if not production_prompts_dir.exists():
            print(f"⚠️  生产 prompts 目录不存在：{production_prompts_dir}")
            print(f"   将跳过生产 prompt 摘要段，证据质量会降低。")
            production_prompts_dir = None
    else:
        production_prompts_dir = None
        print(f"⚠️  未指定 --batch，将无法读取生产 prompt（证据段会为空）")
        print(f"   建议指定 --batch data/runs/<run_id> 以获得更准确的根因分析材料")

    if not round_dir.exists():
        print(f"❌ 轮次目录不存在: {round_dir}")
        return

    print(f"📂 加载打分结果...")
    results = load_results(round_dir)
    print(f"   共 {len(results)} 条 session 结果")

    print(f"🔍 筛选低分 session（阈值 {args.pass_threshold}，取最差 {args.top_n} 条）...")
    low_sessions = get_low_score_sessions(results, args.pass_threshold, args.top_n)
    if not low_sessions:
        print(f"✅ 无低分 session（全部通过阈值），无需根因分析")
        return
    print(f"   低分 session 数：{len(low_sessions)}")

    print(f"📝 整理失分证据...")
    evidence_table = build_evidence_table(low_sessions)

    print(f"📄 提取生产 prompt 摘要...")
    prompt_excerpts = build_prompt_excerpts(low_sessions, production_prompts_dir)

    print(f"📋 提取报告总览...")
    report_summary = build_report_summary(round_dir)

    print(f"🔧 生成根因分析 Prompt...")
    root_cause_prompt = ROOT_CAUSE_PROMPT_TEMPLATE.format(
        report_summary=report_summary,
        evidence_table=evidence_table,
        prompt_excerpts=prompt_excerpts,
    )

    out_file = round_dir / "root_cause_prompt.txt"
    out_file.write_text(root_cause_prompt, encoding="utf-8")

    print(f"\n✅ 根因分析 Prompt 已生成: {out_file}")
    print(f"\n下一步（流程 A）：")
    print(f"  主 Agent 读取 {out_file}")
    print(f"  执行分析 → 写入 {round_dir / 'root_cause_analysis.md'}")
    print(f"  用户审核后决策是否实施优化，通过后运行流程 D 验证重跑")
    print(f"\n  快捷入口：告知 Agent「分析 {args.round} 的根因」")


if __name__ == "__main__":
    main()
