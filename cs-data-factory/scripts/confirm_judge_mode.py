#!/usr/bin/env python3
"""
confirm_judge_mode.py
---------------------
Phase 3 Judge 模式确认门槛脚本（强制执行机制）。

**设计目的**：
  防止 Agent 跳过 Phase 3 Step 0"Judge 模式选择"步骤，直接调用 run_judge.py。
  本脚本不带 --mode 时，只打印模式选项后退出（exit 0），不写任何文件。
  Agent 将输出展示给用户，收到明确选择（A/B）后，再带 --mode 重新运行，
  写入 judge_runs/<round>/exec_mode.json，解锁 run_judge.py 的执行权限。

**与 run_judge.py 的联动**：
  run_judge.py 在执行前检查对应 round 目录下是否存在 exec_mode.json。
  不存在则拒绝运行，并提示执行本脚本写入模式选择。

用法:
  # 阶段 1：展示选项（不写文件，供 Agent 展示给用户）
  python scripts/confirm_judge_mode.py --round tuning_round_1

  # 阶段 2：用户选择后写入模式（解锁 Judge 执行）
  python scripts/confirm_judge_mode.py --round tuning_round_1 --mode A
  python scripts/confirm_judge_mode.py --round tuning_round_1 --mode B --appid <AppId> --model <ModelName>
"""

import json
import os
import sys
import argparse
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SKILL_ROOT = Path(__file__).parent.parent
JUDGE_RUNS_DIR = SKILL_ROOT / "data" / "judge_runs"

MENU = """\
============================================================
⚠️  Phase 3 Judge 模式确认（尚未执行，请将以下内容展示给用户）
============================================================

请选择本轮质检（Judge）的执行模式：

A. SubAgent 模式（推荐）
   导出 prompt 文件 → 调度 SubAgent 执行打分 → collect_subagent_results.py 汇总
   适合：无需额外 AppId 配置，利用当前 Agent 能力打分

B. API 模式
   直接调用 模型 API（需要 AppId 和 Model）→ 结果实时写入
   适合：需要用指定模型（如 your-model）执行质检
   需提供：AppId 和模型名称

============================================================
用户选择后，执行对应命令写入模式选择（解锁 Judge 执行）：

  # 选 A（SubAgent 模式）：
  python scripts/confirm_judge_mode.py \\
      --round {round} --mode A

  # 选 B（API 模式）：
  python scripts/confirm_judge_mode.py \\
      --round {round} --mode B --appid <AppId> --model <ModelName>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Phase 3 Judge 模式确认门槛（写入 exec_mode.json 解锁 run_judge.py）"
    )
    parser.add_argument("--round", required=True,
                        help="质检轮次名（如 tuning_round_1），与 run_judge.py --round 保持一致")
    parser.add_argument("--mode", default="", choices=["A", "B", "a", "b"],
                        help="Judge 模式：A=SubAgent，B=API。不传则只展示选项后退出。")
    parser.add_argument("--appid", default="",
                        help="API 模式专用：模型 API Key")
    parser.add_argument("--model", default="your-model",
                        help="API 模式专用：模型名称（默认 your-model）")
    args = parser.parse_args()

    round_dir = JUDGE_RUNS_DIR / args.round

    # ── 不带 --mode：展示菜单后退出，不写任何文件 ──
    if not args.mode:
        print(MENU.format(round=args.round))
        sys.exit(0)

    mode = args.mode.upper()

    # ── B 模式必须提供 appid ──
    if mode == "B" and not (args.appid or os.environ.get("CS_FACTORY_APPID")):
        print("❌ API 模式（B）需要提供 --appid", file=sys.stderr)
        print(f"   示例：python scripts/confirm_judge_mode.py "
              f"--round {args.round} --mode B --appid <AppId> --model {args.model}",
              file=sys.stderr)
        sys.exit(1)

    # ── 创建 round 目录（run_judge.py 之前可能还不存在）──
    round_dir.mkdir(parents=True, exist_ok=True)

    # ── 防止重复确认 ──
    exec_mode_file = round_dir / "exec_mode.json"
    if exec_mode_file.exists():
        existing = json.loads(exec_mode_file.read_text(encoding="utf-8"))
        print(f"⚠️  exec_mode.json 已存在（mode={existing.get('mode', '?')}）", file=sys.stderr)
        print(f"   如需更改，请先删除 {exec_mode_file} 再重新运行", file=sys.stderr)
        sys.exit(1)

    # ── 写入 exec_mode.json ──
    exec_mode = {
        "mode": "subagent" if mode == "A" else "api",
        "round": args.round,
    }
    if mode == "B":
        exec_mode["model"] = args.model

    exec_mode_file.write_text(
        json.dumps(exec_mode, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if mode == "A":
        print(f"✅ Judge 模式已确认：SubAgent 模式")
        print(f"   exec_mode.json 写入：{exec_mode_file}")
        print(f"   现在可以运行：python judge/scripts/run_judge.py "
              f"--batch <run_dir> --round {args.round} --export-prompts")
    else:
        print(f"✅ Judge 模式已确认：API 模式（model={args.model}）")
        print(f"   exec_mode.json 写入：{exec_mode_file}")
        print(f"   现在可以运行：python judge/scripts/run_judge.py "
              f"--batch <run_dir> --round {args.round} --appid <API_KEY> --model {args.model}")


if __name__ == "__main__":
    main()
