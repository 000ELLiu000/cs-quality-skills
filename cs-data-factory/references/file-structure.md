# 文件结构参考

> cs-data-factory/ 完整目录结构，标注各文件的角色和归属阶段。

```
cs-data-factory/
├── SKILL.md                       # 技能入口：导航 + 调度协议 + 执行红线
├── CHANGELOG.md                   # 版本变更历史
├── scripts/
│   ├── enumerate_paths.py         # Phase 0：路径枚举（自动保护 _custom 路径）
│   ├── analyze_signals.py         # Phase 0：信号规则草稿生成（输出 data/tmp/{sop_id}_signal_rules_draft.json）
│   ├── analyze_persona.py         # Phase 0（可选）：从真实样本生成场景定制化 Persona
│   ├── preview_phase0.py          # Phase 0：HTML 可视化 + 内嵌 verify（输出 data/tmp/{sop_id}_phase0_preview.html）
│   ├── generate_prompts.py        # Phase 1：Prompt 拼装（不调 API）
│   ├── gen_custom_batch.py        # Phase 1 变体：自定义 path×persona 组合生成（同样输出 .prompt.txt）
│   ├── make_batch_50.py           # legacy 辅助：生成 gen_custom_batch 命令模板（不直接生产 prompt）
│   ├── postprocess.py             # Phase 2：result.txt 展平 → final.jsonl
│   ├── confirm_production_mode.py # Phase 2：生产模式强制确认门槛（写 exec_mode.json）
│   ├── confirm_judge_mode.py      # Phase 3：Judge 模式强制确认门槛（写 exec_mode.json）
│   ├── run_batch_api.py           # Phase 2：API 模式批量生产（写入 result.txt）
│   └── export_excel.py            # 可选：final.jsonl → final.xlsx（带格式，便于人工抽查）
├── lib/
│   ├── __init__.py                # Phase 1 公共工具：路径解析 / task_id 解析 / prompt 组装 / bundle 写出
│   └── api_client.py              # Friday API 调用封装（生产 / Judge API 模式共用）
├── harness_verify/                # 节点级格式校验（不做语义判断）
│   ├── verify_graph.py            # Phase 0 后：校验 `{sop_id}_graph.json` 格式
│   ├── verify_paths.py            # Phase 0 后：校验 paths.jsonl 格式
│   ├── verify_prompts.py          # Phase 1 后：校验 prompt 产物完整性（SubAgent 启动前必做）
│   └── verify_output.py           # Phase 2 后：校验 final.jsonl 格式（含 --json / --scan-dir / --strict）
├── references/
│   ├── sop-example.md             # SOP 格式示例（发票场景真实案例）
│   ├── sop/                       # SOP 源文件目录（用户提供）
│   ├── persona.md                 # Persona 定义（单一事实来源）
│   ├── expand-prompt.md           # Prompt 模板
│   ├── output-schema.md           # final.jsonl 输出字段 Schema
│   ├── checkpoint.md              # 全链路 Trace + 节点流转契约 + 续跑规则
│   ├── phase0-sop-parsing.md      # Phase 0 执行规范（SubAgent 执行手册）
│   ├── phase1-prompt-generation.md # Phase 1 执行规范（参数收集 + 脚本命令）
│   ├── phase2-production.md       # Phase 2 执行规范（SubAgent 调度 + 后处理）
│   ├── phase3-judge.md            # Phase 3 执行规范（质检脚本 + 工作流 A/B/C）
│   ├── file-delivery.md           # 文件发送规范（HTML/Excel/Markdown 三类文件 × Catclaw/CatPaw/CatDesk 三种环境）
│   └── file-structure.md          # 本文件：目录结构参考
├── assets/
│   ├── speech-guidelines.md       # 全局话术规范（用户提供）
│   └── real_signals/              # 真实信号样本 Excel（用户提供，.gitignore 排除）
│       └── real_signals_sample_{sop_id}.xlsx   # 命名规范：real_signals_sample_{sop_id}.xlsx
├── judge/
│   ├── annotation-guide.md        # 人工标注说明
│   ├── rubric/
│   │   ├── rubric_v1.0.md         # 评分标准
│   │   └── CHANGELOG.md
│   ├── prompts/
│   │   ├── judge_prompt_v1.0.md   # Judge Prompt 模板（{{字段名}} 占位符）
│   │   └── CHANGELOG.md
│   ├── regression/
│   │   ├── regression_set.jsonl   # 固定回归集（带 ground_truth）
│   │   └── results/               # 回归测试历史结果
│   └── scripts/
│       ├── run_judge.py           # Phase 3 步骤1：抽样 + 生成 prompt 文件（支持 SubAgent / API 两种模式）
│       ├── collect_subagent_results.py  # Phase 3 步骤3：汇总 result.txt（SubAgent 模式）
│       ├── gen_report.py          # Phase 3 步骤4：生成质检报告 report.md
│       ├── analyze_root_cause.py  # Phase 3 步骤5：生成根因分析 Prompt（需 --batch 指定生产 run）
│       ├── pick_low_score_tasks.py # Phase 4：低分 session 反查 task_id + 生产模式，输出 rerun_task_ids.json
│       └── run_regression.py      # 回归测试：Judge Prompt 版本 A vs B（需 --appid）
└── data/
    ├── sop/                       # SOP 解析图（Phase 0 产物，入库）
    │   ├── {sop_id}_graph.json
    │   ├── {sop_id}_signal_rules.json   # 信号规则（Phase 0 产物，入库）
    │   └── {sop_id}_persona.md          # 场景定制化 Persona（可选，Phase 0 产物）
    ├── paths/                     # 路径骨架（Phase 0 产物，入库）
    │   └── {sop_id}_paths.jsonl
    ├── tmp/                       # Phase 0 临时产物（.gitignore 排除，审查完毕可清理）
    │   ├── {sop_id}_signal_rules_draft.json  # 信号规则草稿（待人工确认）
    │   └── {sop_id}_phase0_preview.html      # Phase 0 产物 HTML 预览
    ├── runs/                      # 生产运行批次（Phase 1-2 产物，.gitignore 排除）
    │   └── run_YYYYMMDD_NNN/
    │       ├── prompts_todo.jsonl
    │       ├── exec_mode.json          # 运行时模式锁定文件（api / subagent，防串台）
    │       ├── subagent_prompts/
    │       │   ├── task_ids.json       # 任务 ID 列表（API 模式必须；SubAgent 模式可用）
    │       │   ├── {task_id}.prompt.txt
    │       │   └── {task_id}.result.txt
    │       └── final.jsonl             # 主产物（供 Phase 3 质检消费）；另有归档副本在 data/generated/
    ├── judge_runs/                # 质检运行批次（Phase 3 产物，.gitignore 排除）
    │   └── tuning_round_N/
    │       ├── exec_mode.json          # 运行时模式锁定文件（api / subagent，防串台）
    │       ├── samples.jsonl           # 本轮抽样的 session 集（断点续跑用）
    │       ├── subagent_prompts/
    │       │   ├── batch_ids.json      # 批次 ID 列表（SubAgent 模式必须；供主 Agent 调度 SubAgent 用）
    │       │   ├── batch_001.prompt.txt  # 每个批次文件含多个 session（默认5个）
    │       │   └── batch_001.result.txt  # SubAgent 打分结果（JSON 数组格式）
    │       ├── judge_results.jsonl
    │       ├── report.md
    │       ├── root_cause_prompt.txt   # 根因分析材料（由 analyze_root_cause.py 生成）
    │       └── root_cause_analysis.md  # 根因分析结果 + 优化方案草案（由主 Agent 写入）
    └── generated/                 # 最终归档（.gitignore 排除）
```

---

## 入库 vs 忽略说明

| 类型 | 路径 | Git 状态 |
|---|---|---|
| 核心代码 / 文档 | `scripts/`, `harness_verify/`, `references/`, `judge/`, `SKILL.md` | 入库 |
| SOP 解析图 / 路径骨架 | `data/sop/`, `data/paths/` | 入库（静态产物） |
| 真实信号样本 | `assets/real_signals/*.xlsx` | `.gitignore` 排除 |
| 生产过程产物 | `data/runs/`, `data/judge_runs/`, `data/generated/` | `.gitignore` 排除 |
| Python 缓存 | `__pycache__/`, `*.pyc` | `.gitignore` 排除 |
