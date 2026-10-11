#!/usr/bin/env python3
"""
run_batch_api.py
----------------
Phase 2 生产侧 API 模式：替代 SubAgent spawn，通过 兼容 Chat Completions 的模型 API 批量执行对话生成。

读取 generate_prompts.py 已生成的 prompt 文件（subagent_prompts/{task_id}.prompt.txt），
逐个调用 API，结果写入同名 result.txt。
后续流程与 SubAgent 模式完全一致：继续执行 postprocess.py → final.jsonl。

用法:
  python scripts/run_batch_api.py \\
      --run-dir data/runs/run_YYYYMMDD_NNN \\
      --appid <模型 API Key> \\
      --model your-model \\
      [--workers 4] \\
      [--task-ids P001_P1_001,P001_P1_002]

  # 断点续跑（已存在 result.txt 的 task 自动跳过）：
  python scripts/run_batch_api.py \\
      --run-dir data/runs/run_YYYYMMDD_NNN \\
      --appid <AppId> --model your-model

注意事项：
  - 与 SubAgent 模式互斥：同一 run 目录只能选其一。
  - 并发数建议不超过 10，视 API 限流情况调整。
  - 本脚本会在 run_dir 下写入 exec_mode.json 锁定当前运行为 api 模式。
"""

import argparse
import json
import sys
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ─── 路径 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent          # cs-data-factory/scripts/
SKILL_ROOT = SCRIPT_DIR.parent             # cs-data-factory/
LIB_DIR = SKILL_ROOT / "lib"

# 将 lib 加入搜索路径
sys.path.insert(0, str(SKILL_ROOT))
from lib.api_client import call_api


# ─── exec_mode.json ────────────────────────────────────────────────────────────

def check_and_write_exec_mode(run_dir: Path, model: str) -> None:
    """
    检查 exec_mode.json 是否与当前模式冲突。
    - 不存在 → 写入 mode=api
    - 已存在且 mode=api → 继续（断点续跑）
    - 已存在且 mode=subagent → 报错退出，防止串台
    """
    mode_file = run_dir / "exec_mode.json"
    if mode_file.exists():
        existing = json.loads(mode_file.read_text(encoding="utf-8"))
        if existing.get("mode") == "subagent":
            print("❌ 该 run 目录已使用 SubAgent 模式生产，不能切换为 API 模式。")
            print("   如需 API 重跑，请新建一个 run 目录（--run-dir data/runs/new_run）。")
            sys.exit(1)
        # mode=api：已设定，断点续跑，不重新写
        return

    mode_data = {
        "mode": "api",
        "model": model,
        "set_at": datetime.datetime.now().isoformat(),
    }
    mode_file.write_text(json.dumps(mode_data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── 单任务执行 ────────────────────────────────────────────────────────────────

def run_task(task_id: str, prompt_file: Path, result_file: Path,
             appid: str, model: str) -> tuple[str, str | None]:
    """
    读取 prompt 文件 → 调用 API → 写入 result 文件。
    返回 (task_id, error_msg)，成功时 error_msg=None。
    """
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
        raw = call_api(prompt, appid=appid, model=model, temperature=0.7, max_tokens=4096)
        result_file.write_text(raw, encoding="utf-8")
        return task_id, None
    except Exception as e:
        return task_id, str(e)


# ─── 主流程 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="cs-data-factory Phase 2 生产侧 API 模式批量执行器"
    )
    parser.add_argument("--run-dir", required=True,
                        help="生产 run 目录（相对 cs-data-factory/，如 data/runs/run_xxx）")
    parser.add_argument("--appid", default=None,
                        help="模型 API Key（Bearer token）。也可通过环境变量 CS_FACTORY_APPID 传入，命令行优先。")
    parser.add_argument("--model", default="your-model",
                        help="模型名（默认 your-model）")
    parser.add_argument("--workers", type=int, default=4,
                        help="并发数（默认 4，建议不超过 10）")
    parser.add_argument("--task-ids",
                        help="只跑指定 task_id（逗号分隔），不填则处理全部")
    args = parser.parse_args()

    # ── AppId 解析（命令行 > 环境变量 CS_FACTORY_APPID）──
    from lib.api_client import resolve_appid
    try:
        resolved_appid = resolve_appid(args.appid)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    args.appid = resolved_appid  # 统一写回 args，后续直接用

    run_dir = SKILL_ROOT / args.run_dir
    if not run_dir.exists():
        print(f"❌ run 目录不存在: {run_dir}")
        print("   请先执行 generate_prompts.py 完成 Phase 1。")
        sys.exit(1)

    prompt_dir = run_dir / "subagent_prompts"
    if not prompt_dir.exists():
        print(f"❌ subagent_prompts/ 目录不存在: {prompt_dir}")
        print("   请先执行 generate_prompts.py 完成 Phase 1。")
        sys.exit(1)

    # ── 模式锁定检查 ──
    check_and_write_exec_mode(run_dir, args.model)

    # ── 加载任务列表 ──
    ids_file = prompt_dir / "task_ids.json"
    if not ids_file.exists():
        print(f"❌ task_ids.json 不存在: {ids_file}")
        print("   请先执行 generate_prompts.py 完成 Phase 1。")
        sys.exit(1)

    all_task_ids: list[str] = json.loads(ids_file.read_text(encoding="utf-8"))

    # 若指定了 --task-ids，过滤
    if args.task_ids:
        specified = {t.strip() for t in args.task_ids.split(",")}
        all_task_ids = [t for t in all_task_ids if t in specified]
        print(f"   指定执行 {len(all_task_ids)} 个 task")

    # 跳过已有 result.txt 的（断点续跑）
    pending = []
    skipped = 0
    for task_id in all_task_ids:
        result_file = prompt_dir / f"{task_id}.result.txt"
        if result_file.exists():
            skipped += 1
        else:
            pending.append(task_id)

    print(f"📂 run 目录：{run_dir}")
    print(f"🤖 模型：{args.model}")
    print(f"📋 总任务：{len(all_task_ids)}，待执行：{len(pending)}，已跳过：{skipped}")

    if not pending:
        print("✅ 所有 task 已完成，无需重跑。")
        print(f"\n下一步：")
        print(f"   python scripts/postprocess.py --run-dir {args.run_dir}")
        return

    # ── 并发执行 ──
    print(f"\n🚀 开始执行（workers={args.workers}）...")
    done_count = 0
    failed_count = 0
    failed_ids = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                run_task,
                task_id,
                prompt_dir / f"{task_id}.prompt.txt",
                prompt_dir / f"{task_id}.result.txt",
                args.appid,
                args.model,
            ): task_id
            for task_id in pending
        }
        for future in as_completed(futures):
            task_id, error = future.result()
            if error:
                print(f"  ❌ {task_id}: {error[:120]}")
                failed_ids.append(task_id)
                failed_count += 1
            else:
                print(f"  ✅ {task_id}")
                done_count += 1

    # ── 收尾 ──
    print(f"\n📊 完成：成功={done_count}，失败={failed_count}")
    if failed_ids:
        failed_list = ",".join(failed_ids)
        print(f"⚠️  失败 task：{failed_list}")
        print(f"   可用 --task-ids {failed_list} 单独重跑失败 task")
    print(f"\n下一步：")
    print(f"   python scripts/postprocess.py --run-dir {args.run_dir}")


if __name__ == "__main__":
    main()
