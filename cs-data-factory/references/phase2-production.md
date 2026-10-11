# Phase 2 生产执行规范

> 本文档供主 Agent 执行 Phase 2（数据生产 + 后处理）时参考。  
> Phase 1 校验通过后，主 Agent 先向用户确认生产模式（SubAgent / API），再调度执行，完成后执行后处理展平。

---

## Step 3.5. 生产模式确认（必须，不可跳过）

**Phase 1 校验通过后，Agent 必须向用户展示以下提示并等待回复，收到明确选择后才可启动生产。不得静默直接进入 SubAgent 模式。**

### 程序化强制机制（Hard Gate）

> ⚠️ **本步骤有程序化门槛兜底，文档规定与脚本联动双重保障。**

**两步操作，缺一不可：**

**第一步**：运行以下命令展示选项（不执行任何生产，exit 0），将输出直接展示给用户：

```bash
python scripts/confirm_production_mode.py --run-dir <run_dir>
```

**第二步**：收到用户明确选择后，再运行对应命令写入 `exec_mode.json`：

```bash
# 用户选 A（SubAgent 模式）：
python scripts/confirm_production_mode.py --run-dir <run_dir> --mode A

# 用户选 B（API 模式）：
python scripts/confirm_production_mode.py --run-dir <run_dir> --mode B --appid <AppId> --model <ModelName>
```

`postprocess.py` 在执行前强制检查 `exec_mode.json` 是否存在，不存在则报错退出，防止跳过确认直接后处理。

---

### 模式说明

向用户展示以下内容，等待回复：

```
Phase 1 校验通过，共 {N} 个 task，准备启动生产。请选择生产模式：

A. SubAgent 模式（默认）
   由 Agent 框架内置模型并发生产，无需额外配置，直接启动

B. API 模式
   指定特定模型（如 your-model）调用模型 API生产
   需提供：AppId（模型 API Key）和模型名称

请回复 A 或 B，或直接回复"A"/"默认"使用 SubAgent 模式。
```

- 用户选 **A**（或回复"默认"）→ 执行 Step 4（SubAgent 模式）
- 用户选 **B** → 追问 AppId 和模型名称，然后执行 Step 4-B（API 模式）
- 两种模式互斥，同一次 run 只能选其一；`run_batch_api.py` 会自动写入 `exec_mode.json` 防止混用

---

## Step 4. 主 Agent SubAgent 并发生产

主 Agent 读取 `prompts_todo.jsonl`（只取 `task_id` 列表），用 `task` 工具分批 spawn SubAgent 并发执行对话生成。

> ⚠️ **主 Agent 读取 `prompts_todo.jsonl` 时只提取 `task_id` 字段列表，不加载 `prompt` 字段正文到主 session。**  
> prompt 正文已由 Phase 1 独立写入 `subagent_prompts/{task_id}.prompt.txt`，SubAgent 直接读文件即可。

### SubAgent 调度 Prompt 模板

主 Agent 照此向每个 SubAgent 下达任务：

```
你的任务是读取以下文件中的对话生成 Prompt，执行生成，并将结果写入指定输出文件。

输入文件：<run_dir>/subagent_prompts/<task_id>.prompt.txt
输出文件：<run_dir>/subagent_prompts/<task_id>.result.txt

执行步骤：
1. 读取输入文件的完整内容（这是一条完整的对话生成 Prompt，直接执行即可）
2. 严格按 Prompt 中的要求生成对话数据（JSON 数组格式）
3. 将生成结果（纯 JSON 数组，不加任何说明文字）直接写入输出文件
4. 写入完成后输出「✅ <task_id> 生产完成」，不要输出 JSON 内容本身

【JSON 字符安全规则（必须遵守）】
- 话术 content 字段内若需表示引号，必须使用中文引号（「」）或转义为 \"，禁止嵌套 ASCII 直双引号（"）
- 其他字符串字段同理：任何会破坏 JSON 结构的字符须转义或替换，确保输出文件可被 json.loads() 直接解析
```

### 分批编排规范（保持主 session 上下文干净）

- 主 Agent 只读取 `prompts_todo.jsonl` 的 `task_id` 列表，**不加载 prompt 正文**
- `generate_prompts.py` 已将每条 prompt 写入独立文件（`subagent_prompts/{task_id}.prompt.txt`）
- 每批 spawn ≤ 10 个 SubAgent，等全批完成后再 spawn 下一批
- 每批完成只汇报进度（"第 X 批完成，共 Y/Z"），不把结果正文输出到主 session
- 200 任务以上建议分多次会话执行（如按 Persona 分批：先跑前半部分 Persona，下次跑后半部分）

### 主 Agent 在全批完成后的检查点

- `subagent_prompts/` 目录下每个 `task_id` 都有对应 `.result.txt` → 继续 Step 5
- 有缺失的 `.result.txt` → 记录 error list，执行完 Step 5 后按恢复策略补跑

---

## Step 4-B（可选）：API 模式批量生产

> 适用场景：需要指定特定模型（如 your-model）生成话术。  
> 前置条件：已完成 Phase 1，`subagent_prompts/task_ids.json` 存在。  
> ⚠️ **与 Step 4（SubAgent 模式）互斥，同一次 run 只能选其一。**

```bash
python scripts/run_batch_api.py \
    --run-dir data/runs/run_YYYYMMDD_NNN \
    --appid <模型 API Key> \
    --model your-model \
    [--workers 4]

# 只重跑指定 task（断点续跑或修复失败）：
python scripts/run_batch_api.py \
    --run-dir data/runs/run_YYYYMMDD_NNN \
    --appid <AppId> --model your-model \
    --task-ids P001_P1_001,P001_P1_002
```

- 读取 `subagent_prompts/task_ids.json` 中的任务列表，逐个调用 API，结果写入同名 `result.txt`
- 断点续跑：已存在 `result.txt` 的 task 自动跳过，中断后直接重跑即可
- 首次运行自动写 `exec_mode.json`（mode=api）；已有 `mode=subagent` 时报错退出，防止混用
- 完成后流程与 SubAgent 模式完全一致，继续执行 Step 5（`postprocess.py`）

---

## Step 5. 后处理展平

> ⚠️ **`postprocess.py` 接受的是 `result.txt`（SubAgent 写入的原始 JSON 数组文件），不接受 `result.json` 或其他格式。**  
> SubAgent 必须将结果写入 `subagent_prompts/{task_id}.result.txt`（`--run-dir` 下的 `subagent_prompts/` 目录）。  
> `postprocess.py` 只接受 `--run-dir` 参数，不支持 `--sop-id`。

```bash
python scripts/postprocess.py \
    --run-dir data/runs/run_YYYYMMDD_NNN \
    [--dry-run]
```

- 读取所有 `result.txt` → 解析 JSON → `split_trajectory` 展平为逐轮行
- 输出：`data/runs/run_YYYYMMDD_NNN/final.jsonl` + 归档到 `data/generated/`
- 支持 `--dry-run` 只打统计不写文件

---

## Step 6. 节点校验（推荐）

```bash
# 单批次校验（含动作合规性、session 统计）
python harness_verify/verify_output.py --run-dir data/runs/run_YYYYMMDD_NNN --sop-id <sop_id>

# 跨批次批量扫描（扫 data/runs 下所有 final.jsonl）
python harness_verify/verify_output.py --scan-dir data/runs --sop-id <sop_id>

# 输出 JSON 报告（供自动化流程消费）
python harness_verify/verify_output.py --run-dir data/runs/run_YYYYMMDD_NNN --sop-id <sop_id> --json
```

> `verify_output.py` 对 `final.jsonl` 做格式校验，**不修改数据**。校验发现 error 时回溯到 Step 4（SubAgent 重生产）或 Step 5（postprocess 重展平），根据 error 类型判断。

---

## Step 7（可选）：导出 Excel

**校验通过后，Agent 必须主动向用户询问是否需要导出 Excel，等待用户明确回复（需要 / 不需要）后再决定是否执行导出。不得静默跳过此问题直接结束流程。**

校验通过后，可将 `final.jsonl` 导出为带格式的 Excel 文件（便于人工抽查 / 标注 / 共享）：

```bash
python scripts/export_excel.py --run-dir data/runs/run_YYYYMMDD_NNN
# 输出：data/runs/run_YYYYMMDD_NNN/final.xlsx

# 也可指定自定义输出路径
python scripts/export_excel.py --run-dir data/runs/run_YYYYMMDD_NNN --out my_output.xlsx
```

> 依赖：`openpyxl`（`pip install openpyxl`，大多数场景已默认安装）

**文件发送方式**：按 `references/file-delivery.md` 第 2-B 节（Excel 文件）执行（Catclaw 发大象附件；CatPaw/CatDesk 提供绝对路径；环境不明时同时输出两种）。

---

## 部分 SubAgent 失败时的恢复策略

1. 运行 `python scripts/postprocess.py --run-dir data/runs/<run_id> --dry-run` —— 查看缺失 result.txt 与解析错误列表
2. 根据 error list 找出缺失或解析失败的 `task_id`，只补跑缺失 task，不重跑整个批次
3. 重新运行 `python scripts/postprocess.py --run-dir data/runs/<run_id>` 合并结果

> **result.txt 解析失败**（如 JSON 格式错误）：先修复文件内容（检查是否有未转义的双引号），再重跑 `postprocess.py`；无需重新 spawn SubAgent。

---

## 任务完成后清理过程产物（可选）

`postprocess.py` 完成且 `final.jsonl` 验证通过后，`data/runs/<run_id>/subagent_prompts/` 下的所有 `.prompt.txt` 和 `.result.txt` 均为中间产物，可安全删除：

```bash
# 删除单次运行的中间文件（保留 final.jsonl / prompts_todo.jsonl）
python -c "
import shutil, pathlib
d = pathlib.Path('data/runs/<run_id>/subagent_prompts')
[f.unlink() for f in d.glob('*.prompt.txt')]
[f.unlink() for f in d.glob('*.result.txt')]
print('清理完成')
"
```

**何时清理**：确认 `final.jsonl` 已归档到 `data/generated/` 后，Agent 必须向用户询问是否清理中间产物，等待用户明确回复（清理 / 保留）后再执行，不得静默执行删除。

**git 规范**：`.prompt.txt` / `.result.txt` / `data/runs/` 均已加入 `.gitignore`，不会被误提交。每次 `git commit` 应只包含代码、配置、文档变更，不含过程产物。

---

## Phase 2 完成后的交接规范（必须遵守）

**`final.jsonl` 生成且校验通过后，Agent 必须向用户展示以下内容，然后等待用户指令，不得主动推荐任何选项或直接进入下一步。**

展示格式如下：

```
✅ Phase 2 生产完成

当前状态：
  - run_id：<run_id>
  - 生产 session 数：<N> 条
  - final.jsonl：data/runs/<run_id>/final.jsonl

接下来可以：
  A. 导出 Excel（scripts\export_excel.py）
  B. 跑 Phase 3 质检（对本批次打分，评估数据质量）
  C. 直接进行全量生产（生成正式批次）
  D. 什么都不做，先看看数据

请告诉我下一步要做什么。
```

**禁止**：
- 主动说"建议跑质检"或"建议进行量产"
- 不等用户回复直接进入下一步