# 全链路 Trace / Checkpoint / Schema

> 本文档是 cs-data-factory 的统一数据流转约定。
> 目标：提前声明每个处理节点的输入、输出、校验与恢复规则，避免“脚本跑通但上下游契约不清”。

---

## 1. 产物分类

| 类型 | 说明 | 典型文件 | 是否可入 git |
|---|---|---|---|
| 用户原始输入 | 由用户提供、场景切换时替换 | `references/sop/*.md`、`assets/real_signals/*.xlsx` | 按需要；`.xlsx` 默认不入 git |
| reference 定义 | 稳定规则、模板、Schema、索引 | `references/persona.md`、`references/output-schema.md` | 是 |
| pipeline 产物 | 流程节点生成、可复用 | `data/sop/{sop_id}_graph.json`、`data/sop/{sop_id}_persona.md`、`data/sop/{sop_id}_signal_rules.json`、`data/paths/{sop_id}_paths.jsonl` | 是 |
| 运行中间产物 | 一次 run 内的过程文件 | `data/runs/<run_id>/prompts_todo.jsonl`、`subagent_prompts/*` | 否 |
| 最终输出 | 生产结果、下游消费对象 | `data/runs/<run_id>/final.jsonl`、`data/generated/*` | 视需要 |
| 质检产物 | Judge 子流程输出 | `data/judge_runs/<round>/*` | 否 |

---

## 2. 主流程 Trace & Schema

| 节点 | 责任方 | 输入 | 输入要求 | 输出 | 输出要求 | 校验 / 恢复 |
|---|---|---|---|---|---|---|
| Phase 0-A：SOP 解析（SubAgent-A） | SubAgent | `references/sop/{scene}.md` | 结构化 SOP；节点、分支、动作可读 | `data/sop/{sop_id}_graph.json` | 顶层至少包含 `sop_id` / `signal_fields` / `slot_fields` / `graph` / `actions` | `harness_verify/verify_graph.py` |
| Phase 0-A2：Persona 分析（SubAgent-B，可选） | SubAgent | `assets/real_signals/*.xlsx`（对话历史列） | 用户明确选择 B 后执行；可与 SubAgent-A 并行 | `data/sop/{sop_id}_persona.md` | 成员格式与 `references/persona.md` 一致；含情绪渐进型人设 | 人工抽查确认格式 |
| Phase 0-A3：信号分析（可选，A 完成后执行） | `scripts/analyze_signals.py` | `assets/real_signals/*.xlsx` + `data/sop/{sop_id}_graph.json` | graph.json 已生成；用户选择做信号分析 | `data/tmp/{sop_id}_signal_rules_draft.json`（草稿）→ 人工审核 → `data/sop/{sop_id}_signal_rules.json`（正式入库） | 草稿含每个信号的 source/type/variability/enum_values/mock_rule；LLM 评审补全 mock_rule 后人工确认 | 人工审查草稿；有疑义的 mock_rule 与用户确认后再入库 |
| Phase 0-B：路径枚举 | `enumerate_paths.py` | `data/sop/{sop_id}_graph.json` | graph 引用完整、terminal 可达 | `data/paths/{sop_id}_paths.jsonl` | 每行一条路径；字段完整；可被下游 prompt 生成直接消费 | `harness_verify/verify_paths.py` |
| Phase 1-A：参数选择 | 主 Agent | 用户选择的 `paths` / `personas` / `sample-mode` / `samples` | 参数缺省时使用默认值 | 运行参数集合 | 仅决定批次范围，不改写 reference | 参数确认后进入下一节点 |
| Phase 1-B：Prompt 生成 | `generate_prompts.py` / `gen_custom_batch.py` | `paths.jsonl` + `persona.md` + `references/expand-prompt.md`（或 `--expand-prompt` 覆盖）+ `{sop_id}_signal_rules.json`（可选）+ speech-guidelines | 模板可读；signal_rules 不存在时跳过信号 mock 注入 | `data/runs/<run_id>/prompts_todo.jsonl` + `data/runs/<run_id>/subagent_prompts/{task_id}.prompt.txt` + `task_ids.json` | `prompts_todo.jsonl` 每行含 `task_id/path_id/persona_id/batch/sop_id/output_count/order_signals`；每个 task 生成独立 prompt 文件（含 `{SIGNAL_MOCK_RULES}` 注入） | 预览抽检 prompt（含信号 mock 规则块）；缺配置时快速失败 |
| Phase 1-C：Prompt 校验 | `harness_verify/verify_prompts.py` | `prompts_todo.jsonl` + `subagent_prompts/*.prompt.txt` | generate_prompts.py 已成功运行 | 校验报告 | 字段完整、文件数一致、无残留占位符 | **校验通过才可启动 SubAgent**；失败则排查上游 |
| Phase 2-A：SubAgent 生产 | SubAgent | `{task_id}.prompt.txt` | 单任务独立执行；主 Agent 只传文件路径 | `{task_id}.result.txt` | 每个 task 一个结果文件；内容需可被 postprocess 解析 | 缺失结果时可补跑单 task |
| Phase 2-B：结果汇总 / 展平 | `postprocess.py` | `subagent_prompts/*.result.txt` | result 文件可解析；允许部分缺失 | `data/runs/<run_id>/final.jsonl` | 满足 `references/output-schema.md`；逐轮拆分；可归档 | `--dry-run` 查看缺失与报错 |
| Phase 2-C：归档 | `postprocess.py` | `data/runs/<run_id>/final.jsonl` | `final.jsonl` 已成功生成 | `data/generated/{run_id}_final.jsonl` | 与 run 内 `final.jsonl` 内容一致 | 归档后可询问是否清理中间产物 |
| Phase 2-D：格式校验 | `harness_verify/verify_output.py` | `final.jsonl` + `data/sop/{sop_id}_graph.json` | 动作枚举、字段、session id 合法、时间格式 | 校验报告 / error list | 不改数据，仅暴露问题 | 校验失败后回溯上游节点 |
| Phase 3-A：生成 Judge Prompt | `judge/scripts/run_judge.py --export-prompts` | `final.jsonl`（抽样） | `final.jsonl` 已通过格式校验；`--judge-version` 指定版本存在 | `data/judge_runs/<round>/subagent_prompts/batch_XXX.prompt.txt` + `batch_ids.json` | 每个批次文件含多个 session（默认5个）；已填充变量，无残留占位符 | 检查批次文件数与 batch_ids.json 一致 |
| Phase 3-B：SubAgent 打分 | SubAgent | `batch_XXX.prompt.txt` | 每个 SubAgent 处理一个批次（含多个 session）；主 Agent 只传文件路径 | `batch_XXX.result.txt` | JSON 数组，每个元素含 `session_id` / `action_tags` / `emotion_level` / `dimension_scores` / `normalized_score` / `summary` | 缺失批次可补跑；`collect_subagent_results.py` 支持批次格式 |
| Phase 3-C：汇总报告 | `collect_subagent_results.py` + `gen_report.py` | `subagent_prompts/*.result.txt` | result 文件可解析；允许部分缺失（自动修复常见 JSON 格式问题） | `judge_results.jsonl` + `report.md` | `judge_results.jsonl` 每行含完整打分字段；`report.md` 含各维度均值与低分分析 | 报告生成后触发流程 A/B/C（见 `phase3-judge.md`） |

---

## 3. 节点输入输出契约

### 3.1 `data/sop/{sop_id}_graph.json`

最小契约：
- 顶层字段：`sop_id` / `signal_fields` / `slot_fields` / `graph` / `actions`
- `graph.START` 必须存在
- 所有 `agent_action` 节点引用的 `action_id` 必须能在 `actions` 中找到
- 所有非 terminal 节点必须有明确后继

消费方：
- `scripts/enumerate_paths.py`
- `scripts/generate_prompts.py`
- `harness_verify/verify_graph.py`
- `harness_verify/verify_output.py`

### 3.2 `data/paths/{sop_id}_paths.jsonl`

最小契约：
- 一行一条路径
- 至少包含路径标识、节点序列、动作/信号信息
- 字段必须完整到可直接驱动 prompt 生成

消费方：
- `scripts/generate_prompts.py`
- `scripts/gen_custom_batch.py`
- `harness_verify/verify_paths.py`

### 3.3 `data/runs/<run_id>/prompts_todo.jsonl`

最小契约：
- 一行一条 task
- 至少包含：`task_id` / `path_id` / `persona_id` / `batch` / `sop_id` / `output_count` / `order_signals`
- `task_id` 必须与独立 prompt 文件一一对应
- `order_signals`：骨架路径判断信号 dict（`postprocess.py` 用于组装「所有信号」的权威来源）
- 不含 `prompt` 字段正文（避免主 Agent 上下文膨胀，prompt 正文只写到独立 `.prompt.txt` 文件）

消费方：
- 主 Agent（调度 task 列表）
- `scripts/postprocess.py`（读取 `order_signals` 组装「所有信号」）
- `data/runs/<run_id>/subagent_prompts/{task_id}.prompt.txt`

### 3.4 `data/runs/<run_id>/subagent_prompts/{task_id}.prompt.txt`

最小契约：
- 单任务可独立执行
- 包含当前 path、persona、信号、输出要求
- 主 Agent 不在主上下文中内嵌 prompt 正文

消费方：
- SubAgent

### 3.5 `data/runs/<run_id>/subagent_prompts/{task_id}.result.txt`

最小契约：
- 单任务对应单结果
- 内容需可被 `postprocess.py` 解析
- 若格式异常，`postprocess.py` 允许跳过并汇总错误

消费方：
- `scripts/postprocess.py`

### 3.6 `data/runs/<run_id>/final.jsonl`

最小契约：
- 满足 `references/output-schema.md`
- 动作值必须来自 `{sop_id}_graph.json` 的 `actions`
- session id、时间格式、历史消息格式合法

消费方：
- 算法侧训练
- `harness_verify/verify_output.py`
- `judge/scripts/run_judge.py`

---

## 4. 断点恢复规则

当部分 SubAgent 失败时：
1. 运行 `python scripts/postprocess.py --run-dir data/runs/<run_id> --dry-run`
2. 根据 error list 找出缺失或解析失败的 `task_id`
3. 只补跑缺失 task，不重跑整个批次
4. 重新运行 `python scripts/postprocess.py --run-dir data/runs/<run_id>` 合并结果

补充规则：
- 不存在自动断点续跑机制，需人工补跑
- 所有 SubAgent 输出必须落盘到 `subagent_prompts/{task_id}.result.txt`
- 任何脚本修复如果影响中间产物含义，必须重跑该脚本下游所有产物

---

## 5. 清理规则

### 5.1 Phase 1-2 运行产物

在 `final.jsonl` 已归档、且校验通过后：
- 可删除 `data/runs/<run_id>/subagent_prompts/*.prompt.txt`
- 可删除 `data/runs/<run_id>/subagent_prompts/*.result.txt`
- 可视需要删除 `data/runs/<run_id>/prompts_todo.jsonl`

### 5.2 Phase 0 临时产物（data/tmp/）

`data/tmp/` 下的产物均为**人工审查用的临时文件，不入 git**，可在用户审查并确认入库后清理：

| 文件 | 来源 | 清理时机 |
|---|---|---|
| `{sop_id}_signal_rules_draft.json` | `analyze_signals.py` 输出 | 用户确认后入库（`data/sop/` 下），入库后可删 |
| `{sop_id}_phase0_preview.html` | `preview_phase0.py` 输出 | 用户审查完毕后可删；Phase 0 反复修改期间可保留 |

> `data/tmp/` 下文件**不应长期堆积**；每次重新生成会覆盖同名文件，但不同 sop_id 的文件互不影响。

### 5.3 默认 git 约束

- `*.prompt.txt` / `*.result.txt` / `data/runs/` / `data/judge_runs/` 不入 git
- `data/tmp/` 整目录不入 git
- `.xlsx` 默认不入 git
- 代码、reference、稳定 pipeline 产物（如 `data/sop/*.json`）可以入 git
