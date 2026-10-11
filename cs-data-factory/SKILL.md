---
name: cs-data-factory
metadata:
  version: v10.13.2
description: 一个可批量生产客服与用户多轮对话（含动作、信号）的元 Skill。输入 SOP 与真实信号样本，结合可选 Persona、话术规范、情绪规范等配置，全链路自动生产；支持人工交互介入与质检驱动的定向迭代优化，兼容 CatClaw、CatPaw 等 Agent 产品。
---

# CS Data Factory

批量模拟客服与用户对话（含动作、信号）的元 Skill。

**核心设计：**
```
用户提供：SOP.md + 真实信号样本.xlsx（+ 可选：自定义 persona.md / speech-guidelines.md / references/expand-prompt.md / emotion.md）

主 Agent（调度层，不处理数据）：
  → Phase 0 SubAgent-A：读 SOP.md → 解析 → 自查 → 持久化 data/sop/{sop_id}_graph.json
  → Phase 0 SubAgent-B（可选，用户选 B 时与 A 并行）：读真实样本 Excel → 聚类分析 → 生成 data/sop/{sop_id}_persona.md
  → Phase 0 SubAgent-C（必选，A 完成后）：analyze_signals.py → 信号规则草稿 → LLM 评审 → 人工确认 → data/sop/{sop_id}_signal_rules.json
  → Phase 1：generate_prompts.py → prompts_todo.jsonl（用户可检查）
  → Phase 2：spawn SubAgent × N / run_batch_api.py → postprocess.py → final.jsonl

主 Agent 全程只传文件路径，不在内存中保留 prompt/result 正文。
```

**SOP 格式参考：** `references/sop-example.md`（发票场景示例）

**输入清单：**
- `references/sop/场景名.md`（必须）：结构化 SOP，描述决策树节点和动作
- `assets/real_signals/real_signals_sample_{sop_id}.xlsx`（必须）：线上真实信号样本，用于 Persona 分析和信号规则生成；不再直接拼接到 Prompt
- `references/persona.md`（可选）：基础 Persona 定义；默认使用内置 8 类 Persona
- `assets/speech-guidelines.md`（可选）：话术规范（禁用词、字数上限、执行规范等），默认已内置客服话术规范示例
- `references/expand-prompt.md`（可选）：生产 Prompt 的全局扩展规范；默认使用该文件，也可在 Phase 1 通过 `--expand-prompt` 临时覆盖
- `data/sop/{sop_id}_emotion.md`（可选）：场景专属情绪处理规范，按 sop_id 自动匹配后注入 Prompt
- `data/sop/{sop_id}_signal_rules.json`（必须）：已审核入库的信号规则；由 Phase 0-C 生成，Phase 1 据此注入 `{SIGNAL_MOCK_RULES}`

**索引导航：**
- `references/sop-example.md`：SOP 写法示例
- `references/persona.md`：Persona 全量定义与示例
- `references/output-schema.md`：最终产物 `final.jsonl` 的字段 Schema
- `references/checkpoint.md`：全链路 Trace、节点输入输出契约、断点恢复规则
- `references/phase0-sop-parsing.md`：Phase 0 执行手册（SubAgent Prompt 模板 + Step 1~6）
- `references/phase1-prompt-generation.md`：Phase 1 执行手册（参数收集 + 脚本命令 + 校验）
- `references/phase2-production.md`：Phase 2 执行手册（SubAgent 调度 + 后处理 + 恢复）
- `references/phase3-judge.md`：Phase 3 执行手册（质检脚本 + 工作流 A/B/C + 三层协同）
- `references/file-structure.md`：完整目录结构 + 入库说明

---

## ⛔ 执行红线

1. **每条结果立刻写 checkpoint**：结果未落盘不得汇报"完成"
2. **Phase 衔接必须等待用户选择**：每个 Phase 完成后，Agent 必须以进度表形式展示"当前完成状态 + 下一步选项（含 Phase 3 质检、全量生产等）"，**等待用户明确指令后才可执行下一步**。禁止 Agent 直接进入下一步。
3. **参考信号配置（可选）**：在 SOP 文件末尾添加 `## 参考信号表` 章节，列出字段名。Phase 0 解析时 Agent 自动写入 `graph.json` 的 `slot_fields`，Phase 0-A3 信号分析时自动归类为 `slot` 类型，Phase 1 生产时生成 mock 规则注入 prompt。`slot_fields` 为空时 `{SIGNAL_MOCK_RULES}` 为空字符串。
4. **跨平台终端命令规则提示**：

   | 运行环境 | Shell | 多命令分隔符 | 示例 |
   |---|---|---|---|
   | Windows PowerShell | `powershell` / `pwsh` | `;`（分号） | `cd foo ; python bar.py` |
   | Windows CMD | `cmd.exe` | `&`（单 & ） | `cd foo & python bar.py` |
   | macOS / Linux | `bash` / `zsh` / `sh` | `&&` 或 `;` | `cd foo && python bar.py` |

   > ⚠️ **PowerShell 不支持 `&&`**，使用 `&&` 会报"标记不是有效语句分隔符"错误。Agent 在 Windows 环境下必须用 `;` 分隔多条命令，或拆成独立的多次调用。
   >
   > **识别方式**：当前工作空间的 Shell 类型会在对话上下文中出现（`Shell: cmd.exe` 或 `powershell.exe`）。若无法确认，优先使用 Python 脚本完成文件操作，完全规避 Shell 差异（参见下方"跨平台文件操作"原则）。
   >
   > **跨平台文件操作原则**：凡是 copy/rename/move/delete 文件等操作，统一使用 Python `shutil` / `pathlib` 完成，禁止依赖 Shell 的 `cp`/`copy`/`mv`/`move` 命令。
   >
   > **含中文/特殊字符 JSON 参数传递原则**：凡是需要向 Python 脚本传入包含中文、引号或特殊字符的 JSON 内容（如 `--merge-mock-rules`），**禁止使用内联 JSON 字符串形式**（无论单引号还是双引号，在 Windows PowerShell 中均存在词法分析破坏风险）。**必须使用文件路径形式**：先由 Agent 用 write 工具将 JSON 内容写入临时文件（如 `data/tmp/{sop_id}_llm_mock_rules.json`），再传入文件路径参数。此方式在 Windows/macOS/Linux 三平台均可靠，是全局强制规范。

---

## 📋 输入说明

### 必须提供

**SOP.md**：结构化 Markdown，大纲缩进表示决策树。

约定标记（不强制，但建议遵守以提升解析准确率）：
- `[示例|客服：xxx]` — 话术示例（内嵌参考信号：`[参考信号：xxx]`）
- `「话术关联动作：xxx」` — 客服说话时直接触发的后台操作
- `「话术共识动作：xxx」` — 需用户确认才触发的操作

未写 `「话术关联动作」` 或 `「话术共识动作」` 的节点，默认填「无」。

**真实信号样本 Excel**（`assets/real_signals/real_signals_sample_{sop_id}.xlsx`）：线上真实对话历史，用于：
- Phase 0-B：Persona 聚类分析，生成 `data/sop/{sop_id}_persona.md`
- Phase 0-C：信号规则生成，产出 `data/sop/{sop_id}_signal_rules.json`

**信号规则文件**（`data/sop/{sop_id}_signal_rules.json`）：由 Phase 0-C 生成，经人工审核后入库。定义各信号字段的类型（enum/mock）和取值规则；Phase 1 据此注入 `{SIGNAL_MOCK_RULES}`。

### 可选提供

**定制 Persona 文件**（`data/sop/{sop_id}_persona.md`）：由 Phase 0-B 自动生成，或人工撰写。Phase 1 中通过 `--persona-file` 指定；不提供时使用默认 `references/persona.md`。

**话术规范文档**（`assets/speech-guidelines.md`）：禁用词、字数上限、执行规范等；不提供时使用内置默认规范。

**场景专属情绪处理规范**（`data/sop/{sop_id}_emotion.md`）：针对特定场景定制客服情绪响应策略，按 `sop_id` 自动匹配注入 prompt；不提供时跳过。参考示例见 `references/sop/shanguo-no-rider_emotion_example.md`。

---

## 预期输出

最终产物为 `final.jsonl`，每行一条逐轮预测数据。

- 字段定义、格式约束、完整示例：见 `references/output-schema.md`
- 全链路节点输入/输出契约：见 `references/checkpoint.md`

### 全链路 Trace 总览

| 节点 | 输入 | 输出 | 强约束 |
|---|---|---|---|
| Phase 0：SOP 解析 | `references/sop/*.md` | `data/sop/{sop_id}_graph.json` | graph / actions / signal_fields 完整，自查通过 |
| Phase 0：路径枚举 | `data/sop/{sop_id}_graph.json` | `data/paths/{sop_id}_paths.jsonl` | 每行一条 path，字段完整，可被 `verify_paths.py` 校验 |
| Phase 1：Prompt 生成 | paths + persona + prompt 模板 + `signal_rules`（可选） | `data/runs/<run_id>/prompts_todo.jsonl` + `subagent_prompts/*.prompt.txt` | 每个 task 可独立执行；主 Agent 只传文件路径 |
| Phase 1：Prompt 校验 | `prompts_todo.jsonl` + `subagent_prompts/*.prompt.txt` | 校验报告 | 使用 `harness_verify/verify_prompts.py`；校验通过才可启动 SubAgent |
| Phase 2：SubAgent 生产 | `subagent_prompts/{task_id}.prompt.txt` | `subagent_prompts/{task_id}.result.txt` | 单任务单文件输出；结果需可被 postprocess 解析 |
| Phase 2：后处理 | `*.result.txt` | `data/runs/<run_id>/final.jsonl` + `data/generated/*` | 逐轮展平后满足 `output-schema.md` |
| Phase 2：格式校验 | `final.jsonl` | 校验报告 / 错误列表 | 使用 `harness_verify/verify_output.py` |
| Phase 3：生成 Judge Prompt | `final.jsonl`（抽样） | `data/judge_runs/<round>/subagent_prompts/batch_XXX.prompt.txt` | 使用 `judge/scripts/run_judge.py --export-prompts`（批次模式） |
| Phase 3：SubAgent 打分 | `batch_XXX.prompt.txt`（每批含多 session） | `batch_XXX.result.txt`（JSON 数组） | 纯 JSON 输出，字段完整（见 `phase3-judge.md`） |
| Phase 3：汇总报告 | `*.result.txt` | `judge_results.jsonl` + `report.md` | 使用 `collect_subagent_results.py` + `gen_report.py` |
| Phase 4：识别低分 task | `judge_results.jsonl` | `data/judge_runs/<round>/rerun_task_ids.json` | 使用 `judge/scripts/pick_low_score_tasks.py` |
| Phase 4：重跑 prompt | `rerun_task_ids.json` + SOP + Persona | `data/runs/<verify_dir>/subagent_prompts/*.prompt.txt` | 使用 `generate_prompts.py --task-ids` |
| Phase 4：对比报告 | 两轮 `report.md` + `final.jsonl` | `compare_report.md` | Agent 生成，含话术对比和分数对比 |

---

## 🔄 快速移植到新场景

本 Skill 设计为元 Skill，理论上支持任意客服场景。移植时需替换以下文件：

| 步骤 | 文件/操作 | 说明 |
|---|---|---|
| 1 | 新建 `references/sop/新场景.md` | 遵循现有 SOP.md 格式，描述节点图和动作；在末尾添加 `## 参考信号表` 章节（可选，列出需要 mock 的话术槽变量字段名） |
| 2 | 放入 `assets/real_signals/real_signals_sample_{sop_id}.xlsx` | 真实信号样本为必选输入，用于后续 Persona 分析和信号规则生成；不再直接拼接到 Prompt |
| 3 | 执行 Phase 0-A | LLM 解析 SOP → 生成 `data/sop/{sop_id}_graph.json` |
| 3.5（可选，与步骤3并行） | Phase 0-B：`python scripts/analyze_persona.py --sop-id <sop_id>` 后 spawn SubAgent 执行聚类分析 | 从真实对话历史聚类分析用户行为，生成 `data/sop/{sop_id}_persona.md`；Phase 1 中用 `--persona-file` 指定 |
| 4（必选，步骤3完成后） | Phase 0-C：`python scripts/analyze_signals.py --sop-id <sop_id>` | 分析 Excel 信号，生成草稿 → LLM 评审 → 人工确认 → 复制为 `data/sop/{sop_id}_signal_rules.json` |
| 5 | 更新 `references/persona.md`（若不用步骤3.5） | 若新场景的用户画像不同，调整或新增 Persona 定义；已执行步骤3.5 则跳过 |
| 6 | 更新 `assets/speech-guidelines.md` | 若新场景有不同的话术规范，替换此文件；无特殊要求可保留 |
| 7（可选） | 新增 `data/sop/{sop_id}_emotion.md` | 若新场景需要专属情绪处理规范，可在该路径下配置；不存在时 Prompt 自动跳过 |
| 8 | 执行 Phase 1 生产 | 使用新的 `--sop-id` 参数运行，若有定制 Persona 加 `--persona-file data/sop/{sop_id}_persona.md` |

---

## Phase 0：SOP 初始化（SubAgent 执行，一次性）

> 每次更换 SOP 时重新执行。相同 SOP 只需执行一次，结果持久化。

**执行手册**：`references/phase0-sop-parsing.md`

**调度规范**：
- 主 Agent 并行 spawn 最多两个 SubAgent：SubAgent-A（SOP 解析，必选）、SubAgent-B（Persona 分析，可选）
- SubAgent-A：将 SOP 全文 + 格式参考一并传入，不在主 session 中执行解析；返回「✅ SubAgent-A 完成」
- SubAgent-B：用户选择做 Persona 聚类分析时启动（与 A 并行）；返回「✅ SubAgent-B 完成」
- SubAgent-C：SubAgent-A 完成后必须启动；脚本生成草稿 → Agent LLM 评审 → 人工确认后入库；返回「✅ SubAgent-C 完成」
- A 和 B 均返回后，主 Agent 再执行路径枚举（Step 5~6 见 reference）；SubAgent-C 可与路径枚举并行
- SubAgent 超过重试次数则主 Agent 接手手动修正
- 路径枚举完成后，运行 `python scripts/preview_phase0.py --sop-id <sop_id>` 生成 HTML 审查报告，让用户对 Graph / 信号规则 / Persona 三类产物进行人工确认；按 `references/file-delivery.md` 第 2-A 节发送报告；**用户明确确认无误后才可进入 Phase 1**
- 用户反馈修正请求时，Agent 直接修改对应 JSON/md 文件 → 重新 verify → 重新生成 HTML 报告，循环直到用户确认（详见 `references/phase0-sop-parsing.md` Step 7）

---

## Phase 1：生产准备

**执行手册**：`references/phase1-prompt-generation.md`

**调度规范**：
- Phase 1 全程由脚本完成，不调用 LLM
- 产物校验（`verify_prompts.py`）通过后才可启动 SubAgent

---

## Phase 2：生产执行

**执行手册**：`references/phase2-production.md`

**调度规范**：
- **默认走 SubAgent 模式**；如需指定特定模型（如 your-model），可切换为 API 模式（见执行手册 Step 4-B）
- 主 Agent 只读取 `prompts_todo.jsonl` 的 `task_id` 列表，**不加载 prompt 正文**
- 每批 spawn ≤ 10 个 SubAgent，每批完成只汇报进度，不输出结果正文

---

## Phase 3：质检（可选）

**执行手册**：`references/phase3-judge.md`

**触发**：告知 Agent "对 `<run_id>` 批次跑质检"，或"跑一轮质检"

**调度规范**：
- ⚠️ **进入 Phase 3 后，必须先询问用户选择 Judge 执行模式（SubAgent 或 API），不得假设或默认，也不得复用 Phase 2 的模式选择**
- Judge SubAgent 与生产 SubAgent 同等规范——只传文件路径，不加载正文，分批 ≤ 10
- 生产侧（Phase 2）和 Judge 侧（Phase 3）的模式独立配置，可自由组合，互不干扰

---

## Phase 4：迭代验证（可选，Phase 3 后触发）

**执行手册**：`references/phase3-judge.md` 中的"流程 D：优化验证重跑"

**触发**：质检报告和根因分析完成后，用户说"跑验证"、"验证这轮优化"、"用低分 case 重跑" 等

**核心流程**：
1. `python judge/scripts/pick_low_score_tasks.py --round <round> --batch <run_dir>` → 生成 `rerun_task_ids.json`
2. 用户（或 Agent）执行优化（修改 prompt 模板 / persona / signal_rules）
3. `python scripts/generate_prompts.py --sop-id <sop_id> --run-dir <verify_dir> --task-ids <task_ids>` → 只为低分 task 重新生成 prompt
4. 按原生产模式（SubAgent 或 API）重跑生产 → postprocess.py
5. 对新批次跑质检 → 收集两轮 report.md + final.jsonl → Agent 生成 compare_report.md（含话术对比和分数对比）
6. 用户决策：采用 / 回退 / 继续调整

**调度规范**：同 Phase 2 SubAgent 规范；重跑只针对低分 task（5-10 条），不全量重跑；模式（SubAgent/API）与原批次保持一致。

---

## Persona 说明

`references/persona.md` 是随 Skill 附带的基础 Persona 选项集，覆盖常见 8 类用户画像（急迫催促型、信息缺失型、情绪化抱怨型、强维权型等），可直接使用。

实际落地时，Persona 完全支持按需定制：
- **按场景独立维护**：为每个 sop_id 建立 `data/sop/{sop_id}_persona.md`，与基础库互不干扰；Phase 1 通过 `--persona-file` 指定
- **从线上数据分析生成**：通过 Phase 0 SubAgent-B 对真实对话样本聚类，自动产出场景专属 Persona
- **手动捏造**：触发「自定义 Persona」流程，Agent 提供模板，用户填写后自动追加入库
- **合并大库**：如需跨场景共享，可将多个场景的 Persona 合并维护为统一库，Phase 1 通过 `--persona-file` 指定

核心原则：**Persona 只控制"怎么说"，不控制"走哪条路径"**

---

## 注意事项

- **跨平台终端命令**：详见执行红线第 4 条（平台命令分隔符对照表 + 跨平台文件操作原则）
- 动作枚举以 `data/sop/{sop_id}_graph.json` 的动作表为准，不可自行发明
- 语义质检（Phase 3）由本 Skill 下的 `judge/` 子模块负责，本 Skill Phase 2 只做格式检验
- 需要调整并发数：`--workers N`；仅验证 prompt：在 `generate_prompts.py` 加 `--dry-run`

### 用户自定义入口总览

用户在任意阶段均可触发以下自定义流程，Agent 立即响应，完成后继续之前的工作。

---

**① 自定义 Persona**（触发词：「自定义 Persona」/「新增 Persona」/「捏一个 Persona」/「加一类用户」）：
- Agent 输出标准 Persona 模板，用户填写后 Agent 自动转换格式并追加到 Persona 文件（场景定制 `data/sop/{sop_id}_persona.md` 优先，否则写 `references/persona.md`）
- 追加后重新生成 HTML 报告，供用户在 Persona 标签页确认格式
- Phase 1 中可用 `--personas PXX` 单独测试新增 Persona
- ⚠️ **Persona 配比/权重**（规划中）：当前 `generate_prompts.py` 对所有 Persona 均等采样。后续版本计划支持在命令行传入 `--persona-weights "P1:3,P2:1"` 进行加权随机分配，实现千人千面的配比策略；在此之前如需偏重某类 Persona，可通过增加重复卡片数量的方式变通实现。

**② 自定义路径**（触发词：「自定义路径」/「新增路径」/「捏一条路径」/「加一个特殊 case」/「边缘路径」）：
- Agent 展示可用动作 ID 清单，输出标准路径模板，用户填写后 Agent 构造骨架 JSON 追加到 `data/paths/{sop_id}_paths.jsonl`
- 自定义路径统一用 `CUST_XXX` 作为 `path_id`（序号自动递增），并标记 `_custom: true`
- 若后续重新运行 `enumerate_paths.py`，Agent 负责将 CUST_ 路径重新追加回 paths.jsonl
- Phase 1 中可用 `--paths CUST_001` 单独生产该路径

**③ 自定义生产 Prompt 全局规范**（触发词：「调整 Prompt 模板」/「修改生产规范」/「改情绪处理策略」）：
- 修改 `references/expand-prompt.md`（全局追加说明，不影响核心骨架逻辑）：适合添加情绪处理指导、输出风格约束、字数要求等通用约束
- 修改 `assets/speech-guidelines.md`（话术规范）：适合添加/删除禁用词、执行规则等
- 修改 `data/sop/{sop_id}_emotion.md`（场景专属情绪规范）：适合为特定 SOP 定制情绪响应策略
- 以上文件修改后，需重新执行 Phase 1（`generate_prompts.py`）才可生效

**④ 自定义 Judge Prompt / 评分标准**（触发词：「调整质检维度」/「改打分标准」/「迭代 Judge Prompt」）：
- 质检 Prompt 以版本号命名，存放于 `judge/prompts/judge_prompt_vX.X.md`；评分 Rubric 存于 `judge/rubric/rubric_vX.X.md`
- 可直接修改当前版本文件，或复制为新版本后在 `run_judge.py` 中以 `--judge-version` 指定版本
- 修改后建议通过 Phase 3 质检流程 + `judge/regression/` 回归验证，确保评分方向一致性
- 详细执行规范见 `references/phase3-judge.md`

> 自定义 Persona 和路径的详细执行规范（模板格式 + 校验约束 + 文件写入逻辑）见 `references/phase0-sop-parsing.md` Step 7-6。

---

## 数据质量问题定位指南

用户反馈生成数据质量问题时，Agent 应先判断问题类型，再定位到对应的入口文件进行分析和优化。以下是常见问题类型与处理路径的对应关系。

### 问题类型 → 定位入口

**用户侧表现不真实（口语化不足、情绪单调、行为单一、缺少连发/催促/抱怨等）**
- 定位：`references/persona.md`（或场景定制 `data/sop/{sop_id}_persona.md`）
- 检查该 Persona 的语言特征、典型行为、示例说法是否足够具体；必要时补充新的行为特征或示例

**多条对话区分度低（轮次相同、开场雷同、措辞高度相似）**
- 定位：`references/expand-prompt.md` → `## 约束` 章节的输出要求和第2/3条规则
- 检查是否已有"总轮次数不同""开场方式不同"等约束；若无则补充

**客服话术有问题（套话多、不承接用户具体内容、情绪处理生硬、结束语千篇一律）**
- 定位：`references/expand-prompt.md` → `## 约束` 第3条（客服话术生成规则）
- 也可检查 SOP 文件（`references/sop/{sop_id}.md`）中各节点内嵌的话术示例是否足够多样，提供更好的参考锚点

**全局行为约束缺失（如缺少某种用户行为类型、某种客服应对场景未覆盖）**
- 定位：`references/expand-prompt.md` → `## 约束` 章节，按需在第2条（用户规则）或第3条（客服规则）补充新的子规则
- 同时在 `references/persona.md` 对应 Persona 里补充相关特征

**客服话术规范违规（使用了禁用词/禁用标点/推诿语气等）**
- 定位：`assets/speech-guidelines.md`
- 检查禁用词/禁用标点/禁止行为是否已涵盖；必要时追加条目

**特定场景的情绪处理不当（某类路径下客服应对用户情绪的方式有问题）**
- 定位：`data/sop/{sop_id}_emotion.md`（场景专属情绪规范）
- 若文件不存在，可参考 `references/sop/shanguo-no-rider_emotion_example.md` 新建

### 定位流程建议

1. 拿到用户描述的问题，先判断是「用户侧」还是「客服侧」还是「全局结构」问题
2. 读取对应入口文件的相关段落
3. 对比现有规则与问题现象，找出缺失或表述不清的约束
4. 修改文件后，重新执行 Phase 1（`generate_prompts.py`）regenerate prompts，再跑小批量生产验证效果
5. 若问题涉及质检评分，还可结合 Phase 3 质检报告定位低分 case，通过 Phase 4 迭代验证

---

## 🔖 版本发布 Hook

**每次修改 Skill 内容后，在 commit 之前必须完成以下 checklist。**

> 这不是建议，是发布流程的一部分。跳过任意一项都可能导致版本失控或外部记忆脱节。

### Checklist

- [ ] **1. 更新版本号**
  - `SKILL.md` frontmatter 中的 `metadata.version` 字段递增（遵循 semver：重大重构 → major，新功能 → minor，修复/文档 → patch）

- [ ] **2. 更新 CHANGELOG.md**
  - 在 `CHANGELOG.md` 中新增本次版本条目（代码层 + 文档层分开列）
  - `SKILL.md` 只维护 frontmatter 中的 `metadata.version` 字段，不在正文中重复维护版本详情

- [ ] **3. git commit**
  - 格式：`feat/fix/chore/docs: <简短描述>`
  - 不要攒着，改一次提一次

### 版本号规则

| 场景 | 变更类型 | 示例 |
|---|---|---|
| 重大架构重构、核心流程变更 | major（+1.0） | v9.x → v10.0 |
| 新增功能、新增脚本/子模块 | minor（+0.1） | v10.0 → v10.1 |
| Bug 修复、文档更新、小优化 | patch（+0.x） | v10.2 → v10.3 |

---

## CHANGELOG

完整变更历史见 [CHANGELOG.md](./CHANGELOG.md)
