#!/usr/bin/env python3
"""
analyze_signals.py
------------------
Phase 0 信号分析：分析 Excel「所有信号」列，生成信号规则草稿，供人工审核后入库。

**执行时机**：Phase 0（与 SOP 解析并行，可在 graph.json 生成后立即执行）
**人工审核**：脚本输出草稿 data/tmp/{sop_id}_signal_rules_draft.json；
             主 Agent 调用 LLM 对 mock 规则进行评审补全；
             用户确认无误后，将文件重命名/复制到 data/sop/{sop_id}_signal_rules.json（正式入库）

分析维度（针对每个信号 key）：
  1. **类型**（type）：
     - enum   — 跨 session 可枚举（有限取值集合，如「是/否」「全部已使用/部分使用/…」）
     - mock   — 无法枚举，需规则 mock（如电话号、用户聊天记录、金额）
  2. **变化性**（variability）：
     - static  — 同一 session 内信号值不变（开场即确定）
     - dynamic — 同一 session 不同 turn 可能变化（如「外呼结果」随流程更新）
  3. **mock_rule**（type=mock 时填充）：LLM 评审后补全的 mock 规则描述
  4. **source**（信号来源）：
     - judgment — 出现在 graph.json signal_fields 中（判断信号，骨架已锁定枚举值）
     - slot     — 出现在 graph.json slot_fields 中，或从 other 提升（参考信号，需 mock 后注入话术）
     - other    — 两者都不在且 session 内静态（补集信号，值从真实样本池轮询取用）
     ⚠️ 动态提升规则：source=other 但 variability=dynamic 的信号，自动提升为 source=slot，
        因为其值在 session 内会变化，需要 LLM 按 mock_rule 生成而非从静态池取用。

用法:
  python scripts/analyze_signals.py \\
      --sop-id daocandaopiao \\
      [--excel assets/real_signals/real_signals_sample_daocandaopiao.xlsx] \\
      [--llm-review]    # 是否调用 LLM 对 mock 规则进行评审（默认 False，生成草稿后人工执行）

输出:
  data/tmp/{sop_id}_signal_rules_draft.json   （草稿，含 LLM 评审建议，须人工确认）
  →（人工确认后）→ data/sop/{sop_id}_signal_rules.json   （正式入库，Phase 1 消费）
"""

import json
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# Windows 下强制 stdout/stderr 使用 UTF-8，避免中文/emoji 导致 GBK 编码乱码
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── 路径 ──────────────────────────────────────────────────────────────────────
SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))
from lib import (
    SIGNAL_RULES_DESCRIPTION,
    build_other_signal_pool,
    normalize_signal_rules,
    parse_signal_template,
    strip_runtime_signal_summary,
)

ASSETS_DIR = SKILL_ROOT / "assets"
DATA_DIR = SKILL_ROOT / "data"
SOP_GRAPH_DIR = DATA_DIR / "sop"
TMP_DIR = DATA_DIR / "tmp"
REAL_SIGNALS_DIR = ASSETS_DIR / "real_signals"


# ─── Excel 读取 ────────────────────────────────────────────────────────────────

def load_excel_signals(excel_path: Path) -> list[dict]:
    """
    读取 Excel，提取每行的 session_id（若有）和「所有信号」列，
    返回 list[{"session_id": str, "turn_index": int, "signals": {key: value}}]
    """
    try:
        import openpyxl
    except ImportError:
        print("❌ 请先安装 openpyxl：pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    ws = wb.active

    headers = []
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(c).strip() if c is not None else "" for c in row]
            continue
        row_dict = dict(zip(headers, row))
        signal_text = row_dict.get("所有信号") or row_dict.get("signals") or ""
        if not signal_text:
            continue
        session_id = (
            row_dict.get("会话id") or row_dict.get("session_id") or
            row_dict.get("会话ID") or f"row_{i}"
        )
        turn_raw = row_dict.get("turn_index") or row_dict.get("轮次") or 0
        try:
            turn_index = int(turn_raw) if turn_raw else 0
        except (ValueError, TypeError):
            turn_index = 0

        signals = parse_signal_text(str(signal_text))
        rows.append({
            "session_id": str(session_id),
            "turn_index": turn_index,
            "signals": signals,
        })

    wb.close()
    print(f"  📊 读取 Excel：{len(rows)} 行（含信号文本）")
    return rows


def _read_first_signal_text(excel_path: Path) -> str:
    """
    从 Excel 中读取第一行的「所有信号」原始文本（用于信号模板提取）。
    直接读取原始文本，不做任何解析或截断。
    """
    try:
        import openpyxl
    except ImportError:
        return ""

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    ws = wb.active

    headers = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(c).strip() if c is not None else "" for c in row]
            continue
        row_dict = dict(zip(headers, row))
        signal_text = row_dict.get("所有信号") or row_dict.get("signals") or ""
        wb.close()
        return str(signal_text) if signal_text else ""

    wb.close()
    return ""


# ─── 信号文本解析 ──────────────────────────────────────────────────────────────

def _json_safe_value(raw: str) -> str:
    """
    将原始值转义为可安全嵌入 JSON 字符串的格式。
    
    处理以下特殊字符：
    - 双引号 " → \"  （JSON字符串内必须转义）
    - 反斜杠 \\ → \\\\ 
    - 控制字符（\\n、\\t 等）保持不变（JSON 允许）
    
    注意：这里只做最小必要转义，不处理全量 Unicode 转义，
    因为 json.dump() 会处理剩余的编码问题。
    """
    return raw.replace("\\", "\\\\").replace('"', '\\"')


def parse_signal_text(text: str) -> dict:
    """
    将「所有信号」文本解析为 {key: value} 扁平 dict。
    支持格式：{key:value} 或 {key:value,extra=...}
    
    返回的值已经过 JSON 安全转义，可直接写入 .json 文件
    而不会因内部引号导致 json.load() 解析失败。
    """
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("###"):
            continue
        if line.startswith("{") and ":" in line:
            inner = line[1:]
            if inner.endswith("}"):
                inner = inner[:-1]
            colon_idx = inner.index(":")
            key = inner[:colon_idx].strip()
            # 取冒号后到第一个 , 之前的值（兼容 {key:value,extra=xxx} 格式）
            raw_val = inner[colon_idx + 1:].strip()
            # 去除可能的 ,xxx=yyy 后缀（仅保留主值）
            comma_idx = raw_val.find(",")
            if comma_idx != -1:
                raw_val = raw_val[:comma_idx].strip()
            result[key] = _json_safe_value(raw_val)
    return result


# ─── 统计分析 ──────────────────────────────────────────────────────────────────

def analyze_signals(rows: list[dict], graph: dict) -> dict:
    """
    分析信号枚举 / 变化性，返回 {key: analysis_info}
    """
    signal_fields = set(graph.get("signal_fields", []))
    slot_fields = set(graph.get("slot_fields", []))

    # 按 key 聚合 session-level 值（session_id → set of values）
    key_session_values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    # 按 key 聚合 turn-level 值（session_id → list of (turn_index, value)）
    key_turn_values: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        sid = row["session_id"]
        tidx = row["turn_index"]
        for k, v in row["signals"].items():
            key_session_values[k][sid].add(v)
            key_turn_values[k][sid].append((tidx, v))

    # 所有出现过的 key
    all_keys = sorted(set(key_session_values.keys()))
    total_sessions = len(set(r["session_id"] for r in rows))

    result = {}
    for key in all_keys:
        sess_vals = key_session_values[key]
        # 所有去重值
        all_values_flat = set(v for vs in sess_vals.values() for v in vs)
        # 值数量（跨 session 的枚举数）
        enum_count = len(all_values_flat)
        # 出现的 session 数
        appear_sessions = len(sess_vals)
        # 是否动态：同一 session 内有多个不同值
        is_dynamic = any(len(vs) > 1 for vs in sess_vals.values())

        # 枚举 vs mock 判断：枚举值数 ≤ 20 且每个值看起来不是随机串
        is_enum = _looks_like_enum(all_values_flat)

        # 来源判定
        if key in signal_fields:
            source = "judgment"
        elif key in slot_fields:
            source = "slot"
        else:
            source = "other"

        result[key] = {
            "source": source,
            "type": "enum" if is_enum else "mock",
            "variability": "dynamic" if is_dynamic else "static",
            "enum_values": sorted(all_values_flat) if is_enum else [],
            "examples": sorted(all_values_flat)[:5] if not is_enum else [],
            "appear_sessions": appear_sessions,
            "total_sessions": total_sessions,
            "mock_rule": "",  # 由 LLM 评审后填充
        }

    # ── 动态提升：other + dynamic → slot ──
    # 补集信号中如果同 session 内值会变化，说明不能从静态池取用，
    # 需要 LLM 按 mock_rule 生成，因此提升为 slot（参考信号）。
    promoted_keys = []
    for key, info in result.items():
        if info["source"] == "other" and info["variability"] == "dynamic":
            info["source"] = "slot"
            info["_promoted_from_other"] = True
            # 为提升的信号填入默认 mock_rule（LLM 评审时可修改）
            if not info["mock_rule"]:
                info["mock_rule"] = "同 session 内值可变，按上下文合理生成"
            promoted_keys.append(key)

    if promoted_keys:
        print(f"  ⚡ 动态补集信号提升为参考信号（other+dynamic → slot）：{promoted_keys}")

    return result


def _looks_like_enum(values: set) -> bool:
    """
    判断是否是可枚举信号：
    - 值的数量 ≤ 20
    - 没有看起来像电话/身份证/流水号的值（长数字串）
    - 没有长文本（>30 字符）
    """
    if len(values) > 20:
        return False
    for v in values:
        v = str(v).strip()
        # 长数字串（≥8位）→ 很可能是 ID/电话/金额编号
        if re.fullmatch(r"\d{8,}", v):
            return False
        # 长文本
        if len(v) > 30:
            return False
    return True


# ─── LLM 评审 Prompt 构建 ──────────────────────────────────────────────────────

def build_llm_review_prompt(sop_id: str, analysis: dict, graph: dict) -> str:
    """
    构造 LLM 评审 Prompt：对 type=mock 的信号，要求 LLM 给出 mock_rule。
    """
    # 只对 source=slot 的参考信号生成 mock rule（含从 other 提升的动态信号）；
    # other 补集信号（静态）不注入 prompt，无需 mock rule
    # _no_sample=True 的字段已在 build_draft() 中填入默认规则，无需 LLM 评审，也排除在外
    mock_keys = {
        k: v for k, v in analysis.items()
        if v["type"] == "mock" and v["source"] == "slot" and not v.get("_no_sample", False)
    }
    if not mock_keys:
        return ""

    lines = [
        f"你是客服数据工厂的信号分析助手（场景：{sop_id}）。",
        "",
        "以下信号字段无法直接枚举（取值是随机/个性化的），请为每个字段写出简洁的 **mock 规则**，",
        "格式：「生成规则的自然语言描述（例：随机生成11位手机号）」。",
        "",
        "## 待评审信号字段（type=mock）",
        "",
    ]

    for key, info in mock_keys.items():
        examples_str = "、".join(f"「{e}」" for e in info["examples"][:3]) if info["examples"] else "（无示例）"
        source_label = {"judgment": "判断信号", "slot": "参考信号（话术引用）", "other": "补集信号"}.get(info["source"], info["source"])
        variability_label = "动态（session 内可变）" if info["variability"] == "dynamic" else "静态（session 内固定）"
        lines.append(f"### {key}")
        lines.append(f"- 来源：{source_label}")
        lines.append(f"- 变化性：{variability_label}")
        lines.append(f"- 样本示例：{examples_str}")
        lines.append(f"- mock_rule（请填写）：")
        lines.append("")

    lines += [
        "## 输出要求",
        "",
        "输出纯 JSON，格式：",
        "```json",
        "{",
        '  "信号字段名": "mock 规则描述",',
        '  ...',
        "}",
        "```",
        "",
        "只输出 JSON，不要任何解释。",
    ]

    return "\n".join(lines)


# ─── 草稿写出 ──────────────────────────────────────────────────────────────────

def build_draft(sop_id: str, analysis: dict, graph: dict,
                rows: list[dict] = None, excel_path=None,
                first_signal_text: str = "") -> dict:
    """
    构造草稿 dict，写入 data/tmp/{sop_id}_signal_rules_draft.json
    
    数据结构说明：
      - signals: 完整信号表（唯一权威数据源，所有修改应在此处进行）
      - signal_template: 从真实样本提取的信号模板（保留 section headers + key 顺序）
      - other_signal_pool: 补集信号值池（按 session 聚合的静态补集信号值集合）

    运行时约束：
      `_production_signals` 仅在读取/预览时由 `signals` 自动派生，
      草稿文件本身不再持久化该摘要，避免双写维护成本。
    
    参数：
      rows: load_excel_signals() 的返回值（用于构建补集信号池）
      excel_path: Excel 文件路径（用于从原始文件读取完整值构建补集信号池）
      first_signal_text: 第一行真实样本的「所有信号」原始文本（用于提取信号模板）
    """
    signal_fields = set(graph.get("signal_fields", []))
    slot_fields = set(graph.get("slot_fields", []))

    signals = {}
    for key, info in analysis.items():
        signals[key] = {
            "source": info["source"],
            "type": info["type"],
            "variability": info["variability"],
            "enum_values": info["enum_values"],
            "examples": info["examples"],
            "mock_rule": info["mock_rule"],
            "_stats": {
                "appear_sessions": info["appear_sessions"],
                "total_sessions": info["total_sessions"],
            },
        }
        # 保留提升标记（从 other 提升为 slot 的动态信号）
        if info.get("_promoted_from_other"):
            signals[key]["_promoted_from_other"] = True
        # 保留 outbound_expand 标记（由下方自动推断段落或手动设置）
        if info.get("outbound_expand"):
            signals[key]["outbound_expand"] = True

    # 补充：graph 里有但 Excel 没出现的信号
    for key in signal_fields:
        if key not in signals:
            signals[key] = {
                "source": "judgment",
                "type": "enum",
                "variability": "static",
                "enum_values": [],
                "examples": [],
                "mock_rule": "",
                "_stats": {"appear_sessions": 0, "total_sessions": 0},
                "_note": "仅在 graph signal_fields 中声明，Excel 中未出现",
            }
    for key in slot_fields:
        if key not in signals:
            # SOP 中声明但 Excel 中未出现，说明信号名不匹配（或真实样本无此信号）。
            # 此时无法从样本推断 mock 规则，打 _no_sample 标记并填入默认规则，
            # 生产时 LLM 按常识生成，不触发 verify 警告，也不纳入 LLM 评审 Prompt。
            signals[key] = {
                "source": "slot",
                "type": "mock",
                "variability": "static",
                "enum_values": [],
                "examples": [],
                "mock_rule": "按常识生成合理值（Excel 样本中无此信号，SOP 话术槽位仅供参考）",
                "_no_sample": True,
                "_stats": {"appear_sessions": 0, "total_sessions": 0},
                "_note": "仅在 graph slot_fields 中声明，Excel 中未出现；已填默认 mock_rule，如需精确规则可手动修改",
            }

    # ── outbound_expand 自动推断：judgment + dynamic → 标记需要在 outbound_update 里展开对话文本 ──
    # 判断信号（驱动路径分支）且在 session 内动态变化的信号，通常对应外呼/回调等需要记录完整通话内容的场景。
    # 此类信号的结论值由骨架锁定，但需要在外呼触发轮次的 outbound_update 字段里生成完整多轮对话文本。
    # 自动推断逻辑：可被 Phase 0 审查后手动覆盖（在 signal_rules.json 里设为 false）。
    outbound_expand_keys = []
    for key, sig in signals.items():
        if sig.get("source") == "judgment" and sig.get("variability") == "dynamic":
            if not sig.get("outbound_expand"):  # 避免覆盖已手动设置的值
                sig["outbound_expand"] = True
                existing_note = sig.get("_note", "")
                sig["_note"] = (
                    (existing_note + " | " if existing_note else "")
                    + "自动标记 outbound_expand=true（判断信号+动态变化，需在外呼触发轮次的 outbound_update 字段里展开完整客服-商家对话文本，而非摘要）"
                )
                outbound_expand_keys.append(key)
    if outbound_expand_keys:
        print(f"  📡 自动标记 outbound_expand（judgment+dynamic）：{outbound_expand_keys}")

    # ── 信号模板（从真实样本第一行提取，自适应不同场景）──
    signal_template = ""
    if first_signal_text:
        signal_template = parse_signal_template(first_signal_text)
        print(f"  📋 信号模板已提取（{len(signal_template.splitlines())} 行）")

    # ── 补集信号值池（source=other, variability=static 的信号，按 session 聚合）──
    static_other_keys = {
        k for k, v in signals.items()
        if v["source"] == "other" and v["variability"] == "static"
    }
    other_signal_pool = []
    if static_other_keys and rows:
        other_signal_pool = build_other_signal_pool(
            rows, static_other_keys, excel_path=excel_path
        )
        print(f"  🗃️ 补集信号池已构建（{len(other_signal_pool)} 个 session，{len(static_other_keys)} 个信号 key）")

    return {
        "sop_id": sop_id,
        "_format_version": "2.0",
        "_description": SIGNAL_RULES_DESCRIPTION,
        "signal_template": signal_template,
        "other_signal_pool": other_signal_pool,
        "signals": signals,
    }


def sync_mock_rules_to_production(draft: dict, mock_rule_updates: dict[str, str]) -> dict:
    """
    兼容旧函数名：将 LLM/SubAgent 返回的 mock_rule 写入 `signals` 权威表。

    `_production_signals` 不再持久化；返回值会附带运行时派生摘要，
    便于预览/展示，但调用方写回文件前应先去掉该运行时字段。
    
    参数：
        draft: 已加载的 signal_rules draft dict（来自 json.load）
        mock_rule_updates: {信号字段名: mock_rule描述字符串}
    
    返回：
        修改后的 draft（含运行时派生摘要；调用方写回文件前需去掉该字段）
    
    用法示例（SKILL.md Step C-3）：
        import json
        from scripts.analyze_signals import sync_mock_rules_to_production
        
        with open("data/tmp/{sop_id}_signal_rules_draft.json", encoding="utf-8") as f:
            draft = json.load(f)
        
        llm_response = {"支付金额": "随机生成50-500元...", "开票联系商户电话": "..."}
        draft = sync_mock_rules_to_production(draft, llm_response)
        
        with open("data/tmp/{sop_id}_signal_rules_draft.json", "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
    """
    draft = strip_runtime_signal_summary(draft)
    signals = draft.get("signals", {})

    for sig_key, new_mock_rule in mock_rule_updates.items():
        if sig_key in signals:
            signals[sig_key]["mock_rule"] = new_mock_rule

    return normalize_signal_rules(draft)


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-data-factory Phase 0 信号分析")
    parser.add_argument("--sop-id", required=True, help="SOP 标识（如 daocandaopiao）")
    parser.add_argument("--excel", default="",
                        help="Excel 路径（默认：assets/real_signals/real_signals_sample_{sop_id}.xlsx）")
    parser.add_argument("--llm-review", action="store_true",
                        help="输出 LLM 评审 Prompt（粘贴给 LLM 后将其 JSON 回复合并到草稿）")
    parser.add_argument(
        "--merge-mock-rules",
        default="",
        metavar="JSON_FILE",
        help=(
            "将 LLM/SubAgent 返回的 mock_rule 写入草稿中的 signals 权威表。\n"
            "推荐形式（跨平台安全）：JSON 文件路径，如 data/tmp/{sop_id}_llm_mock_rules.json\n"
            "备用形式（仅限 macOS/Linux）：内联 JSON 字符串，如 '{\"支付金额\": \"...\"}'\n"
            "⚠️  Windows PowerShell 下内联 JSON 字符串存在引号解析风险，必须使用文件路径形式。\n"
            "完成后覆盖写回 data/tmp/{sop_id}_signal_rules_draft.json，并打印同步摘要。\n"
            "此模式下不执行信号分析，仅做 mock_rule 合并，不需要 --excel。"
        ),
    )
    args = parser.parse_args()

    # ── --merge-mock-rules 独立模式：合并 mock_rule 后直接返回，不执行信号分析 ──
    if args.merge_mock_rules:
        draft_path = TMP_DIR / f"{args.sop_id}_signal_rules_draft.json"
        if not draft_path.exists():
            print(f"❌ 草稿文件不存在：{draft_path}", file=sys.stderr)
            print(f"   请先运行 analyze_signals.py --sop-id {args.sop_id} 生成草稿", file=sys.stderr)
            sys.exit(1)

        # 解析输入：JSON 文件路径（推荐）或 JSON 字符串（不推荐，Windows PowerShell 存在引号解析风险）
        raw = args.merge_mock_rules.strip()
        try:
            if raw.startswith("{"):
                # ⚠️  内联 JSON 字符串在 Windows PowerShell 中可能因引号转义问题导致解析失败。
                # 推荐做法：先将 JSON 写入临时文件（如 data/tmp/{sop_id}_llm_mock_rules.json），
                # 再用文件路径形式传入：--merge-mock-rules data/tmp/{sop_id}_llm_mock_rules.json
                print("⚠️  检测到内联 JSON 字符串传参。", file=sys.stderr)
                print("   Windows PowerShell 下引号转义可能导致解析失败。", file=sys.stderr)
                print("   推荐改用文件路径形式：先将 JSON 写入文件，再传入文件路径。", file=sys.stderr)
                mock_updates = json.loads(raw)
            else:
                p = Path(raw) if Path(raw).is_absolute() else SKILL_ROOT / raw
                if not p.exists():
                    print(f"❌ JSON 文件不存在：{p}", file=sys.stderr)
                    print(f"   推荐流程：将 LLM 返回的 JSON 用 write 工具写入 data/tmp/{args.sop_id}_llm_mock_rules.json", file=sys.stderr)
                    print(f"   再执行：python scripts/analyze_signals.py --sop-id {args.sop_id} --merge-mock-rules data/tmp/{args.sop_id}_llm_mock_rules.json", file=sys.stderr)
                    sys.exit(1)
                with open(p, encoding="utf-8") as f:
                    mock_updates = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ JSON 解析失败：{e}", file=sys.stderr)
            print(f"   若在 Windows PowerShell 下使用内联 JSON 字符串，建议改用文件路径形式：", file=sys.stderr)
            print(f"   1. 将 JSON 内容写入文件：data/tmp/{args.sop_id}_llm_mock_rules.json", file=sys.stderr)
            print(f"   2. 执行：python scripts/analyze_signals.py --sop-id {args.sop_id} --merge-mock-rules data/tmp/{args.sop_id}_llm_mock_rules.json", file=sys.stderr)
            sys.exit(1)

        if not isinstance(mock_updates, dict):
            print("❌ --merge-mock-rules 的内容必须是 JSON 对象（dict）", file=sys.stderr)
            sys.exit(1)

        with open(draft_path, encoding="utf-8") as f:
            draft = json.load(f)

        draft = sync_mock_rules_to_production(draft, mock_updates)

        persisted_draft = strip_runtime_signal_summary(draft)
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(persisted_draft, f, ensure_ascii=False, indent=2)

        print(f"✅ mock_rule 已写入草稿权威表：{draft_path}")
        for k, v in mock_updates.items():
            in_signals = k in draft.get("signals", {})
            in_runtime_summary = any(
                k in draft.get("_production_signals", {}).get(cat, {})
                for cat in ("judgment", "slot")
            )
            status = "✓ 已写入权威表，运行时摘要可派生" if (in_signals and in_runtime_summary) else ("⚠ 字段不存在" if not in_signals else "⚠ 已写入权威表，但运行时摘要缺失")
            print(f"  {status}  {k}")
        return

    # ── 确定 Excel 路径 ──
    if args.excel:
        excel_path = SKILL_ROOT / args.excel if not Path(args.excel).is_absolute() else Path(args.excel)
    else:
        excel_path = REAL_SIGNALS_DIR / f"real_signals_sample_{args.sop_id}.xlsx"

    if not excel_path.exists():
        print(f"❌ Excel 文件不存在：{excel_path}", file=sys.stderr)
        sys.exit(1)

    # ── 加载 graph.json ──
    graph_file = SOP_GRAPH_DIR / f"{args.sop_id}_graph.json"
    if not graph_file.exists():
        print(f"❌ graph.json 不存在：{graph_file}", file=sys.stderr)
        print(f"   请先完成 Phase 0-A SOP 解析（生成 data/sop/{args.sop_id}_graph.json）", file=sys.stderr)
        sys.exit(1)
    with open(graph_file, encoding="utf-8") as f:
        graph = json.load(f)

    print(f"📂 加载材料...")
    print(f"  sop_id: {args.sop_id}")
    print(f"  graph 信号字段（judgment）: {graph.get('signal_fields', [])}")
    print(f"  graph 参考字段（slot）: {graph.get('slot_fields', [])}")

    # ── 读取 Excel ──
    rows = load_excel_signals(excel_path)
    if not rows:
        print("❌ Excel 中未读到有效信号行", file=sys.stderr)
        sys.exit(1)

    # ── 提取第一行原始信号文本（用于信号模板提取）──
    first_signal_text = _read_first_signal_text(excel_path)
    if first_signal_text:
        print(f"  📋 已提取第一行原始信号文本（{len(first_signal_text)} 字符）")

    # ── 统计分析 ──
    print(f"\n🔍 统计分析中...")
    analysis = analyze_signals(rows, graph)

    enum_keys = [k for k, v in analysis.items() if v["type"] == "enum"]
    mock_keys = [k for k, v in analysis.items() if v["type"] == "mock"]
    dynamic_keys = [k for k, v in analysis.items() if v["variability"] == "dynamic"]
    judgment_keys = [k for k, v in analysis.items() if v["source"] == "judgment"]
    slot_keys = [k for k, v in analysis.items() if v["source"] == "slot"]
    other_keys = [k for k, v in analysis.items() if v["source"] == "other"]

    print(f"\n📊 分析结果：")
    print(f"  总信号数：{len(analysis)} 个")
    print(f"  类型分布：enum={len(enum_keys)}, mock={len(mock_keys)}")
    print(f"  变化性分布：dynamic={len(dynamic_keys)}, static={len(analysis) - len(dynamic_keys)}")
    print(f"  来源分布：judgment={len(judgment_keys)}, slot={len(slot_keys)}, other={len(other_keys)}")

    print(f"\n  📌 可枚举（enum）信号：")
    for k in enum_keys:
        vals = analysis[k]["enum_values"]
        print(f"    {k}：{vals}")

    print(f"\n  🔀 需要 mock 的信号：")
    for k in mock_keys:
        src = analysis[k]["source"]
        var = analysis[k]["variability"]
        exs = analysis[k]["examples"]
        print(f"    {k}（{src}, {var}）示例：{exs[:3]}")

    if dynamic_keys:
        print(f"\n  ⚡ 动态信号（session 内可变）：")
        for k in dynamic_keys:
            print(f"    {k}")

    # ── 构造草稿 ──
    draft = build_draft(
        args.sop_id, analysis, graph,
        rows=rows, excel_path=excel_path,
        first_signal_text=first_signal_text,
    )

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = TMP_DIR / f"{args.sop_id}_signal_rules_draft.json"
    with open(draft_path, "w", encoding="utf-8") as f:
        json.dump(strip_runtime_signal_summary(draft), f, ensure_ascii=False, indent=2)
    print(f"\n✅ 草稿已写出：{draft_path}")

    # ── LLM 评审 Prompt ──
    if args.llm_review or mock_keys:
        review_prompt = build_llm_review_prompt(args.sop_id, analysis, graph)
        if review_prompt:
            review_prompt_path = TMP_DIR / f"{args.sop_id}_signal_review_prompt.txt"
            review_prompt_path.write_text(review_prompt, encoding="utf-8")
            print(f"\n📋 LLM 评审 Prompt 已写出：{review_prompt_path}")
            print(f"   请将此文件内容发给 LLM，将其 JSON 回复中的 mock_rule 填入草稿，")
            print(f"   人工确认无误后，将草稿复制为：")
            print(f"   data/sop/{args.sop_id}_signal_rules.json（正式入库）")

    # ── 正式入库检查 ──
    formal_path = SOP_GRAPH_DIR / f"{args.sop_id}_signal_rules.json"
    if formal_path.exists():
        print(f"\nℹ️  正式信号规则已存在：{formal_path}")
        print(f"   如需更新，请手动替换。")
    else:
        print(f"\n⏳ 下一步：")
        print(f"   1. 审查草稿：{draft_path}")
        print(f"   2. （可选）用 LLM 评审补全 mock_rule")
        print(f"   3. 人工确认后，将草稿复制/重命名为：")
        print(f"      data/sop/{args.sop_id}_signal_rules.json")


if __name__ == "__main__":
    main()
