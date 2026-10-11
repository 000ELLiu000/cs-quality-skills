#!/usr/bin/env python3
"""
enumerate_paths.py
------------------
从 `data/sop/{sop_id}_graph.json` 枚举所有 SOP 路径，输出路径骨架 JSONL。

每条骨架包含：
  - path_id:       P001, P002 ...
  - sop_id:        SOP 标识（如 invoice）
  - scene:         场景描述
  - signals:       { 信号字段: 取值 }（该路径经过的所有信号判断结果）
  - actions:       [ { action_id, desc, 关联动作, 共识动作, outbound } ]
  - turns:         对话轮数（= agent_action 节点数）
  - has_outbound:  是否包含外呼动作

用法:
  python scripts/enumerate_paths.py --sop-id invoice
  python scripts/enumerate_paths.py --sop-id invoice --graph data/sop/invoice_graph.json
"""

import json
import sys
import argparse
from pathlib import Path


def load_graph(graph_path: Path) -> dict:
    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误：找不到 graph 文件：{graph_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"错误：graph 文件 JSON 格式有误：{e}", file=sys.stderr)
        sys.exit(1)


def dfs(node_id: str, graph_nodes: dict, actions_def: dict,
        current_signals: dict, current_actions: list, all_paths: list,
        visited: set | None = None):
    """
    DFS 遍历节点图，收集所有从 START 到 END 的路径。
    visited 集合用于防止环路导致无限递归。
    """
    if visited is None:
        visited = set()
    if node_id in visited:
        return  # 跳过环路，防止无限递归
    visited = visited | {node_id}

    node = graph_nodes[node_id]
    ntype = node["type"]

    if ntype == "terminal":
        all_paths.append({
            "signals": dict(current_signals),
            "actions": list(current_actions),
        })
        return

    elif ntype == "signal_check":
        signal = node["signal"]
        # 按目标节点去重：多个枚举值指向同一节点时只走一次
        # 信号槽记录为列表（生产时随机选一个具体值）
        seen_targets: dict = {}
        for val, next_id in node["branches"].items():
            seen_targets.setdefault(next_id, []).append(val)

        for next_id, vals in seen_targets.items():
            new_signals = dict(current_signals)
            new_signals[signal] = vals if len(vals) > 1 else vals[0]
            dfs(next_id, graph_nodes, actions_def, new_signals, current_actions, all_paths, visited)

    elif ntype == "agent_action":
        action_id = node["action"]
        action = actions_def[action_id]
        new_actions = current_actions + [{
            "action_id": action_id,
            "desc": action["desc"],
            "关联动作": action.get("action_tag", "无"),
            "共识动作": "无",
            "outbound": action.get("outbound", False),
        }]
        # 将该动作的 required_slots 合并进 signals（填槽信号，mock 用）
        new_signals = dict(current_signals)
        for slot in action.get("required_slots", []):
            if slot not in new_signals:
                new_signals[slot] = "__mock__"
        dfs(node["next"], graph_nodes, actions_def, new_signals, new_actions, all_paths, visited)

    elif ntype == "user_reaction":
        for reaction, next_id in node["branches"].items():
            new_signals = dict(current_signals)
            # 用 "用户反应@<上一个action_id>" 记录用户反应取值
            last_action_id = current_actions[-1]["action_id"] if current_actions else "?"
            new_signals[f"用户反应@{last_action_id}"] = reaction
            dfs(next_id, graph_nodes, actions_def, new_signals, current_actions, all_paths, visited)


def deduplicate_paths(all_paths: list) -> list:
    """去重：信号组合+动作序列完全相同的路径只保留一条。"""
    seen = set()
    unique = []
    for p in all_paths:
        key = json.dumps(p, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def build_skeleton(path_id: str, path_data: dict, graph: dict) -> dict:
    actions = path_data["actions"]
    has_outbound = any(a["outbound"] for a in actions)
    turns = len(actions)
    return {
        "path_id": path_id,
        "sop_id": graph.get("sop_id", "unknown"),
        "scene": graph.get("scene", ""),
        "signals": path_data["signals"],
        "actions": actions,
        "turns": turns,
        "has_outbound": has_outbound,
    }


def main():
    parser = argparse.ArgumentParser(description="枚举 SOP 路径骨架")
    parser.add_argument(
        "--sop-id",
        default="invoice",
        help="SOP 标识，用于定位 graph 文件和命名输出文件（默认: invoice）",
    )
    parser.add_argument(
        "--graph",
        default=None,
        help="graph.json 路径（不填则自动按 sop-id 查找）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="输出 JSONL 路径（不填则自动命名为 data/paths/{sop_id}_paths.jsonl）",
    )
    parser.add_argument(
        "--no-dedup",
        dest="dedup",
        action="store_false",
        default=True,
        help="保留所有路径（含重复）",
    )
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent  # = cs-data-factory/

    # 默认路径（按 sop-id 命名）
    if args.graph:
        graph_path = Path(args.graph)
    else:
        graph_path = project_root / f"data/sop/{args.sop_id}_graph.json"
    out_path = Path(args.out) if args.out else \
        project_root / f"data/paths/{args.sop_id}_paths.jsonl"

    if not graph_path.is_absolute():
        graph_path = project_root / graph_path
    if not out_path.is_absolute():
        out_path = project_root / out_path

    print(f"📂 读取图: {graph_path}")
    graph = load_graph(graph_path)
    graph_nodes = graph["graph"]
    actions_def = graph["actions"]

    # DFS 枚举
    all_paths: list = []
    dfs("START", graph_nodes, actions_def, {}, [], all_paths)
    print(f"🔍 原始路径数（含重复）: {len(all_paths)}")

    if args.dedup:
        all_paths = deduplicate_paths(all_paths)
        print(f"✂️  去重后路径数: {len(all_paths)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 保护自定义路径（_custom: true）────────────────────────────────────────
    # 枚举前读取旧文件中所有 CUST_ 路径，枚举后追加回去，防止用户自定义路径丢失
    custom_path_lines = []
    if out_path.exists():
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("_custom"):
                        custom_path_lines.append(line)
                except json.JSONDecodeError:
                    pass
        if custom_path_lines:
            print(f"🔒 发现 {len(custom_path_lines)} 条自定义路径（CUST_xxx），枚举后将自动追加保留")

    with open(out_path, "w", encoding="utf-8") as f:
        for i, path_data in enumerate(all_paths):
            path_id = f"P{i+1:03d}"
            skeleton = build_skeleton(path_id, path_data, graph)
            f.write(json.dumps(skeleton, ensure_ascii=False) + "\n")
        # 追加自定义路径
        for cline in custom_path_lines:
            f.write(cline + "\n")

    total = len(all_paths) + len(custom_path_lines)
    print(f"✅ 骨架已输出: {out_path}  ({len(all_paths)} 条枚举路径"
          + (f" + {len(custom_path_lines)} 条自定义路径，共 {total} 条)" if custom_path_lines else ")"))

    # 摘要
    print("\n--- 路径摘要（前15条）---")
    with open(out_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 15:
                print("  ...")
                break
            p = json.loads(line)
            sig_str = " | ".join(
                f"{k}={v if isinstance(v, str) else '/'.join(v)}"
                for k, v in p["signals"].items()
                if not k.startswith("用户反应")
            )
            react_str = " | ".join(
                f"{k.replace('用户反应@','')}={v}"
                for k, v in p["signals"].items()
                if k.startswith("用户反应")
            )
            action_ids = [a["action_id"] for a in p["actions"]]
            print(f"  {p['path_id']}  turns={p['turns']}  outbound={p['has_outbound']}")
            print(f"    信号: {sig_str}")
            print(f"    反应: {react_str}")
            print(f"    动作: {' → '.join(action_ids)}")


if __name__ == "__main__":
    main()
