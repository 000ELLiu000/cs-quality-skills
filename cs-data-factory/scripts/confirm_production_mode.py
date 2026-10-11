#!/usr/bin/env python3
"""
confirm_production_mode.py
--------------------------
Phase 2 生产模式确认门槛脚本（强制执行机制）。

**设计目的**：
  防止 Agent 跳过 Step 3.5"生产模式确认"步骤，直接启动生产。
  本脚本不带 --mode 时，只打印模式选项后退出（exit 0），不写任何文件。
  Agent 将输出展示给用户，收到明确选择（A/B）后，再带 --mode 重新运行，
  写入 exec_mode.json，解锁 postprocess.py 的执行权限。

**与 postprocess.py 的联动**：
  postprocess.py 在执行前检查 run_dir/exec_mode.json 是否存在。
  不存在则拒绝运行，并提示执行本脚本写入模式选择。

用法:
  # 阶段 1：展示选项（不写文件，供 Agent 展示给用户）
  python scripts/confirm_production_mode.py --run-dir data/runs/run_YYYYMMDD_NNN

  # 阶段 2：用户选择后写入模式（解锁生产）
  python scripts/confirm_production_mode.py --run-dir data/runs/run_YYYYMMDD_NNN --mode A
  python scripts/confirm_production_mode.py --run-dir data/runs/run_YYYYMMDD_NNN --mode B --appid <AppId> --model <ModelName>
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
DATA_DIR = SKILL_ROOT / "data"

MENU = """\
============================================================
⚠️  Phase 2 生产模式确认（尚未执行，请将以下内容展示给用户）
============================================================

Phase 1 校验通过，准备启动生产。请选择生产模式：

A. SubAgent 模式（默认）
   由 Agent 框架内置模型并发生产，无需额外配置，直接启动

B. API 模式
   指定特定模型（如 your-model）调用模型 API生产
   需提供：AppId（模型 API Key）和模型名称

请回复 A 或 B，或直接回复"A"/"默认"使用 SubAgent 模式。

============================================================
用户选择后，执行对应命令写入模式选择（解锁生产）：

  # 选 A（SubAgent 模式）：
  python scripts/confirm_production_mode.py \\
      --run-dir {run_dir} --mode A

  # 选 B（API 模式）：
  python scripts/confirm_production_mode.py \\
      --run-dir {run_dir} --mode B --appid <AppId> --model <ModelName>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Phase 2 生产模式确认门槛（写入 exec_mode.json 解锁 postprocess.py）"
    )
    parser.add_argument("--run-dir", required=True,
                        help="运行目录（如 data/runs/run_YYYYMMDD_NNN）")
    parser.add_argument("--mode", default="", choices=["A", "B", "a", "b"],
                        help="生产模式：A=SubAgent，B=API。不传则只展示选项后退出。")
    parser.add_argument("--appid", default="",
                        help="API 模式专用：模型 API Key")
    parser.add_argument("--model", default="",
                        help="API 模式专用：模型名称（如 your-model）")
    args = parser.parse_args()

    # ── 规范化 run_dir ──
    raw = args.run_dir
    if not Path(raw).is_absolute() and not raw.startswith("data/runs"):
        raw = f"data/runs/{raw}"
    run_dir = SKILL_ROOT / raw

    # ── 不带 --mode：展示菜单后退出，不写任何文件 ──
    if not args.mode:
        print(MENU.format(run_dir=raw))
        sys.exit(0)

    mode = args.mode.upper()

    # ── B 模式必须提供 appid 和 model ──
    if mode == "B":
        if not (args.appid or os.environ.get("CS_FACTORY_APPID")) or not args.model:
            print("❌ API 模式（B）需要同时提供 --appid 和 --model", file=sys.stderr)
            print(f"   示例：python scripts/confirm_production_mode.py "
                  f"--run-dir {raw} --mode B --appid <AppId> --model your-model",
                  file=sys.stderr)
            sys.exit(1)

    # ── 检查 run_dir 存在 ──
    if not run_dir.exists():
        print(f"❌ run 目录不存在：{run_dir}", file=sys.stderr)
        print(f"   请先运行 generate_prompts.py --confirmed 生成 Prompt 文件", file=sys.stderr)
        sys.exit(1)

    # ── 检查 prompts_todo.jsonl 存在（Phase 1 产物）──
    if not (run_dir / "prompts_todo.jsonl").exists():
        print(f"❌ Phase 1 产物不存在：{run_dir / 'prompts_todo.jsonl'}", file=sys.stderr)
        print(f"   请先运行 generate_prompts.py --confirmed", file=sys.stderr)
        sys.exit(1)

    # ── 防止重复确认（已存在 exec_mode.json 时提示）──
    exec_mode_file = run_dir / "exec_mode.json"
    if exec_mode_file.exists():
        existing = json.loads(exec_mode_file.read_text(encoding="utf-8"))
        existing_mode = existing.get("mode", "?")
        print(f"⚠️  exec_mode.json 已存在（mode={existing_mode}）", file=sys.stderr)
        print(f"   如需更改模式，请先删除 {exec_mode_file} 再重新运行", file=sys.stderr)
        sys.exit(1)

    # ── 写入 exec_mode.json ──
    exec_mode = {
        "mode": "subagent" if mode == "A" else "api",
        "run_dir": raw,
    }
    if mode == "B":
        exec_mode["model"] = args.model

    exec_mode_file.write_text(
        json.dumps(exec_mode, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if mode == "A":
        print(f"✅ 生产模式已确认：SubAgent 模式")
        print(f"   exec_mode.json 写入：{exec_mode_file}")
        print(f"   现在可以启动 SubAgent 并发生产（共读取 prompts_todo.jsonl 中的 task）")
        print(f"   生产完成后运行：python scripts/postprocess.py --run-dir {raw}")
    else:
        print(f"✅ 生产模式已确认：API 模式（model={args.model}）")
        print(f"   exec_mode.json 写入：{exec_mode_file}")
        print(f"   现在可以运行：python scripts/run_batch_api.py --run-dir {raw} "
              f"--appid <API_KEY> --model {args.model}")


if __name__ == "__main__":
    main()
