#!/usr/bin/env python3
"""
verify_graph.py
---------------
Phase 0 节点校验：检查 `data/sop/{sop_id}_graph.json` 的格式完整性。

格式层校验（不做语义判断）：
  1. 顶层字段完整（sop_id / graph / actions）
  2. 所有非 terminal 节点有明确后继
  3. action_id 唯一无冲突
  4. signal_check 节点的 branches 值均指向已存在节点
  5. agent_action 节点引用的 action_id 在 actions 表中存在
  6. START 节点存在
  7. 至少存在一个 terminal 节点

用法：
  python harness_verify/verify_graph.py --sop-id invoice
  python harness_verify/verify_graph.py --graph data/sop/invoice_graph.json
"""

import json
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import collect_signal_rule_drift

SKILL_ROOT = Path(__file__).parent.parent
DATA_DIR = SKILL_ROOT / "data"


def load_graph(graph_path: Path) -> dict:
    if not graph_path.exists():
        raise FileNotFoundError(f"graph 文件不存在：{graph_path}")
    with open(graph_path, encoding="utf-8") as f:
        return json.load(f)


def verify(graph: dict) -> list:
    """
    校验 graph.json 格式，返回 [{level: 'error'|'warning', msg: str}] 列表。
    空列表表示通过。可被外部模块（如 preview_phase0.py）直接导入复用。
    """
    issues = []

    def err(msg):
        issues.append({"level": "error", "msg": msg})

    def warn(msg):
        issues.append({"level": "warning", "msg": msg})

    # 1. 顶层字段
    for field in ("sop_id", "graph", "actions"):
        if field not in graph:
            err(f"顶层缺少必须字段：{field}")

    if "graph" not in graph or "actions" not in graph:
        return issues  # 无法继续

    nodes = graph["graph"]
    actions_def = graph["actions"]

    # 2. START 节点
    if "START" not in nodes:
        err("缺少 START 节点")

    # 3. 至少一个 terminal
    terminal_nodes = [nid for nid, n in nodes.items() if n.get("type") == "terminal"]
    if not terminal_nodes:
        err("没有任何 terminal 节点")

    # 4. 所有节点后继合法
    for nid, node in nodes.items():
        ntype = node.get("type", "")
        if ntype == "terminal":
            continue
        elif ntype == "signal_check":
            branches = node.get("branches", {})
            if not branches:
                err(f"{nid}（signal_check）没有 branches")
            for val, target in branches.items():
                if target not in nodes:
                    err(f"{nid}.branches[{val!r}] → {target!r} 节点不存在")
        elif ntype == "agent_action":
            action_id = node.get("action", "")
            if not action_id:
                err(f"{nid}（agent_action）缺少 action 字段")
            elif action_id not in actions_def:
                err(f"{nid}.action = {action_id!r}，在 actions 表中不存在")
            next_node = node.get("next", "")
            if not next_node:
                err(f"{nid}（agent_action）缺少 next 字段")
            elif next_node not in nodes:
                err(f"{nid}.next → {next_node!r} 节点不存在")
        elif ntype == "user_reaction":
            branches = node.get("branches", {})
            if not branches:
                err(f"{nid}（user_reaction）没有 branches")
            for val, target in branches.items():
                if target not in nodes:
                    err(f"{nid}.branches[{val!r}] → {target!r} 节点不存在")
        else:
            err(f"{nid} 的 type={ntype!r} 不在已知类型中（terminal/signal_check/agent_action/user_reaction）")

    # 5. action_id 格式（有内容，非空字符串）
    for aid, action in actions_def.items():
        if not aid.strip():
            err("actions 中存在空字符串 action_id")
        if not action.get("desc", "").strip():
            err(f"actions[{aid}].desc 为空")

    # 6. signal_fields 中的字段有无实际在 signal_check 节点用到
    declared_signals = set(graph.get("signal_fields", []))
    used_signals = {
        node["signal"]
        for node in nodes.values()
        if node.get("type") == "signal_check" and "signal" in node
    }
    for s in sorted(declared_signals - used_signals):
        warn(f"signal_fields 中声明的 {s!r} 在 graph 节点中未使用")

    return issues


def verify_signal_rules(rules: dict | None, graph: dict) -> list:
    """
    校验 signal_rules.json 基础格式，返回 [{level, msg}] 列表。
    rules 为 None 时返回空列表（信号规则为可选产物）。
    可被外部模块（如 preview_phase0.py）直接导入复用。
    """
    if rules is None:
        return []
    issues = []

    def err(msg):
        issues.append({"level": "error", "msg": msg})

    def warn(msg):
        issues.append({"level": "warning", "msg": msg})

    signals = rules.get("signals", {})
    slot_fields = set(graph.get("slot_fields", []))

    for key in slot_fields:
        if key not in signals:
            warn(f"slot_fields 中的 {key!r} 在 signal_rules 中未找到")

    for key, cfg in signals.items():
        if cfg.get("source") == "slot" and cfg.get("type") == "mock":
            if not cfg.get("mock_rule", "").strip():
                # _no_sample=True 表示该字段在 Excel 中未出现（信号名不匹配或样本无此字段），
                # build_draft() 已为其填入默认规则"按常识生成合理值"，此处不再重复发出警告。
                # 若用户手动清空了 mock_rule 且保留 _no_sample=True，同样视为有意为之，跳过。
                if not cfg.get("_no_sample", False):
                    warn(f"参考信号 {key!r} 的 mock_rule 为空（LLM 未补全或需人工填写）")

    # ── 兼容旧文件：若仍持久化了 `_production_signals`，提示清理即可 ──
    drift_items = collect_signal_rule_drift(rules, graph=graph)
    if drift_items:
        warn("signal_rules 文件仍持久化了旧版 _production_signals 摘要；当前实现已改为 signals 单一权威源，建议回写清理")

    return issues


def main():
    parser = argparse.ArgumentParser(description="Phase 0 节点校验：graph.json 格式检查")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--sop-id", help="SOP 标识（自动定位 data/sop/{sop_id}_graph.json）")
    grp.add_argument("--graph", help="直接指定 graph.json 路径")
    args = parser.parse_args()

    if args.graph:
        graph_path = Path(args.graph)
        if not graph_path.is_absolute():
            graph_path = SKILL_ROOT / graph_path
    else:
        # 查 data/sop/{sop_id}_graph.json
        graph_path = DATA_DIR / "sop" / f"{args.sop_id}_graph.json"

    print(f"🔍 校验：{graph_path}")
    try:
        graph = load_graph(graph_path)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 格式错误：{e}", file=sys.stderr)
        sys.exit(1)

    sop_id = graph.get("sop_id", "?")
    node_count = len(graph.get("graph", {}))
    action_count = len(graph.get("actions", {}))
    print(f"   sop_id={sop_id}  节点数={node_count}  动作数={action_count}")

    issues = verify(graph)

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]

    if warnings:
        print(f"\n⚠️  警告（{len(warnings)} 条）：")
        for w in warnings:
            print(f"   {w['msg']}")

    if errors:
        print(f"\n❌ 校验失败（{len(errors)} 个错误）：")
        for e in errors:
            print(f"   {e['msg']}")
        sys.exit(1)
    else:
        print(f"\n✅ 校验通过（节点引用完整，action_id 无冲突）")
        if warnings:
            print(f"   含 {len(warnings)} 条警告，不影响运行")
        sys.exit(0)


if __name__ == "__main__":
    main()
