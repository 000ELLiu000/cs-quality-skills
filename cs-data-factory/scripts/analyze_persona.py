#!/usr/bin/env python3
"""
analyze_persona.py
------------------
从真实信号样本 Excel 中分析对话历史，聚类用户行为模式，
生成场景定制化的 persona.md 文件。

分析流程：
  Step 1. 读取 Excel，按 sessionId 分组，取每组最后一行的完整对话历史
  Step 2. 将所有完整对话历史交给 LLM 聚类分析（提取用户行为模式）
  Step 3. 参照 references/persona.md 格式，生成场景定制化的 persona 文件
          （含情绪渐进增强人设：先平静→再不耐烦→再愤怒）

用法:
  python scripts/analyze_persona.py \\
      --sop-id daocandaopiao \\
      --out data/sop/daocandaopiao_persona.md \\
      [--max-sessions 60]    # 最多取多少条 session（默认全部）
      [--dry-run]            # 只打印提取到的对话历史，不分析

输出:
  data/sop/{sop_id}_persona.md （pipeline 产物，就近 graph.json 存放）

后续使用：
  生成后在 generate_prompts.py 中用 --persona-file 指定此文件替代默认 persona.md，
  或手动将内容合并到 references/persona.md 中。

注意：
  - 本脚本需要 LLM 能力（由主 Agent 或 SubAgent 执行），不直接调用外部 API
  - dry-run 模式输出对话历史文本，用于人工抽查后交给 LLM 分析
  - 兼容列名：对话历史 / 对话记录 / 历史上文 / 历史消息
"""

import argparse
import sys
import json
from pathlib import Path
from collections import defaultdict

# Windows 下强制 stdout/stderr 使用 UTF-8，避免 emoji 等字符导致 GBK 编码报错
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── 路径 ───────────────────────────────────────────────────────────────────────
SKILL_ROOT = Path(__file__).parent.parent
ASSETS_DIR = SKILL_ROOT / "assets"
REFERENCES_DIR = SKILL_ROOT / "references"

# 兼容的对话历史列名（按优先级排列）
DIALOG_COLUMN_CANDIDATES = ["对话历史", "对话记录", "历史上文", "历史消息"]


def find_dialog_column(headers: list) -> str | None:
    """在列头中找到对话历史列，返回实际列名，找不到返回 None。"""
    for candidate in DIALOG_COLUMN_CANDIDATES:
        if candidate in headers:
            return candidate
    return None


def load_excel(excel_path: Path) -> list[dict]:
    """加载 Excel，返回所有行的 dict 列表。"""
    try:
        import openpyxl
    except ImportError:
        print("❌ 需要 openpyxl：pip install openpyxl", file=sys.stderr)
        sys.exit(1)
    if not excel_path.exists():
        print(f"❌ Excel 文件不存在：{excel_path}", file=sys.stderr)
        sys.exit(1)
    wb = openpyxl.load_workbook(excel_path)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        rows.append(dict(zip(headers, row)))
    return rows, headers


def extract_last_dialogs(rows: list[dict], dialog_col: str, max_sessions: int | None) -> list[dict]:
    """
    按 sessionId 分组，取每组最后一行的完整对话历史。
    返回 [{sessionId, dialog_text}, ...]
    """
    session_rows = defaultdict(list)
    for i, row in enumerate(rows):
        sid = row.get("sessionId")
        if sid is not None:
            session_rows[str(sid)].append((i, row))
        else:
            # 没有 sessionId 列时，每行独立作为一个 session
            session_rows[f"__row_{i}__"].append((i, row))

    results = []
    for sid, items in session_rows.items():
        # 取最后一行（对话历史渐进递增，最后一行是完整版本）
        last_row = items[-1][1]
        dialog_text = str(last_row.get(dialog_col, "") or "").strip()
        if dialog_text:
            results.append({
                "sessionId": sid,
                "dialog_text": dialog_text,
            })

    # 过滤过短的对话（少于50字视为无效）
    results = [r for r in results if len(r["dialog_text"]) >= 50]

    if max_sessions and len(results) > max_sessions:
        results = results[:max_sessions]

    return results


def build_analysis_prompt(dialogs: list[dict], sop_id: str, reference_persona_text: str) -> str:
    """
    构建交给 LLM 的分析 Prompt。
    """
    dialog_block = ""
    for i, d in enumerate(dialogs, 1):
        dialog_block += f"\n=== 对话 {i}（sessionId: {d['sessionId']}）===\n"
        dialog_block += d["dialog_text"]
        dialog_block += "\n"

    prompt = f"""你的任务是分析以下真实客服对话样本，总结用户行为模式，并生成一份场景定制化的 Persona 文件。

---

## 参考格式

以下是现有 Persona 文件的格式和示例，你生成的文件必须严格遵循此格式：

{reference_persona_text}

---

## 真实对话样本（共 {len(dialogs)} 条，已取每个 session 的完整对话）

{dialog_block}

---

## 分析要求

1. **聚类分析**：从上述真实对话中，识别用户的典型行为模式（语气、耐心程度、配合度、语言特征、典型用词等）。聚类数量建议 5-8 类，以能覆盖真实分布为准。

2. **占比估算**：根据观察到的频率，估算每类 Persona 的大致占比（百分比之和=100%）。

3. **情绪渐进增强人设（必须包含）**：除了从真实样本聚类出的人设外，额外构造至少 1 类情绪渐进型人设，描述用户情绪从平静→不耐烦→愤怒的渐进变化过程（真实样本仅作参考，这类人设可以合理增强构造，不必在样本中有直接对应）。情绪渐进型人设编号放在最后（如 PX：情绪渐进型）。

4. **格式要求**：
   - 文件顶部注明这是场景定制化版本，基于哪个场景（sop_id: {sop_id}）的真实样本分析生成
   - 保留「核心原则」章节（内容与参考格式相同）
   - 保留「各类 Persona 真实占比」表格（更新为本场景数据）
   - 每个 Persona 块包含：耐心、合作度、语言特征、典型行为、示例（至少3个），以及从真实样本或合理构造的对话示例
   - Persona ID 从 P1 开始编号（不沿用参考格式的编号，按本场景重新编）
   - 文件末尾保留「在生产时如何使用」和「Persona 选取建议」章节

5. **输出**：只输出 Persona 文件的完整 Markdown 内容，不要输出任何额外说明或解释。
   生成完成后立即将结果写入 `data/sop/{sop_id}_persona.md`（如果文件已存在则覆盖）。
   写入完成后输出「✅ SubAgent-B 完成，Persona 已写入 data/sop/{sop_id}_persona.md」。
"""
    return prompt


def main():
    parser = argparse.ArgumentParser(description="从真实样本 Excel 分析生成场景定制化 Persona")
    parser.add_argument("--sop-id", required=True, help="SOP 标识（如 daocandaopiao），用于定位 Excel 配置")
    parser.add_argument("--excel", default="", help="显式指定 Excel 路径（不填则使用约定路径 assets/real_signals/real_signals_sample_{sop_id}.xlsx）")
    parser.add_argument("--out", default="", help="输出 persona 文件路径（默认：data/sop/{sop_id}_persona.md）")
    parser.add_argument("--max-sessions", type=int, default=0, help="最多取多少条 session（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="只打印提取到的对话历史摘要，不生成分析 Prompt")
    args = parser.parse_args()

    # ── 确定 Excel 路径 ──
    if args.excel:
        excel_path = Path(args.excel)
    else:
        # fallback 到约定路径：assets/real_signals/real_signals_sample_{sop_id}.xlsx
        default_excel = ASSETS_DIR / "real_signals" / f"real_signals_sample_{args.sop_id}.xlsx"
        if not default_excel.exists():
            print(f"❌ 未找到 Excel 文件：{default_excel}", file=sys.stderr)
            print(f"   请通过 --excel 显式指定 Excel 路径，或将 Excel 文件放置于：", file=sys.stderr)
            print(f"   {default_excel}", file=sys.stderr)
            sys.exit(1)
        excel_path = default_excel
        print(f"   使用默认路径：{excel_path}")

    # ── 确定输出路径 ──
    if args.out:
        out_path = Path(args.out)
    else:
        # pipeline 产物，与 graph.json 并列在 data/sop/
        out_path = SKILL_ROOT / "data" / "sop" / f"{args.sop_id}_persona.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 加载 Excel ──
    print(f"📂 加载 Excel：{excel_path}")
    rows, headers = load_excel(excel_path)
    print(f"   总行数：{len(rows)}  列：{headers}")

    # ── 找对话历史列 ──
    dialog_col = find_dialog_column(headers)
    if not dialog_col:
        print(f"❌ 未在列头中找到对话历史列。", file=sys.stderr)
        print(f"   支持的列名：{DIALOG_COLUMN_CANDIDATES}", file=sys.stderr)
        print(f"   实际列头：{headers}", file=sys.stderr)
        sys.exit(1)
    print(f"   使用列名：「{dialog_col}」")

    # ── 提取每 session 最后一行对话 ──
    max_sessions = args.max_sessions if args.max_sessions > 0 else None
    dialogs = extract_last_dialogs(rows, dialog_col, max_sessions)
    print(f"   唯一 session 数：{len(dialogs)}（有效对话长度 ≥ 50 字）")

    if not dialogs:
        print("❌ 未提取到任何有效对话，请检查 Excel 数据。", file=sys.stderr)
        sys.exit(1)

    # ── dry-run：只打印对话摘要 ──
    if args.dry_run:
        print(f"\n🔍 DRY-RUN：打印前10条 session 的对话历史摘要")
        for i, d in enumerate(dialogs[:10]):
            print(f"\n--- session {i+1} ({d['sessionId']}) ---")
            print(d["dialog_text"][:400])
            if len(d["dialog_text"]) > 400:
                print("...(截断)")
        print(f"\n共 {len(dialogs)} 条有效 session，去掉 --dry-run 后生成分析 Prompt。")
        return

    # ── 加载参考 persona.md ──
    reference_persona_path = REFERENCES_DIR / "persona.md"
    if reference_persona_path.exists():
        reference_persona_text = reference_persona_path.read_text(encoding="utf-8")
        # 只取前半部分（格式参考，不需要全部示例）
        reference_persona_text = reference_persona_text[:3000] + "\n...(格式参考，其余省略)"
    else:
        reference_persona_text = "（未找到参考 persona.md，请参考 cs-data-factory 标准格式）"

    # ── 构建分析 Prompt ──
    print(f"\n📝 生成分析 Prompt...")
    analysis_prompt = build_analysis_prompt(dialogs, args.sop_id, reference_persona_text)

    # ── 打印 Prompt 供 Agent 执行 ──
    print(f"\n{'='*60}")
    print(f"以下是交给 LLM/SubAgent 执行的分析 Prompt（共 {len(analysis_prompt)} 字）：")
    print(f"预期输出文件：{out_path}")
    print(f"{'='*60}\n")
    print(analysis_prompt)
    print(f"\n{'='*60}")
    print(f"⚠️  请将上述 Prompt 交给 SubAgent 执行（不在主 session 中直接分析，避免上下文膨胀）。")
    print(f"   SubAgent 执行完成后，检查 {out_path} 是否生成正确。")
    print(f"   若需在 Phase 1 中使用定制 Persona，在 generate_prompts.py 命令中指定：")
    print(f"   --persona-file {out_path}")
    print(f"{'='*60}")

    # ── 同时将 Prompt 写入临时文件，供 SubAgent 读取 ──
    prompt_cache_path = SKILL_ROOT / "data" / "tmp" / f"{args.sop_id}_persona_analysis.prompt.txt"
    prompt_cache_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_cache_path.write_text(analysis_prompt, encoding="utf-8")
    print(f"\n💾 Prompt 已缓存到：{prompt_cache_path}")
    print(f"   SubAgent 可直接读取此文件执行分析，无需在主 session 传递长文本。")


if __name__ == "__main__":
    main()