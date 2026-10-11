import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from collections import defaultdict


def resolve_project_path(skill_root: Path, raw_path: str) -> Path:
    """Resolve a project-relative path against the skill root."""
    path = Path(raw_path)
    return path if path.is_absolute() else skill_root / raw_path



def resolve_run_dir(skill_root: Path, raw_run_dir: str) -> tuple[str, Path]:
    """Normalize a run-dir argument so both scripts share the same convention."""
    normalized = raw_run_dir.strip()
    if not Path(normalized).is_absolute():
        normalized_slash = normalized.replace("\\", "/")
        if not normalized_slash.startswith("data/runs/"):
            normalized = f"data/runs/{normalized}"
    return normalized, resolve_project_path(skill_root, normalized)



def task_id_to_path_persona(task_id: str) -> tuple[str, str, str]:
    """Parse task_id into (path_id, persona_id, batch), including legacy custom-batch ids."""
    legacy_match = re.match(r"^(P\d+)_(P\d+)(?:_\d+)?_(\d+)$", task_id)
    if legacy_match:
        return legacy_match.group(1), legacy_match.group(2), legacy_match.group(3)

    parts = task_id.rsplit("_", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return task_id, "", ""



def build_order_signals(path: dict) -> dict:
    """Keep only stable path-level signals for downstream serialization."""
    return {
        key: value
        for key, value in path.get("signals", {}).items()
        if not key.startswith("用户反应@") and value != "__mock__"
    }



def write_prompt_bundle(run_dir: Path, tasks: list[dict]) -> tuple[Path, Path, Path]:
    """Write Phase 1 task index, prompt files, and task_ids.json in one place."""
    run_dir.mkdir(parents=True, exist_ok=True)

    out_file = run_dir / "prompts_todo.jsonl"
    with open(out_file, "w", encoding="utf-8") as f:
        for task in tasks:
            index_record = {k: v for k, v in task.items() if k != "prompt"}
            f.write(json.dumps(index_record, ensure_ascii=False) + "\n")

    subagent_dir = run_dir / "subagent_prompts"
    subagent_dir.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        prompt_file = subagent_dir / f"{task['task_id']}.prompt.txt"
        prompt_file.write_text(task["prompt"], encoding="utf-8")

    task_ids_list = [task["task_id"] for task in tasks]
    ids_file = subagent_dir / "task_ids.json"
    ids_file.write_text(
        json.dumps(task_ids_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return out_file, subagent_dir, ids_file


# ─── Phase 1 共享工具 ───────────────────────────────────────────────────────────


def load_paths_file(paths_file: Path) -> list:
    """Load Phase 0 path skeletons from a jsonl file."""
    if not paths_file.exists():
        print(f"❌ 路径文件不存在：{paths_file}", file=sys.stderr)
        print("   请先执行 Phase 0 初始化（Agent 解析 SOP → enumerate_paths.py）", file=sys.stderr)
        sys.exit(1)

    paths = []
    with open(paths_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                paths.append(json.loads(line))
    return paths


def load_paths_for_sop(skill_root: Path, sop_id: str) -> list:
    """Load `data/paths/{sop_id}_paths.jsonl`."""
    return load_paths_file(skill_root / "data" / "paths" / f"{sop_id}_paths.jsonl")


def load_sop_graph_file(graph_file: Path) -> dict:
    """Load one SOP graph json file."""
    if not graph_file.exists():
        print(f"❌ graph 文件不存在：{graph_file}", file=sys.stderr)
        print(f"   期望路径：data/sop/{graph_file.name}", file=sys.stderr)
        sys.exit(1)

    with open(graph_file, encoding="utf-8") as f:
        return json.load(f)


def load_sop_graph_for_sop(skill_root: Path, sop_id: str) -> dict:
    """Load `data/sop/{sop_id}_graph.json`."""
    return load_sop_graph_file(skill_root / "data" / "sop" / f"{sop_id}_graph.json")


def load_signal_rules_for_sop(skill_root: Path, sop_id: str, graph: dict | None = None) -> dict | None:
    """Load and normalize the persisted signal_rules file for one sop_id."""
    rules_file = skill_root / "data" / "sop" / f"{sop_id}_signal_rules.json"
    return load_signal_rules_file(rules_file, graph=graph)


def load_personas(persona_file: Path) -> dict:
    """
    从 persona.md 解析 Persona 定义，返回 {P1: {name, desc, features}, ...}
    """
    text = persona_file.read_text(encoding="utf-8")

    persona_section_start = text.find("### P")
    if persona_section_start == -1:
        print("⚠️  persona.md 中未找到 ### P 开头的 Persona 块", file=sys.stderr)
        sys.exit(1)

    persona_content = text[persona_section_start:]
    stop_match = re.search(r'\n## [^#]', persona_content)
    if stop_match:
        persona_content = persona_content[:stop_match.start()]

    personas = {}
    blocks = re.split(r'\n(?=### P\d)', persona_content)
    for block in blocks:
        m = re.match(r'### (P\d+)：(.+)', block.strip())
        if not m:
            continue
        pid = m.group(1)
        full_name = f"{pid}：{m.group(2).strip()}"

        patience = re.search(r'\*\*耐心\*\*：(.+)', block)
        coop = re.search(r'\*\*合作度\*\*：(.+)', block)
        lang = re.search(r'\*\*语言特征\*\*：(.+)', block)
        behavior = re.search(r'\*\*典型行为\*\*：(.+)', block)

        desc_parts = []
        if patience:
            desc_parts.append(f"耐心：{patience.group(1).strip()}")
        if coop:
            desc_parts.append(f"合作度：{coop.group(1).strip()}")

        features_parts = []
        if lang:
            features_parts.append(lang.group(1).strip())
        if behavior:
            features_parts.append(behavior.group(1).strip())
        examples = re.findall(r'「(.+?)」', block)
        if examples:
            features_parts.append("典型说法：" + "、".join(f"「{e}」" for e in examples[:2]))

        dialog_blocks = re.findall(r'```\n([\s\S]+?)\n```', block)
        if dialog_blocks:
            all_previews = []
            for blk in dialog_blocks:
                lines = blk.strip().splitlines()
                preview_lines = []
                for line in lines:
                    line = line.strip()
                    if line:
                        preview_lines.append(line)
                    if len(preview_lines) >= 8:
                        break
                all_previews.append("\n".join(preview_lines))
            examples_str = "\n\n".join(all_previews)
        else:
            examples_str = "、".join(f"「{e}」" for e in examples[:4]) if examples else ""

        personas[pid] = {
            "name": full_name,
            "desc": "；".join(desc_parts) if desc_parts else full_name,
            "features": "；".join(features_parts) if features_parts else full_name,
            "examples": examples_str,
        }

    if not personas:
        print("⚠️  persona.md 解析结果为空，请检查格式", file=sys.stderr)
        sys.exit(1)
    return personas


def extract_prompt_template(expand_prompt_md: str) -> str:
    """Extract the first fenced prompt template block from expand-prompt.md."""
    lines = expand_prompt_md.splitlines()
    start_line = None
    for i, line in enumerate(lines):
        if line.strip() == "```":
            start_line = i
            break
    if start_line is None:
        raise ValueError("expand-prompt.md 中未找到 ``` 开始标记")

    end_line = None
    for i in range(start_line + 1, len(lines)):
        if lines[i].strip() == "```":
            end_line = i
            break
    if end_line is None:
        raise ValueError("expand-prompt.md 中未找到 ``` 结束标记")

    return "\n".join(lines[start_line + 1:end_line]).strip()


def compute_seq_map(generated_dir: Path) -> dict:
    """Build the current max batch sequence for each path/persona pair from generated data."""
    seq_map = {}
    if not generated_dir.exists():
        return seq_map

    pattern = re.compile(r'"会话id"\s*:\s*"gen_([A-Z0-9]+)_(P\d+)_(\d+)(?:_s\d+)?(?:_t\d+)?"')
    for file_path in generated_dir.glob("*.jsonl"):
        with open(file_path, encoding="utf-8") as fh:
            for line in fh:
                m = pattern.search(line)
                if m:
                    key = f"{m.group(1)}_{m.group(2)}"
                    seq = int(m.group(3))
                    seq_map[key] = max(seq_map.get(key, 0), seq)
    return seq_map


def next_seq(seq_map: dict, path_id: str, persona_id: str) -> str:
    """Allocate the next three-digit batch sequence for one path/persona pair."""
    key = f"{path_id}_{persona_id}"
    current = seq_map.get(key, 0)
    new_seq = current + 1
    seq_map[key] = new_seq
    return f"{new_seq:03d}"


def build_signal_mock_rules(signal_rules: dict | None, path: dict, graph: dict) -> str:
    """
    构造注入 prompt 的信号 Mock 规则文本块（放在骨架之后）。

    规则优先级（严格遵守）：
    1. 骨架已锁定的判断信号值 → 不得更改，直接用于 mocked_signals.判断信号
    2. 参考信号（slot_fields + 从 other 提升的动态信号）→ 按 mock_rule 生成，全 session 保持一致
    3. 骨架中有明确值的同名信号 → 即使在 slot_fields 中，也优先以骨架值为准

    特殊类型：outbound_expand=true 的判断信号
      - 这类信号既是判断信号（结论值由骨架锁定，影响路径走向），也需要 mock 展开（在外呼触发轮次
        的 outbound_update 字段里生成完整的客服-商家对话文本）
      - 结论值写入 mocked_signals.判断信号（与普通判断信号一致）
      - 展开文本写入 outbound_update（进入最终「外呼信号」列），而非 mocked_signals
      - 生成规则在独立的「外呼记录展开规则」块中呈现

    signal_rules 为 None 时返回空字符串（向后兼容）。
    """
    if signal_rules is None:
        return ""

    signal_rules = normalize_signal_rules(signal_rules, graph=graph)
    signals_cfg = signal_rules.get("signals", {})
    signal_fields = set(graph.get("signal_fields", []))

    slot_fields = set(graph.get("slot_fields", []))
    for key, cfg in signals_cfg.items():
        if cfg.get("source") == "slot":
            slot_fields.add(key)

    locked_signals = {}
    for key, value in path.get("signals", {}).items():
        if key.startswith("用户反应@"):
            continue
        if key in signal_fields:
            locked_signals[key] = value

    slot_mock_lines = []
    for key in slot_fields:
        if key in locked_signals:
            continue
        cfg = signals_cfg.get(key, {})
        mock_rule = cfg.get("mock_rule", "").strip()
        sig_type = cfg.get("type", "mock")
        enum_values = cfg.get("enum_values", [])

        if sig_type == "enum" and enum_values:
            values_str = "、".join(f"「{value}」" for value in enum_values)
            slot_mock_lines.append(f"- {key}：从以下枚举值中选一个 → {values_str}")
        elif mock_rule:
            slot_mock_lines.append(f"- {key}：{mock_rule}")
        else:
            slot_mock_lines.append(f"- {key}：请按常识生成合理值（无明确规则，参考场景上下文）")

    dynamic_lines = []
    for key, cfg in signals_cfg.items():
        if cfg.get("variability") == "dynamic" and key in signal_fields:
            mock_rule = cfg.get("mock_rule", "").strip()
            if mock_rule:
                dynamic_lines.append(f"- {key}：{mock_rule}（随对话推进动态更新，不列入 mocked_signals）")

    outbound_expand_lines = []
    for key, locked_val in locked_signals.items():
        cfg = signals_cfg.get(key, {})
        if not cfg.get("outbound_expand"):
            continue
        mock_rule = cfg.get("mock_rule", "").strip()
        conclusion = locked_val[0] if isinstance(locked_val, list) else str(locked_val)
        if mock_rule:
            outbound_expand_lines.append(
                f"- **{key}**（结论已锁定为「{conclusion}」）：{mock_rule}"
            )
        else:
            outbound_expand_lines.append(
                f"- **{key}**（结论已锁定为「{conclusion}」）：请按结论展开为完整的客服-商家对话文本"
            )

    if not (slot_mock_lines or dynamic_lines or outbound_expand_lines):
        return ""

    lines = [
        "## 信号 Mock 规则（骨架优先）",
        "",
        "> **重要原则**：",
        "> 1. 骨架「当前场景信号（已由SOP路径锁定）」中的判断信号值，不得更改，直接用于 `mocked_signals.判断信号`",
        "> 2. 若骨架中已明确给出某信号的具体值，即使该信号也在「参考信号」中，也以骨架值为准",
        "> 3. 以下 mock 规则仅适用于需要你自行 mock 的参考信号",
        "",
    ]

    if slot_mock_lines:
        lines.append("**参考信号 mock 规则**（在生成对话前先 mock 好，全 session 保持一致）：")
        lines.extend(slot_mock_lines)
        lines.append("")

    if dynamic_lines:
        lines.append("**动态信号说明**（session 内可变，无需提前 mock，随对话自然演进）：")
        lines.extend(dynamic_lines)
        lines.append("")

    if outbound_expand_lines:
        lines += [
            "**外呼记录展开规则**（判断信号，结论已由骨架锁定，但需在 `outbound_update` 里展开为完整对话文本）：",
            ">",
            "> 这类信号的「结论值」已确定（影响路径走向），你只需要在该检查点触发外呼的那一轮客服话术里，",
            "> 将 `outbound_update` 字段填写为完整的客服-商家通话记录（多行对话格式），不填入 `mocked_signals`。",
            "> `mocked_signals.判断信号` 中仍然写结论值（如「商家同意」），不写展开文本。",
            "",
        ]
        lines.extend(outbound_expand_lines)
        lines.append("")

    return "\n".join(lines) + "\n"


def build_path_content(path: dict, graph: dict) -> str:
    """
    构建路径骨架文本。
    - 判断信号（signal_fields）：SOP 决策用，值已锁定，列在骨架顶部供模型理解上下文
    - 用户反应：内嵌在动作序列中，每个 agent_action 之后紧跟用户反应节点
    - 话术参考：直接内嵌在每步骨架中，不单独列模块
    - 不再列订单金额/商家名/时间等话术槽变量（无占位符的信号无需 mock 提示）
    """
    actions_map = graph.get("actions", {})
    signal_fields = set(graph.get("signal_fields", []))

    locked_signals = []
    for key, value in path["signals"].items():
        if key.startswith("用户反应@"):
            continue
        if key in signal_fields:
            if isinstance(value, list):
                locked_signals.append(f"  {key}：从 {value} 中选一个（选定后全程不变）")
            else:
                locked_signals.append(f"  {key}：{value}")

    lines = [f"路径ID: {path['path_id']}"]
    lines.append(f"含外呼: {'是' if path['has_outbound'] else '否'}")
    if locked_signals:
        lines.append("")
        lines.append("【当前场景信号（已由SOP路径锁定，不得更改）】")
        lines.extend(locked_signals)

    lines += [
        "",
        "【对话检查点说明】",
        "以下列出本次对话必须经过的关键检查点，每个检查点描述一个必须发生的实质性行为或事件。",
        "  约束的是：对话必须按检查点顺序推进；每个检查点描述的客服行为和用户态度必须真实发生；分支走向不可更改。",
        "  不约束的是：到达每个检查点之前，用户可以有任意多句铺垫、追问、情绪表达；对话总轮数不固定，自然推进即可。",
        "  特别要求：每个「用户态度锚点」处，用户应用至少2条连续消息表达态度（连发、追问、情绪递进），尤其是情绪化Persona；一句话收场会失真。",
        "",
        "【对话检查点】",
    ]

    user_reactions = {}
    for key, value in path["signals"].items():
        if key.startswith("用户反应@"):
            action_id = key.split("@")[1]
            user_reactions[action_id] = value

    for i, act in enumerate(path["actions"], 1):
        aid = act["action_id"]
        action_def = actions_map.get(aid, {})
        examples = action_def.get("speech_examples", [])

        lines.append(f"  [检查点{i}] 客服实质：{act['desc']}")
        if examples:
            lines.append(f"    话术参考：「{examples[0]}」")
        lines.append(f"    话术关联动作: {act['关联动作']}")
        lines.append(f"    话术共识动作: {act['共识动作']}")

        if aid in user_reactions:
            reaction = user_reactions[aid]
            lines.append(f"  → [用户态度锚点] 用户对上一步的实质态度锁定为「{reaction}」（用户可以用多句话、多种方式表达这个态度，不限一句）")

    return "\n".join(lines)


def build_prompt(
    path: dict,
    persona_id: str,
    persona: dict,
    batch: str,
    graph: dict,
    prompt_template: str,
    speech_guidelines: str,
    emotion_guidelines: str = "",
    signal_mock_rules: str = "",
    output_count: int = 5,
) -> str:
    """Render one Phase 1 prompt from the shared template inputs."""
    path_content = build_path_content(path, graph)

    prompt = prompt_template
    prompt = prompt.replace("{PATH_CONTENT}", path_content)
    prompt = prompt.replace("{PERSONA_NAME}", persona["name"])
    prompt = prompt.replace("{PERSONA_DESC}", persona["desc"])
    prompt = prompt.replace("{PERSONA_FEATURES}", persona["features"])
    examples = persona.get("examples", "")
    prompt = prompt.replace("{PERSONA_EXAMPLES}", examples if examples else "")
    prompt = prompt.replace("{SPEECH_GUIDELINES}", f"以下为客服话术规范，生成所有话术时必须严格遵守：\n\n{speech_guidelines}")
    prompt = prompt.replace("{SIGNAL_MOCK_RULES}", signal_mock_rules)
    if emotion_guidelines:
        rendered_emotion = (
            "## 情绪处理规范（每条客服回复生成前必须执行）\n\n"
            + emotion_guidelines
            + "\n\n"
        )
    else:
        rendered_emotion = ""
    prompt = prompt.replace("{EMOTION_GUIDELINES}", rendered_emotion)
    prompt = prompt.replace("{PATH_ID}", path["path_id"])
    prompt = prompt.replace("{PERSONA_ID}", persona_id)
    prompt = prompt.replace("{BATCH}", batch)
    prompt = prompt.replace("{OUTPUT_COUNT}", str(output_count))
    return prompt


# ─── signal_rules 归一化工具 ───────────────────────────────────────────────────


SIGNAL_RULES_DESCRIPTION = (
    "信号规则文件（v2.0，含信号模板和补集信号池）。字段说明：\n"
    "  source: judgment（判断信号）/ slot（参考信号，含从 other 提升的动态信号）/ other（补集信号，值从真实样本池轮询取用）\n"
    "  type: enum（可枚举）/ mock（需规则生成）\n"
    "  variability: static（session 内固定）/ dynamic（session 内可变）\n"
    "  mock_rule: type=mock 时的生成规则（LLM 评审后填写）\n"
    "  enum_values: type=enum 时的合法值列表\n"
    "  signal_template: 从真实样本提取的信号模板（保留 section headers + key 顺序，值为占位符）\n"
    "  other_signal_pool: 补集信号值池（list[dict]，每个 dict 是一个 session 的完整补集信号值集合）\n"
    "  _production_signals: 运行时从 signals 自动派生的摘要，不在文件中持久化。\n"
    "建议流程：先在 data/tmp/{sop_id}_signal_rules_draft.json 人工审核，再入库到 data/sop/{sop_id}_signal_rules.json。"
)


def canonicalize_signal_rules_metadata(signal_rules: dict | None) -> dict | None:
    """Normalize persisted signal_rules metadata shared by draft and formal files."""
    if signal_rules is None:
        return None

    normalized = deepcopy(signal_rules)
    normalized["_format_version"] = "2.0"
    normalized["_description"] = SIGNAL_RULES_DESCRIPTION
    return normalized


def _copy_signal_cfg_fields(cfg: dict | None) -> dict:
    """Extract the runtime summary fields derived from the authoritative signals table."""
    cfg = cfg or {}
    enum_values = cfg.get("enum_values", [])
    if not isinstance(enum_values, list):
        enum_values = list(enum_values) if enum_values else []
    return {
        "type": cfg.get("type", ""),
        "variability": cfg.get("variability", ""),
        "enum_values": list(enum_values),
        "mock_rule": cfg.get("mock_rule", "") or "",
    }



def build_production_signals(signals: dict, graph: dict | None = None) -> dict:
    """
    Rebuild the compatibility summary view from the authoritative signals table.

    约定：
    - `signals` 是唯一权威数据源
    - `_production_signals` 是派生摘要，用于 HTML/人工确认
    - judgment 集合以 graph.signal_fields 为准
    - slot 集合以 graph.slot_fields 为准，并补充 signals 中 source=slot 的动态提升字段
    """
    graph = graph or {}
    signals = signals or {}

    graph_judgment_keys = list(dict.fromkeys(graph.get("signal_fields", [])))
    graph_slot_keys = list(dict.fromkeys(graph.get("slot_fields", [])))

    if graph_judgment_keys:
        judgment_keys = graph_judgment_keys
    else:
        judgment_keys = [
            key for key, cfg in signals.items()
            if cfg.get("source") == "judgment"
        ]

    promoted_slot_keys = [
        key for key, cfg in signals.items()
        if cfg.get("source") == "slot" and key not in graph_slot_keys
    ]

    if graph_slot_keys:
        slot_keys = graph_slot_keys + [key for key in promoted_slot_keys if key not in graph_slot_keys]
    else:
        slot_keys = [
            key for key, cfg in signals.items()
            if cfg.get("source") == "slot"
        ]

    judgment = {
        key: _copy_signal_cfg_fields(signals.get(key, {}))
        for key in judgment_keys
    }
    slot = {
        key: _copy_signal_cfg_fields(signals.get(key, {}))
        for key in slot_keys
    }

    other_count = sum(
        1
        for key, cfg in signals.items()
        if cfg.get("source") == "other" and key not in judgment and key not in slot
    )

    return {
        "judgment": judgment,
        "slot": slot,
        "other_count": other_count,
    }



def collect_signal_rule_drift(signal_rules: dict | None, graph: dict | None = None) -> list[dict]:
    """
    Compare stored `_production_signals` with the derived view rebuilt from `signals`.

    返回结构化漂移信息，供 verify / 诊断输出使用。
    若文件本身未持久化 `_production_signals`，视为采用“signals 单一权威源”模式，
    此时不报告摘要漂移。
    """
    if signal_rules is None:
        return []

    if "_production_signals" not in signal_rules:
        return []

    signals = signal_rules.get("signals", {}) or {}
    stored = signal_rules.get("_production_signals", {}) or {}
    expected = build_production_signals(signals, graph=graph)

    drifts = []

    for category in ("judgment", "slot"):
        stored_cat = stored.get(category, {}) if isinstance(stored.get(category, {}), dict) else {}
        expected_cat = expected.get(category, {})
        all_keys = sorted(set(stored_cat.keys()) | set(expected_cat.keys()))

        for sig_key in all_keys:
            if sig_key not in stored_cat:
                drifts.append({
                    "kind": "missing_in_summary",
                    "category": category,
                    "signal": sig_key,
                })
                continue
            if sig_key not in expected_cat:
                drifts.append({
                    "kind": "extra_in_summary",
                    "category": category,
                    "signal": sig_key,
                })
                continue

            for field in ("type", "variability", "enum_values", "mock_rule"):
                stored_value = stored_cat[sig_key].get(field, [] if field == "enum_values" else "")
                expected_value = expected_cat[sig_key].get(field, [] if field == "enum_values" else "")
                if stored_value != expected_value:
                    drifts.append({
                        "kind": "field_mismatch",
                        "category": category,
                        "signal": sig_key,
                        "field": field,
                        "stored": stored_value,
                        "expected": expected_value,
                    })

    stored_other_count = stored.get("other_count")
    expected_other_count = expected.get("other_count")
    if stored_other_count != expected_other_count:
        drifts.append({
            "kind": "other_count_mismatch",
            "stored": stored_other_count,
            "expected": expected_other_count,
        })

    return drifts



def normalize_signal_rules(signal_rules: dict | None, graph: dict | None = None) -> dict | None:
    """
    Attach the runtime-only `_production_signals` summary derived from `signals`.

    `signals` 是唯一权威数据源；`_production_signals` 仅在读取/预览时按需派生。
    该函数不会修改入参对象，而是返回带运行时摘要的副本。
    """
    if signal_rules is None:
        return None

    normalized = deepcopy(signal_rules)
    normalized["_production_signals"] = build_production_signals(
        normalized.get("signals", {}),
        graph=graph,
    )
    return normalized



def strip_runtime_signal_summary(signal_rules: dict | None) -> dict | None:
    """Drop the runtime-only `_production_signals` field before persisting signal_rules."""
    if signal_rules is None:
        return None

    stripped = deepcopy(signal_rules)
    stripped.pop("_production_signals", None)
    return stripped



def load_signal_rules_file(
    rules_path: Path,
    graph: dict | None = None,
    auto_repair: bool = False,
) -> dict | None:
    """Load a persisted signal_rules file and optionally rewrite it in single-authority form."""
    if not rules_path.exists():
        return None

    raw_rules = json.loads(rules_path.read_text(encoding="utf-8"))
    persisted_rules = canonicalize_signal_rules_metadata(
        strip_runtime_signal_summary(raw_rules)
    )

    if auto_repair and persisted_rules != raw_rules:
        rules_path.write_text(
            json.dumps(persisted_rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return persisted_rules



def repair_signal_rules_file(rules_path: Path, graph: dict | None = None) -> bool:
    """Rewrite a signal_rules file into single-authority form by dropping runtime summaries."""
    if not rules_path.exists():
        return False

    raw_rules = json.loads(rules_path.read_text(encoding="utf-8"))
    persisted_rules = canonicalize_signal_rules_metadata(
        strip_runtime_signal_summary(raw_rules)
    )
    if persisted_rules == raw_rules:
        return False

    rules_path.write_text(
        json.dumps(persisted_rules, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


# ─── 信号模板与补集信号池工具 ─────────────────────────────────────────────────


def parse_signal_template(signal_text: str) -> str:
    """
    从一条真实样本的「所有信号」文本中提取信号模板。

    将每个 {信号名:值} 中的值替换为占位符 {信号名:__PLACEHOLDER__}，
    保留 section headers（### xxx）和信号 key 的原始顺序。

    返回的模板字符串可用于后续值替换：
      template.replace("{信号名:__PLACEHOLDER__}", "{信号名:实际值}")

    自适应设计：不硬编码任何 section 名称或信号名，
    完全由输入的真实样本决定模板结构。
    """
    lines = signal_text.splitlines()
    template_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            template_lines.append("")
            continue
        if stripped.startswith("###"):
            template_lines.append(stripped)
            continue
        # 匹配 {信号名:值} 格式的行
        if stripped.startswith("{") and ":" in stripped:
            inner = stripped[1:]
            if inner.endswith("}"):
                inner = inner[:-1]
            colon_idx = inner.index(":")
            key = inner[:colon_idx].strip()
            template_lines.append("{" + key + ":__PLACEHOLDER__}")
        else:
            # 非标准行（如 "券码1:" 这样的标签行），原样保留
            template_lines.append(stripped)
    return "\n".join(template_lines)



def extract_signal_keys_ordered(signal_text: str) -> list[str]:
    """
    从一条真实样本的「所有信号」文本中提取所有信号 key，保持原始顺序。
    用于确保模板替换时的 key 遍历顺序与模板一致。
    """
    keys = []
    for line in signal_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and ":" in stripped:
            inner = stripped[1:]
            if inner.endswith("}"):
                inner = inner[:-1]
            colon_idx = inner.index(":")
            key = inner[:colon_idx].strip()
            keys.append(key)
    return keys



def parse_signal_text_full(text: str) -> dict:
    """
    将「所有信号」文本解析为 {key: value} 扁平 dict，**保留完整值**。

    与 analyze_signals.parse_signal_text() 的区别：
    - parse_signal_text() 截断逗号后的附加信息（用于统计分析，只需主值）
    - 本函数保留完整值（用于补集信号池，需要原样还原到模板中）

    例如 {订单是否过期:否,过期时间=2026-03-27 23:59:59}
    → parse_signal_text() 返回 "否"
    → 本函数返回 "否,过期时间=2026-03-27 23:59:59"
    """
    result = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("###"):
            continue
        if stripped.startswith("{") and ":" in stripped:
            inner = stripped[1:]
            if inner.endswith("}"):
                inner = inner[:-1]
            colon_idx = inner.index(":")
            key = inner[:colon_idx].strip()
            value = inner[colon_idx + 1:].strip()
            result[key] = value
    return result



def build_other_signal_pool(
    rows: list[dict],
    other_keys: set[str],
    excel_path=None,
) -> list[dict]:
    """
    从真实样本中按 session 聚合补集信号（source=other, variability=static）的值，
    构建补集信号值池。

    每个 session 取第一行的补集信号值（static 信号同 session 内不变，取哪行都一样）。
    返回 list[dict]，每个 dict 是一个 session 的完整补集信号值集合：
      [{"用户标签": "高价值用户", "门店电话": "空", ...}, ...]

    postprocess.py 在组装「所有信号」时，从此池中 round-robin 取用。

    ⚠️ 值保留策略：使用 parse_signal_text_full() 解析，保留逗号后的附加信息，
    确保补集信号值与真实样本完全一致（如 "否,过期时间=2026-03-27 23:59:59"）。

    参数：
        rows: analyze_signals.load_excel_signals() 的返回值（用于获取 session_id 映射）
        other_keys: source=other 且 variability=static 的信号 key 集合
        excel_path: Excel 文件路径（用于重新读取完整值；为 None 时降级使用 rows 中的截断值）
    """
    if not other_keys:
        return []

    # 优先从 Excel 重新读取完整值（避免 parse_signal_text 的逗号截断问题）
    if excel_path is not None:
        return _build_pool_from_excel(excel_path, other_keys)

    # 降级：使用 rows 中已解析的值（可能被截断，但仍可用）
    session_first_row: dict[str, dict] = {}
    for row in rows:
        sid = row["session_id"]
        if sid not in session_first_row:
            session_first_row[sid] = row["signals"]

    pool = []
    for sid, signals in session_first_row.items():
        entry = {}
        for key in other_keys:
            if key in signals:
                entry[key] = signals[key]
            else:
                entry[key] = "空"
        pool.append(entry)

    return pool



def _build_pool_from_excel(excel_path, other_keys: set[str]) -> list[dict]:
    """从 Excel 直接读取完整值构建补集信号池（内部函数）。"""
    try:
        import openpyxl
    except ImportError:
        import warnings
        warnings.warn(
            "openpyxl 未安装，无法从 Excel 读取完整信号值，补集信号池将为空。"
            "请执行 pip install openpyxl 安装。"
        )
        return []

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    ws = wb.active

    headers = []
    session_first_seen: dict[str, bool] = {}
    pool = []

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
            row_dict.get("会话ID") or row_dict.get("sessionId") or f"row_{i}"
        )
        sid = str(session_id)

        # 每个 session 只取第一行
        if sid in session_first_seen:
            continue
        session_first_seen[sid] = True

        # 用完整值解析
        full_signals = parse_signal_text_full(str(signal_text))
        entry = {}
        for key in other_keys:
            entry[key] = full_signals.get(key, "空")
        pool.append(entry)

    wb.close()
    return pool



def fill_signal_template(
    template: str,
    generated_signals: dict[str, str],
    other_values: dict[str, str],
) -> str:
    """
    将信号模板中的占位符替换为实际值，生成最终的「所有信号」文本。

    替换优先级：
      1. generated_signals（路径信号 + 参考信号，来自骨架 + LLM mock）
      2. other_values（补集信号，来自真实样本池 round-robin）
      3. 未匹配的占位符保留 __PLACEHOLDER__（理论上不应出现）

    参数：
        template: parse_signal_template() 的返回值
        generated_signals: {信号名: 值} — 合并了判断信号和参考信号
        other_values: {信号名: 值} — 当前 session 分配的补集信号值
    """
    result = template
    # 先替换 generated_signals（优先级高）
    for key, value in generated_signals.items():
        placeholder = "{" + key + ":__PLACEHOLDER__}"
        result = result.replace(placeholder, "{" + key + ":" + str(value) + "}")
    # 再替换 other_values
    for key, value in other_values.items():
        placeholder = "{" + key + ":__PLACEHOLDER__}"
        result = result.replace(placeholder, "{" + key + ":" + str(value) + "}")
    return result
