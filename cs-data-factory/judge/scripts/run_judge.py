#!/usr/bin/env python3
"""
run_judge.py
------------
Phase 3 第一步：从生产批次中抽样，执行质检打分。
评分粒度：session 维度（多 session 合并为批次文件，每批默认 5 个 session）。

【模式 A：SubAgent 模式（推荐）】
  python judge/scripts/run_judge.py \\
      --batch data/runs/run_20260325_001 \\
      --round tuning_round_1 \\
      --export-prompts          # 只生成 prompt 文件，不调 LLM

  # 然后由 Agent 批量 spawn SubAgent 执行打分（每个 SubAgent 处理一个批次文件 batch_XXX.prompt.txt）
  # SubAgent 完成后结果写入 subagent_prompts/batch_XXX.result.txt（JSON 数组格式）
  # 最后汇总：
  python judge/scripts/collect_subagent_results.py --round tuning_round_1

【模式 B：API 模式（指定模型评分）】
  python judge/scripts/run_judge.py \\
      --batch data/runs/run_20260325_001 \\
      --round tuning_round_1 \\
      --appid <模型 API Key> \\
      --model your-model

  # 结果直接写入 judge_results.jsonl，无需 collect_subagent_results.py

⚠️  --export-prompts 和 --appid 互斥，同一轮次只能选其一。

选项:
  --batch           生产 run 目录（相对于 cs-data-factory 根目录），如 data/runs/run_xxx
  --file            直接指定数据文件路径（与 --batch 二选一）
  --n               抽样数量（默认 20，-1 表示全量，单位为 session）
  --round           本次质检轮次名（默认 tuning_round_1）
  --judge-version   Judge Prompt 版本（默认 v1.0）
  --workers         并发线程数（默认 4，仅 API 模式有效）
  --export-prompts  SubAgent 模式：只生成 prompt 文件到 subagent_prompts/（推荐）
  --appid           API 模式：模型 API Key（与 --export-prompts 互斥）
  --model           API 模式：模型名（默认 your-model，与 --appid 搭配使用）
  --dry-run         打印前 3 条打分 prompt，不调用 API
"""

import io
import sys
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import json
import os
import re
import sys
import random
import argparse
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── 路径 ──────────────────────────────────────────────────────────────────────
JUDGE_ROOT = Path(__file__).parent.parent          # cs-data-factory/judge/
SKILL_ROOT = JUDGE_ROOT.parent                     # cs-data-factory 根目录

# lib/api_client 供 API 模式使用
sys.path.insert(0, str(SKILL_ROOT))
from lib.api_client import call_api as _call_api_impl


# ─── LLM 调用（API 模式）──────────────────────────────────────────────────────
# SubAgent 模式（推荐）不走此函数；API 模式（--appid 参数）时由此函数调用。
# 调用实现统一在 lib/api_client.py，此处仅做薄包装。

# 模块级变量：由 main() 在 API 模式下初始化
_api_appid: str = ""
_api_model: str = ""
_api_temperature: float = 0.0

# v1.2: Persona/Skeleton 数据（由 main() 初始化，run_task 引用）
_persona_defs: dict = {}
_path_skeletons: dict = {}


def call_llm(prompt: str, task_id: str, temperature: float = 0.0) -> str:
    """调用 兼容 Chat Completions 的模型 API 执行打分（API 模式下使用）。"""
    if not _api_appid:
        raise RuntimeError(
            "API 未配置。请使用 --appid 参数指定 AppId，或改用 --export-prompts（SubAgent 模式）。"
        )
    try:
        return _call_api_impl(
            prompt=prompt,
            appid=_api_appid,
            model=_api_model,
            temperature=temperature,
            max_tokens=2048,
        )
    except Exception as e:
        raise RuntimeError(f"任务 {task_id} API 调用失败: {e}")


# ─── Session Meta 解析 ──────────────────────────────────────────────────────────

def parse_session_meta(session_id: str) -> dict:
    """
    从 session_id 中解析 persona_id 和 path_id。
    session_id 格式: gen_{path_id}_{persona_id}_{batch}_s{seq}_t{turn}
    或 session 级别（已去掉 _t{turn}）: gen_{path_id}_{persona_id}_{batch}_s{seq}

    返回: {"persona_id": "P7", "path_id": "path001"}
    如果解析失败，返回空字符串。
    """
    # 尝试匹配 gen_{path_id}_{persona_id}_{batch}_s{seq}
    m = re.match(r"gen_(.+?)_(P\d+)_", session_id)
    if m:
        return {"path_id": m.group(1), "persona_id": m.group(2)}
    return {"path_id": "", "persona_id": ""}


# ─── Persona / Skeleton 数据加载 ─────────────────────────────────────────────

def load_persona_definitions(persona_path: Path = None) -> dict:
    """
    从 persona.md 加载各 Persona 定义段落。
    返回: {"P1": "### P1：急迫催促型\n...", "P7": "### P7：顺从配合型\n...", ...}

    查找优先级:
      1. persona_path 参数（如指定）
      2. SKILL_ROOT / "references" / "persona.md"

    如果文件不存在或解析失败，返回空 dict（降级为 J 维度全满分）。
    """
    if persona_path is None:
        persona_path = SKILL_ROOT / "references" / "persona.md"
    if not persona_path.exists():
        return {}

    text = persona_path.read_text(encoding="utf-8")
    personas = {}
    current_id = None
    current_lines = []

    for line in text.splitlines():
        # 匹配 ### PX：类型名 或 ### PX: 类型名
        m = re.match(r"^###\s+(P\d+)[：:]", line)
        if m:
            # 保存上一个 persona
            if current_id:
                personas[current_id] = "\n".join(current_lines).strip()
            current_id = m.group(1)
            current_lines = [line]
        elif current_id:
            current_lines.append(line)

    # 保存最后一个
    if current_id:
        personas[current_id] = "\n".join(current_lines).strip()

    return personas


def load_path_skeletons(sop_id: str = None) -> dict:
    """
    从 paths.jsonl 加载各路径骨架。
    返回: {"path001": {"path_id": "path001", "sop_id": ..., "scene": ..., ...}, ...}

    查找优先级:
      1. SKILL_ROOT / "data" / "paths" 下所有 *_paths.jsonl
      2. 如果指定 sop_id，只加载 {sop_id}_paths.jsonl

    如果文件不存在或解析失败，返回空 dict（降级为 K 维度全满分）。
    """
    paths_dir = SKILL_ROOT / "data" / "paths"
    if not paths_dir.exists():
        return {}

    skeletons = {}
    if sop_id:
        files = [paths_dir / f"{sop_id}_paths.jsonl"]
    else:
        files = list(paths_dir.glob("*_paths.jsonl"))

    for f in files:
        if not f.exists():
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    pid = obj.get("path_id", "")
                    if pid:
                        skeletons[pid] = obj
                except json.JSONDecodeError:
                    continue
    return skeletons


# ─── Judge Prompt 加载 ──────────────────────────────────────────────────────────

def _extract_prompt_block(md_text: str) -> str:
    """
    从 .md 文件中提取 ## Prompt 模板 下 ``` 包裹的核心 prompt 文本。
    只返回核心 prompt，不含文档 header / 使用说明等元信息。
    这样 export-prompts 生成的 .prompt.txt 是纯净可直接执行的 prompt。
    """
    lines = md_text.splitlines()
    start = None
    end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "```" and start is None:
            start = i
        elif stripped == "```" and start is not None:
            end = i
            break
    if start is None or end is None:
        raise ValueError("judge_prompt .md 文件中未找到 ``` 包裹的 Prompt 模板块")
    return "\n".join(lines[start + 1:end]).strip()


def load_judge_prompt(version: str) -> str:
    """加载 judge prompt 模板，只返回核心 prompt 块（去除 .md 文档元信息）。"""
    prompt_file = JUDGE_ROOT / "prompts" / f"judge_prompt_{version}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Judge Prompt 文件不存在: {prompt_file}")
    md_text = prompt_file.read_text(encoding="utf-8")
    return _extract_prompt_block(md_text)


def build_judge_input(session: dict, judge_prompt_template: str,
                      persona_defs: dict = None, path_skeletons: dict = None) -> str:
    """
    将 session 数据填入 Judge Prompt 核心模板。
    session 结构：
      {
        "session_id": "gen_P001_P1_001_s1",
        "turns": [...],           # final.jsonl 的 turns 字段
        "所有信号": "...",         # 取第一条 turn 的所有信号
      }
    模板中用 {{字段名}} 占位，依次替换。

    v1.2 新增：
      persona_defs: {"P1": "...", "P7": "..."} — 各 Persona 定义段落
      path_skeletons: {"path001": {...}} — 各路径骨架
      如果模板中包含 {{persona_definition}} / {{path_skeleton}} 占位符，
      则从 session_id 解析 persona_id / path_id 后查找并注入；
      找不到时注入空字符串（Judge Prompt 中约定：为空则该维度全满分）。
    """
    # 把 turns 格式化成可读文本放入模板
    turns_json = json.dumps(session.get("turns", []), ensure_ascii=False, indent=2)
    所有信号 = session.get("所有信号", "")

    prompt = judge_prompt_template
    prompt = prompt.replace("{{完整对话}}", turns_json)
    prompt = prompt.replace("{{所有信号}}", 所有信号)

    # v1.2: 注入 Persona 定义和路径骨架（向后兼容：模板无占位符则无操作）
    if "{{persona_definition}}" in prompt or "{{path_skeleton}}" in prompt:
        meta = parse_session_meta(session.get("session_id", ""))
        persona_id = meta["persona_id"]
        path_id = meta["path_id"]

        # 查找 persona 定义
        persona_text = ""
        if persona_defs and persona_id:
            persona_text = persona_defs.get(persona_id, "")
        if not persona_text:
            persona_text = "（未提供 Persona 定义，J 维度所有子项直接满分）"

        # 查找路径骨架
        skeleton_text = ""
        if path_skeletons and path_id:
            skeleton_obj = path_skeletons.get(path_id)
            if skeleton_obj:
                skeleton_text = json.dumps(skeleton_obj, ensure_ascii=False, indent=2)
        if not skeleton_text:
            skeleton_text = "（未提供路径骨架，K 维度所有子项直接满分）"

        prompt = prompt.replace("{{persona_definition}}", persona_text)
        prompt = prompt.replace("{{path_skeleton}}", skeleton_text)

    return prompt


def _build_batch_judge_prompt(session_ids: list, session_prompts: list) -> str:
    """
    将多个 session 的 Judge Prompt 合并为一个批次 Prompt。
    SubAgent 读取该文件后，对每个 session 评分，输出 JSON 数组。

    格式：
      [批次打分任务说明]
      === Session 1/N: <session_id> ===
      <单 session Judge Prompt>
      ...
      [输出格式要求]
    """
    n = len(session_ids)
    header = (
        f"# 批次质检打分任务（共 {n} 个 session）\n\n"
        f"你需要对以下 {n} 个 session 逐一进行质检评分。\n"
        f"每个 session 独立打分，最终输出一个 JSON 数组，数组中每个元素是一个 session 的评分结果。\n\n"
        f"**输出格式要求（严格遵守）**：\n"
        f"- 输出纯 JSON 数组，不含任何说明文字或 markdown 标记\n"
        f"- 数组长度必须恰好为 {n}，与输入 session 顺序一一对应\n"
        f"- 每个元素格式与单 session 评分格式相同（包含 session_id / dimension_scores 等字段）\n"
        f"- 每个元素必须包含 \"session_id\" 字段，值为对应 session 的 ID\n"
        f"- 字符串值中禁止包含未转义的双引号，避免 JSON 解析失败\n\n"
        f"{'='*60}\n\n"
    )

    parts = [header]
    for i, (sid, prompt) in enumerate(zip(session_ids, session_prompts)):
        parts.append(f"## Session {i + 1}/{n}: {sid}\n\n{prompt}\n\n{'='*60}\n\n")

    parts.append(
        "# 输出要求\n\n"
        "现在开始打分。输出一个 JSON 数组，包含上述所有 session 的评分结果，格式示例：\n"
        "[\n"
        "  {\"session_id\": \"<id_1>\", \"action_tags\": [...], \"emotion_level\": \"...\", "
        "\"dimension_scores\": {...}, \"normalized_score\": 0.0, \"final_score\": 0.0, \"summary\": \"...\"},\n"
        "  ...\n"
        "]\n\n"
        "直接输出 JSON 数组，不要包含其他文字。\n"
    )
    return "".join(parts)


# ─── 打分结果解析 ──────────────────────────────────────────────────────────────

def parse_judge_result(raw: str, session_id: str, judge_version: str) -> dict:
    """
    解析 Judge LLM 输出，期望格式为 JSON（session 级）：
    {
        "action_tags": ["solution_propose"],
        "emotion_level": "calm",
        "dimension_scores": {...},
        "normalized_score": 0.72,
        "final_score": 3.9,
        "summary": "..."
    }
    v1.2 新增字段（可选，向后兼容）：
        "score_J", "score_K", "persona_id", "path_id"
    """
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    parsed = json.loads(raw)

    result = {
        "session_id": session_id,
        "judge_version": judge_version,
        "action_tags": parsed.get("action_tags", []),
        "emotion_level": parsed.get("emotion_level", "unknown"),
        "dimension_scores": parsed.get("dimension_scores", {}),
        "normalized_score": parsed.get("normalized_score", 0.0),
        "final_score": parsed.get("final_score", round(1 + parsed.get("normalized_score", 0.0) * 4, 1)),
        "summary": parsed.get("summary", ""),
    }

    # v1.2: 透传 J/K 维度得分和 meta 信息（由 Judge LLM 输出或从 session_id 解析）
    for key in ("score_action", "score_H", "score_I", "score_J", "score_K"):
        if key in parsed:
            result[key] = parsed[key]

    # persona_id / path_id：优先取 LLM 输出，否则从 session_id 解析
    meta = parse_session_meta(session_id)
    result["persona_id"] = parsed.get("persona_id", meta.get("persona_id", ""))
    result["path_id"] = parsed.get("path_id", meta.get("path_id", ""))

    return result


# ─── 数据加载与抽样 ────────────────────────────────────────────────────────────

def load_data(batch_path: str = None, file_path: str = None) -> list:
    """加载生产数据，返回 turn 行列表（final.jsonl 每行一个 turn）"""
    if file_path:
        src = Path(file_path)
    elif batch_path:
        run_dir = SKILL_ROOT / batch_path
        src = run_dir / "final.jsonl"
        if not src.exists():
            raise FileNotFoundError(f"未找到 final.jsonl: {src}")
    else:
        raise ValueError("--batch 或 --file 必须指定一个")

    rows = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def group_by_session(rows: list) -> dict[str, dict]:
    """
    将 turn 行按 session 分组，返回 session_id → session_dict。
    session_dict 结构：
      {
        "session_id": "gen_P001_P1_001_s1",
        "turns": [...],
        "所有信号": "...",   # 取第一条 turn 的所有信号字段
      }
    会话id 格式：{session_id}_t{n}，去掉 _t{n} 后缀得到 session_id。
    """
    sessions: dict[str, dict] = {}
    for row in rows:
        full_id = row.get("会话id", "")
        # 去掉 _t{n} 后缀得到 session_id
        session_id = re.sub(r"_t\d+$", "", full_id)
        if session_id not in sessions:
            sessions[session_id] = {
                "session_id": session_id,
                "turns": [],
                "所有信号": row.get("所有信号", ""),
            }
        sessions[session_id]["turns"].append(row)
    return sessions


def sample_sessions(sessions: dict[str, dict], n: int) -> list[dict]:
    """随机抽取 n 个 session，返回 session_dict 列表。n=-1 返回全量。"""
    all_sessions = list(sessions.values())
    if n == -1 or n >= len(all_sessions):
        return all_sessions
    return random.sample(all_sessions, n)


# ─── Checkpoint ────────────────────────────────────────────────────────────────

def load_progress(round_dir: Path) -> dict:
    p = round_dir / "judge_progress.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_progress(round_dir: Path, progress: dict):
    progress["last_updated"] = datetime.datetime.now().isoformat()
    with open(round_dir / "judge_progress.json", "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def append_result(round_dir: Path, result: dict):
    out_file = round_dir / "judge_results.jsonl"
    with open(out_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def append_failed(round_dir: Path, task_id: str, error: str):
    failed_file = round_dir / "judge_failed.jsonl"
    with open(failed_file, "a", encoding="utf-8") as f:
        f.write(json.dumps({"task_id": task_id, "error": error}, ensure_ascii=False) + "\n")


# ─── 单任务执行 ────────────────────────────────────────────────────────────────

def run_task(session: dict, judge_prompt_template: str,
             judge_version: str, dry_run: bool) -> tuple:
    session_id = session.get("session_id", "unknown")
    try:
        prompt = build_judge_input(session, judge_prompt_template,
                                  persona_defs=_persona_defs, path_skeletons=_path_skeletons)
        if dry_run:
            return session_id, None, None, prompt
        raw = call_llm(prompt, session_id, temperature=_api_temperature)
        result = parse_judge_result(raw, session_id, judge_version)
        return session_id, result, None, None
    except Exception as e:
        return session_id, None, str(e), None


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-data-factory Phase 3 Judge 打分（session 维度）")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--batch", help="生产 run 目录（相对 cs-data-factory 根目录），如 data/runs/run_xxx")
    grp.add_argument("--file", help="直接指定数据文件路径（.jsonl）")
    parser.add_argument("--n", type=int, default=20, help="抽样 session 数（默认 20，-1=全量）")
    parser.add_argument("--round", default="tuning_round_1", help="质检轮次名（默认 tuning_round_1）")
    parser.add_argument("--judge-version", default="v1.0", help="Judge Prompt 版本（默认 v1.0）")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数（默认 4，仅 API 模式有效）")
    parser.add_argument("--dry-run", action="store_true", help="打印前 3 条打分 prompt，不调用 API")
    # SubAgent 模式 和 API 模式互斥
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--export-prompts", action="store_true",
                          help="SubAgent 模式（推荐）：只导出 prompt 文件到 subagent_prompts/，不调用 LLM")
    mode_grp.add_argument("--appid", default=None,
                          help="API 模式：模型 API Key（与 --export-prompts 互斥）。也可通过环境变量 CS_FACTORY_APPID 传入，命令行优先。")
    parser.add_argument("--model", default="your-model",
                        help="API 模式：模型名（默认 your-model，与 --appid 搭配使用）")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="API 模式：温度参数（默认 0.0，kimi-k2.5 等模型需设为 1.0）")
    parser.add_argument("--batch-size", type=int, default=5,
                        help="SubAgent 模式：每个 prompt 文件包含的 session 数量（默认 5，即一个 SubAgent 处理 5 个 session）")
    args = parser.parse_args()

    # ── 参数校验：--model 必须与 --appid 搭配 ──
    if args.model != "your-model" and not (args.appid or os.environ.get("CS_FACTORY_APPID")):
        parser.error("--model 只在 API 模式下有效，需配合 --appid 一起使用")

    # ── 加载 Judge Prompt ──
    print(f"📋 加载 Judge Prompt {args.judge_version}...")
    judge_prompt_template = load_judge_prompt(args.judge_version)

    # ── v1.2: 加载 Persona 定义和路径骨架（仅当 prompt 模板中含对应占位符时）──
    persona_defs = {}
    path_skeletons = {}
    if "{{persona_definition}}" in judge_prompt_template or "{{path_skeleton}}" in judge_prompt_template:
        print(f"📋 检测到 v1.2 模板，加载 Persona 定义和路径骨架...")
        persona_defs = load_persona_definitions()
        path_skeletons = load_path_skeletons()
        print(f"   Persona 定义: {len(persona_defs)} 个（{', '.join(sorted(persona_defs.keys())) if persona_defs else '无'}）")
        print(f"   路径骨架: {len(path_skeletons)} 个")

    # 模块级变量赋值：让 run_task 可以引用
    global _persona_defs, _path_skeletons
    _persona_defs = persona_defs
    _path_skeletons = path_skeletons

    # ── 加载数据并按 session 分组 ──
    print(f"📂 加载数据...")
    rows = load_data(batch_path=args.batch, file_path=args.file)
    print(f"   原始 turns: {len(rows)}")
    all_sessions = group_by_session(rows)
    print(f"   共 {len(all_sessions)} 个 session")

    # ── 准备 round 目录 ──
    round_dir = SKILL_ROOT / "data" / "judge_runs" / args.round
    round_dir.mkdir(parents=True, exist_ok=True)

    # ── exec_mode.json：锁定本轮执行模式，防止 SubAgent/API 串台 ──
    # ⚠️ 强制执行机制：exec_mode.json 必须由 confirm_judge_mode.py 预先写入，
    # 代表用户已明确选择了 Judge 执行模式（Phase 3 Step 0）。
    # dry-run 模式豁免此检查（仅用于预览 prompt，不实际执行打分）。
    mode_file = round_dir / "exec_mode.json"
    if not args.dry_run and not mode_file.exists():
        print(f"❌ Judge 模式未确认：{mode_file} 不存在", file=sys.stderr)
        print(f"   Phase 3 Step 0 要求用户明确选择 Judge 执行模式（SubAgent/API）。", file=sys.stderr)
        print(f"   请先运行以下命令展示模式选项，收到用户确认后再指定 --mode：", file=sys.stderr)
        print(f"   python scripts/confirm_judge_mode.py --round {args.round}", file=sys.stderr)
        print(f"   （用户选 A 后）python scripts/confirm_judge_mode.py --round {args.round} --mode A", file=sys.stderr)
        return

    api_requested = bool(args.appid or os.environ.get("CS_FACTORY_APPID")) and not args.export_prompts
    if args.export_prompts or (not api_requested and not args.dry_run):
        desired_mode = "subagent"
    elif api_requested:
        desired_mode = "api"
    else:
        desired_mode = None  # dry-run 不写

    if desired_mode is not None:
        if mode_file.exists():
            existing_mode = json.loads(mode_file.read_text(encoding="utf-8")).get("mode")
            if existing_mode != desired_mode:
                print(f"❌ 该 round 已使用 {existing_mode} 模式，当前命令要求 {desired_mode} 模式，无法混用。")
                print(f"   如需切换模式，请使用新的 --round 名称。")
                return
        else:
            mode_data = {
                "mode": desired_mode,
                "model": args.model if args.appid else "subagent",
                "set_at": datetime.datetime.now().isoformat(),
            }
            mode_file.write_text(json.dumps(mode_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── API 模式：初始化模块级变量（支持环境变量 CS_FACTORY_APPID）──
    if args.appid is not None or (not args.export_prompts and not args.dry_run):
        # 非 SubAgent/dry-run 模式时尝试解析 AppId
        try:
            from lib.api_client import resolve_appid as _resolve_appid
            resolved = _resolve_appid(args.appid)
        except ValueError as e:
            if not args.export_prompts and not args.dry_run:
                print(f"❌ API 模式需要 AppId：{e}")
                return
            resolved = None
        if resolved:
            global _api_appid, _api_model, _api_temperature
            _api_appid = resolved
            _api_model = args.model
            _api_temperature = args.temperature

    # ── 断点续跑：先加载进度，再决定是否重新采样 ──
    progress = load_progress(round_dir)
    samples_file = round_dir / "samples.jsonl"
    if samples_file.exists() and progress:
        # 续跑：读取已有采样集
        with open(samples_file, "r", encoding="utf-8") as f:
            sampled = [json.loads(line) for line in f if line.strip()]
        print(f"   续跑模式：读取已有采样集 {len(sampled)} 个 session")
    else:
        sampled = sample_sessions(all_sessions, args.n)
        with open(samples_file, "w", encoding="utf-8") as f:
            for s in sampled:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"   抽样保存至: {samples_file}")
    print(f"   抽样后 sessions: {len(sampled)}")

    done_ids = set(progress.get("done", []))
    pending = [s for s in sampled if s.get("session_id", "") not in done_ids]
    print(f"   待打分: {len(pending)}  已跳过: {len(done_ids)}")

    if not pending:
        print("✅ 所有 session 已完成打分")
        return

    # ── 初始化 progress ──
    if not progress:
        progress = {
            "status": "running",
            "judge_version": args.judge_version,
            "total": len(sampled),
            "done_count": 0,
            "failed_count": 0,
            "done": [],
            "started_at": datetime.datetime.now().isoformat(),
        }

    # ── DRY RUN ──
    if args.dry_run:
        print(f"\n🔍 DRY-RUN 模式（前 3 条 prompt）")
        for session in pending[:3]:
            _, _, _, prompt = run_task(session, judge_prompt_template, args.judge_version, dry_run=True)
            print(f"\n{'='*60}\n{session.get('session_id','')}\n{'='*60}")
            print(prompt[:2000])
            print("...")
        return

    # ── EXPORT PROMPTS（SubAgent 模式，推荐）──
    if args.export_prompts:
        prompt_dir = round_dir / "subagent_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        batch_size = getattr(args, "batch_size", 5)

        # 将 pending sessions 分批（每批 batch_size 个）
        batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
        exported_batches = 0
        skipped_batches = 0
        total_sessions = 0

        for batch_idx, batch_sessions in enumerate(batches):
            batch_id = f"batch_{batch_idx + 1:03d}"
            prompt_file = prompt_dir / f"{batch_id}.prompt.txt"
            if prompt_file.exists():
                skipped_batches += 1
                continue

            # 构建多 session 合并 Prompt
            session_prompts = []
            session_ids = [s.get("session_id", f"unknown_{i}") for i, s in enumerate(batch_sessions)]
            for s in batch_sessions:
                session_prompts.append(build_judge_input(s, judge_prompt_template,
                                                         persona_defs=persona_defs,
                                                         path_skeletons=path_skeletons))

            combined_prompt = _build_batch_judge_prompt(session_ids, session_prompts)
            prompt_file.write_text(combined_prompt, encoding="utf-8")
            exported_batches += 1
            total_sessions += len(batch_sessions)

        print(f"\n✅ 批次 Prompt 文件已导出: {exported_batches} 个批次（共 {total_sessions} 个 session，每批 {batch_size} 个）→ {prompt_dir}")
        if skipped_batches:
            print(f"   跳过（已存在）: {skipped_batches} 个批次")

        # 写 batch_ids.json（供主 Agent 批量 spawn SubAgent 时读取任务列表）
        all_batch_ids = [f"batch_{i + 1:03d}" for i in range(len(batches))]
        ids_file = prompt_dir / "batch_ids.json"
        ids_file.write_text(
            json.dumps({
                "batch_ids": all_batch_ids,
                "batch_size": batch_size,
                "total_sessions": len(pending),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"   batch_ids.json → {ids_file}（{len(all_batch_ids)} 个批次）")
        print(f"\n下一步：Agent 批量 spawn SubAgent 执行打分")
        print(f"   SubAgent 读取: {prompt_dir}/batch_XXX.prompt.txt")
        print(f"   SubAgent 写入: {prompt_dir}/batch_XXX.result.txt")
        print(f"   （每个 SubAgent 处理 {batch_size} 个 session，输出 JSON 数组）")
        print(f"\n打分完成后汇总：")
        print(f"   python judge/scripts/collect_subagent_results.py --round {args.round}")
        return

    # ── 并发打分 ──
    print(f"\n🚀 开始打分（workers={args.workers}，judge={args.judge_version}）...")
    done_count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_task, session, judge_prompt_template, args.judge_version, False): session
            for session in pending
        }
        for future in as_completed(futures):
            session_id, result, error, _ = future.result()
            if error:
                print(f"  ❌ {session_id}: {error[:100]}")
                append_failed(round_dir, session_id, error)
                failed_count += 1
            else:
                score = result.get("normalized_score", "?")
                final = result.get("final_score", "?")
                print(f"  ✅ {session_id}: normalized={score}  final={final}")
                append_result(round_dir, result)
                progress["done"].append(session_id)
                done_count += 1

            progress["done_count"] = len(progress["done"])
            progress["failed_count"] = failed_count
            write_progress(round_dir, progress)

    # ── 收尾 ──
    progress["status"] = "done"
    progress["finished_at"] = datetime.datetime.now().isoformat()
    write_progress(round_dir, progress)

    total_done = progress["done_count"]
    print(f"\n📊 完成：打分={total_done}  失败={failed_count}")
    print(f"📁 结果: {round_dir / 'judge_results.jsonl'}")
    if failed_count > 0:
        print(f"⚠️  失败任务见: {round_dir / 'judge_failed.jsonl'}")
    print(f"\n下一步（API 模式，无需 collect_subagent_results.py）：")
    print(f"   python judge/scripts/gen_report.py --round {args.round}")


if __name__ == "__main__":
    main()