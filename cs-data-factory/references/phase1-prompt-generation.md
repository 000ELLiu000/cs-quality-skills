# Phase 1 生产准备执行规范

> 本文档供主 Agent 执行 Phase 1（Prompt 生成）时参考。  
> Phase 1 全程由脚本完成，不调用 LLM，主 Agent 负责参数收集、命令执行和产物校验。

---

## 前置说明：场景定制化 Persona

> Phase 0 如执行了 Persona 聚类分析（SubAgent-B），产物为 `data/sop/{sop_id}_persona.md`。  
> 若 Phase 0 跳过了 SubAgent-B（例如用户选择默认 Persona），则使用默认 `references/persona.md`。

**在 Phase 1 中使用定制 Persona：** 在 `generate_prompts.py` 命令中加 `--persona-file` 参数：

```bash
python scripts/generate_prompts.py \
    --sop-id <sop_id> \
    --run-dir data/runs/run_YYYYMMDD_NNN \
    --persona-file data/sop/<sop_id>_persona.md \
    [--samples 5]
```

> 不加 `--persona-file` 则使用默认 `references/persona.md`；若需临时覆盖 Prompt 模板，可额外传 `--expand-prompt <path>`。

---

## Step 0. 参数收集（必须，不可跳过）

**Agent 启动 Phase 1 前，必须向用户展示以下参数列表并等待回复，收到用户明确反馈（包括"使用默认值"/"直接确认"）后才可执行 `generate_prompts.py`。不得静默使用默认值跳过本步。**

**展示前，Agent 需先读取当前 sop_id 的路径数（来自 `data/paths/{sop_id}_paths.jsonl` 行数）和 Persona 数（来自 persona 文件），用于计算任务量预估。**

> **强制执行机制**：`generate_prompts.py` 内置了 `--confirmed` 门槛。
> Agent 应先**不带 `--confirmed`** 运行脚本，脚本会打印参数摘要后自动退出（exit 0），不生成任何文件。
> Agent 将摘要展示给用户确认后，才能加 `--confirmed` 重新运行正式生成。
> 任何情况下直接带 `--confirmed` 运行均视为违规，会被此文档明确禁止。

向用户展示以下内容，等待回复：

```
请确认生产参数（直接回复"默认"使用快速验证配置，或逐项指定）：

【快速验证推荐配置（默认）】
  适合首次跑通流程或验证 prompt 质量，回复"默认"即使用此配置：
  - 路径集：步数最多的 2 条路径（turns 最大的前 2 条，Agent 自动选取）
  - Persona 集：情绪特征最明显的 2 个 Persona（Agent 自动选取最高/最低合作度各1个）
  - 采样模式：all（全排列）
  - 每 task 生成条数：2
  → 预计任务数：2 路径 × 2 Persona = 4 task，共 8 条 session

【正式生产配置（全量）】
  - 路径集：全量（共 {N_paths} 条）
  - Persona 集：全量（共 {N_personas} 个）
  - 采样模式：all（全排列）
  - 每 task 生成条数：5
  → 预计任务数：{N_paths} 路径 × {N_personas} Persona = {N_paths*N_personas} task，共 {N_paths*N_personas*5} 条 session

【自定义】
  - 路径集：指定路径 ID，如 P001,P003,P005
  - Persona 集：指定 Persona ID，如 P1,P3
  - 采样模式：all（全排列）或 one-per-path（每路径1个Persona，任务数=路径数）
  - 每 task 生成条数：N（建议验证时用 2，生产时用 5）
  → 预计任务数和 session 数 Agent 会在确认参数后告知

任务量公式：
  - all 模式：任务数 = 路径数 × Persona 数，session 总数 = 任务数 × 每 task 生成条数
  - one-per-path 模式：任务数 = 路径数，session 总数 = 路径数 × 每 task 生成条数

情绪处理规范：Agent 自动检测 data/sop/{sop_id}_emotion.md，有则加载并注入 prompt，无则跳过（如需配置，将规范文件放入该路径即可，无需任何参数）
```

**Agent 选取"快速验证默认路径"的规则：**
- 读取 `data/paths/{sop_id}_paths.jsonl`，按 `turns` 字段降序，选取前 2 条（turns 最多的路径往往覆盖外呼等复杂分支）
- 若 turns 相同，优先选 `has_outbound=True` 的路径

**Agent 选取"快速验证默认 Persona"的规则：**
- 读取当前 persona 文件（场景定制版优先，否则用默认版），选取合作度最低的1个（情绪对抗最明显）和合作度最高的1个（情绪配合最明显）
- 若场景 persona 文件无合作度字段，则选 ID 最小的1个和最大的1个

---

## Step 1. 参数说明

- 路径：`--paths P001,P003,P005`（或不填=全部）
- Persona：`--personas P1,P3`（或不填=全部）
- 采样模式：`--sample-mode all`（全排列，默认）或 `--sample-mode one-per-path`（每路径只选1个Persona，轮换分配，适合快速测试）

---

## Step 2. 生成 Prompt 文件

> **两阶段执行规范**：
> 1. **先不带 `--confirmed` 运行**（参数摘要模式）：脚本打印摘要后 exit 0，Agent 将摘要展示给用户
> 2. **用户确认后，加 `--confirmed` 重新运行**（正式生成模式）：脚本生成所有文件

```bash
# ── 阶段 1：参数摘要（不带 --confirmed，Agent 展示给用户确认）──
python scripts/generate_prompts.py \
    --sop-id <sop_id> \
    --run-dir data/runs/run_YYYYMMDD_NNN \
    [--paths P001,P003] \
    [--personas P1,P3] \
    [--sample-mode all] \
    [--samples 5]

# ── 阶段 2：正式生成（用户确认参数后，加 --confirmed）──
# 全排列（生产用）
python scripts/generate_prompts.py \
    --sop-id <sop_id> \
    --run-dir data/runs/run_YYYYMMDD_NNN \
    [--paths P001,P003] \
    [--personas P1,P3] \
    [--samples 5] \
    --confirmed

# 快速测试（每路径1个Persona，N路径=N任务）
python scripts/generate_prompts.py \
    --sop-id <sop_id> \
    --run-dir data/runs/run_test_YYYYMMDD \
    --sample-mode one-per-path \
    [--samples 2] \
    --confirmed
```

输出：
- `data/runs/run_YYYYMMDD_NNN/prompts_todo.jsonl`（索引清单）
- `data/runs/run_YYYYMMDD_NNN/subagent_prompts/*.prompt.txt`（各任务独立 Prompt 文件）

---

## Step 2.5. Phase 1 产物校验（推荐，启动 SubAgent 前必做）

```bash
python harness_verify/verify_prompts.py --run-dir data/runs/run_YYYYMMDD_NNN
```

校验项：
- `prompts_todo.jsonl` 存在且字段完整（task_id / path_id / persona_id / batch / sop_id / output_count / order_signals）
- 每个 task_id 对应的 `.prompt.txt` 文件存在且非空
- `.prompt.txt` 中不含未替换的模板占位符（`{PATH_CONTENT}` 等残留）
- 文件数与任务数一致

**✋ 校验不通过则不要启动 SubAgent，先排查 `generate_prompts.py` 的报错。**

---

## Step 3. 用户抽查 Prompt（可选）

直接读取对应 `.prompt.txt` 文件内容即可（比从 `prompts_todo.jsonl` 解析更快）：

```
告知 Agent：读取 data/runs/run_YYYYMMDD_NNN/subagent_prompts/P001_P1_001.prompt.txt
```

---

## Phase 1 完成后的交接规范（必须遵守）

**`prompts_todo.jsonl` 生成且校验通过后，Agent 必须向用户展示以下内容，然后等待用户指令，不得主动推荐任何选项或直接进入下一步。**

展示格式如下：

```
✅ Phase 1 Prompt 生成完成

当前状态：
  - run_id：<run_id>
  - task 数：<N> 个
  - 预计 session 数：<N × samples> 条

接下来可以：
  A. 进入 Phase 2 生产（SubAgent 或 API 模式）
  B. 抽查 Prompt 内容（读取指定 task 的 .prompt.txt）
  C. 修改生产参数重新生成

请告诉我下一步要做什么。
```

**禁止**：
- 主动说"建议进入 Phase 2"或"直接生产吧"
- 不等用户回复直接进入下一步
