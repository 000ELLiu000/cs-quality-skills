# Phase 3 质检执行规范

> 本文档供主 Agent 执行 Phase 3（质检）时参考。  
> Phase 3 为可选流程，在生产数据（`final.jsonl`）生成后触发。  
> **评分粒度：session 维度。** 每个 session 独立产出一条评分结果；但 SubAgent 调度时为控制上下文与并发，多个 session 会被打包到同一个 `batch_XXX.prompt.txt` 中执行。

---

## 触发方式

- 告知 Agent "对 `<run_id>` 批次跑质检"，或"跑一轮质检"
- 输出位置：`data/judge_runs/<round>/`
- Judge 版本：默认最新，可指定（`--judge-version v1.x`）

---

## 步骤 0：Judge 模式选择（必须先询问用户）

> ⚠️ **强制执行：Agent 在做任何打分操作之前，必须先询问用户选择 Judge 执行模式，不得假设或默认。**

### 程序化强制机制（Hard Gate）

> ⚠️ **本步骤有程序化门槛兜底，文档规定与脚本联动双重保障。**

**两步操作，缺一不可：**

**第一步**：运行以下命令展示选项（不执行任何打分，exit 0），将输出直接展示给用户：

```bash
python scripts/confirm_judge_mode.py --round <round_name>
```

**第二步**：收到用户明确选择后，再运行对应命令写入 `exec_mode.json`：

```bash
# 用户选 A（SubAgent 模式）：
python scripts/confirm_judge_mode.py --round <round_name> --mode A

# 用户选 B（API 模式）：
python scripts/confirm_judge_mode.py --round <round_name> --mode B --appid <AppId> --model <ModelName>
```

`run_judge.py` 在执行前强制检查 `exec_mode.json` 是否存在（dry-run 豁免），不存在则报错退出，防止跳过确认直接打分。

---

### 模式说明

**Agent 必须向用户发出以下询问（在对话中直接展示，等待用户回复）：**

```
请选择本轮质检（Judge）的执行模式：

A. SubAgent 模式（推荐）
   导出 prompt 文件 → 调度 SubAgent 执行打分 → collect_subagent_results.py 汇总
   适合：无需额外 AppId 配置，利用当前 Agent 能力打分

B. API 模式
   直接调用 兼容 Chat Completions 的模型 API（需要 AppId 和 Model）→ 结果实时写入
   适合：需要用指定模型（如 your-model）执行质检
   AppId 传入方式（命令行优先）：
     --appid <your-appid>          # 当次有效
     set CS_FACTORY_APPID=xxx      # Windows CMD 临时环境变量
    export CS_FACTORY_APPID=xxx   # macOS / Linux 环境变量
```

用户选择后，记录模式并继续对应步骤。两种模式互斥，同一 round 只能选其一。

---

## 脚本入口（SubAgent 模式）

> - **SubAgent 模式**：用 `--export-prompts` 导出 prompt 文件 → Agent spawn SubAgent 打分 → `collect_subagent_results.py` 汇总  
> - **API 模式**：用 `--appid` 直接调用 兼容 Chat Completions 的模型 API 打分 → 结果实时写入 `judge_results.jsonl`，无需步骤 2/3  
>
> ⚠️ **两种模式互斥，同一 round 只能选其一（由 `exec_mode.json` 运行时锁定）。**  
> 生产侧（Phase 2）和 Judge 侧（Phase 3）的模式独立配置，可自由组合。

### 步骤 1：生成 Judge Prompt 文件（不调 LLM）

```bash
python judge/scripts/run_judge.py \
    --batch data/runs/<run_id> \
    --round tuning_round_N \
    --judge-version v1.0 \
    --n -1 \
    --export-prompts
```

输出 `data/judge_runs/<round>/subagent_prompts/batch_XXX.prompt.txt`，每个批次文件包含 N 个 session（默认 N=5，可通过 `--batch-size` 调整）。

### 步骤 2：主 Agent 调度 SubAgent 执行打分

主 Agent 读取 `data/judge_runs/<round>/subagent_prompts/batch_ids.json` 中的批次 ID 列表，spawn SubAgent 并发执行打分，每个 SubAgent 处理一个批次文件（内含多个 session），完成后写入 `batch_XXX.result.txt`。

**SubAgent 调度 Prompt 模板（主 Agent 照此向每个 SubAgent 下达打分任务）：**

```
你的任务是读取以下批次打分文件，对其中所有 session 进行质检评分，并将结果以 JSON 数组格式写入输出文件。

输入文件：<round_dir>/subagent_prompts/<batch_id>.prompt.txt
输出文件：<round_dir>/subagent_prompts/<batch_id>.result.txt

执行步骤：
1. 读取输入文件的完整内容（内含多个 session 的打分任务，每个 session 独立评分）
2. 严格按每个 session 对应的 Rubric 评分
3. 输出一个 JSON 数组，每个元素对应一个 session 的评分结果，顺序与输入一致
4. 将 JSON 数组直接写入输出文件（纯 JSON，不含说明文字或 markdown 标记）
5. 写入完成后输出「✅ <batch_id> 打分完成（N 个 session）」

输出 JSON 格式（JSON 数组，每个元素是一个 session 的整体评分）：
[
  {
    "session_id": "session 的实际 ID（从 prompt 中读取）",
    "action_tags": ["本 session 命中的服务动作标签"],
    "emotion_level": "low / mid / high / none 四选一",
    "dimension_scores": {
      "<维度标签_子维度>": {
        "score": 实际得分,
        "max_score": 满分,
        "fail_tags": ["失分原因"],
        "reason": "一句话说明（禁止在 reason 字段值中使用未转义的双引号）"
      }
    },
    "normalized_score": 0~1 浮点（所有子维度归一化均值）,
    "final_score": 1-5 制得分（保留1位小数）,
    "summary": "本次 session 对话整体质量一句话总结"
  },
  ...
]

【JSON 字符安全规则】reason/summary/fail_tags 字段值中若需引号，必须使用「」或转义为 \"，禁止裸 ASCII 双引号
```

**分批编排规范**：每批 spawn ≤ 10 个 SubAgent（每个 SubAgent 处理一个 batch 文件，内含 5 个 session）；每批完成后只汇报进度，不输出打分结果正文。

### 步骤 3：汇总结果

```bash
python judge/scripts/collect_subagent_results.py \
    --round tuning_round_N
```

自动修复 result.txt 中常见 JSON 格式问题，输出 `data/judge_runs/<round>/judge_results.jsonl`。

---

## 脚本入口（API 模式）

> 适用场景：需要用指定模型（如 your-model）执行质检评分。  
> ⚠️ **与 SubAgent 模式互斥，同一 round 只能选其一。**

```bash
python judge/scripts/run_judge.py \
    --batch data/runs/<run_id> \
    --round tuning_round_N \
    --judge-version v1.1 \
    --n -1 \
    --appid <模型 API Key> \
    --model your-model \
    [--workers 4]
```

- 直接调用 API 打分，打分结果实时写入 `judge_results.jsonl`
- **跳过步骤 2（SubAgent spawn）和步骤 3（collect_subagent_results.py）**，直接进步骤 4
- 断点续跑：已打分的 session 自动跳过（基于内部 `judge_progress.json`）
- 首次运行自动写 `exec_mode.json`（mode=api）；若该 round 已使用 SubAgent 模式则报错退出，防止串台

完成后直接执行步骤 4（`gen_report.py`）。

---

### 步骤 4：生成质检报告

```bash
python judge/scripts/gen_report.py \
    --round tuning_round_N
```

输出 `data/judge_runs/<round>/report.md`，报告含以下章节：
- 总览、各维度通过率、Fail Tags TOP5、动作标签分布
- 最差 Sessions 列表（待人工复核）
- **第七章：低分问题清单**（v1.1 起，含 reason 原文证据 + 对话原文折叠块，供人工 review 对照 LLM 打分结论）

**文件发送方式**：按 `references/file-delivery.md` 第 2-C 节（Markdown 报告）执行（Catclaw 发大象附件；CatPaw/CatDesk 提供绝对路径；环境不明时同时输出两种）。

> 脚本末尾已自动打印绝对路径，Agent 须将该路径原文告知用户（不得截断）。

### 步骤 5：生成根因分析 Prompt（v1.1 新增）

```bash
python judge/scripts/analyze_root_cause.py \
    --round tuning_round_N \
    [--top-n 8]
```

输出 `data/judge_runs/<round>/root_cause_prompt.txt`，供流程 A 使用。

### 回归测试（版本对比，回归集建立后使用）

```bash
python judge/scripts/run_regression.py \
    --version-a v1.0 --version-b v1.1
```

输出 `judge/regression/results/<timestamp>_<version>_baseline.md`（基线）或 `..._diff.md`（版本对比）。

**文件发送方式**：按 `references/file-delivery.md` 第 2-C 节（Markdown 报告）执行。

---

## 文件结构

```
judge/
├── rubric/
│   ├── rubric_v1.0.md           # 评分标准（基础版）
│   ├── rubric_v1.2.md           # 评分标准（v1.2：新增 J/K 维度）
│   └── CHANGELOG.md
├── prompts/
│   ├── judge_prompt_v1.0.md     # Judge Prompt 模板（{{字段名}} 占位符）
│   ├── judge_prompt_v1.2.md     # Judge Prompt 模板（v1.2：含 persona/skeleton 上下文）
│   └── CHANGELOG.md
├── regression/
│   ├── regression_set.jsonl     # 固定回归集（带 ground_truth，第一轮调优后建立）
│   └── results/                 # 回归测试历史结果
├── scripts/
│   ├── run_judge.py             # 抽样 + 生成 prompt 文件（--export-prompts 推荐）
│   ├── collect_subagent_results.py  # 汇总 SubAgent result.txt → judge_results.jsonl
│   ├── gen_report.py            # 生成质检报告（v1.1 低分证据 / v1.2 persona 分组统计）
│   ├── pick_low_score_tasks.py  # 低分 session 筛选（v1.2 支持 persona/dimension 过滤）
│   ├── analyze_root_cause.py    # 根因分析 Prompt 生成器（v1.1 新增）
│   └── run_regression.py
└── annotation-guide.md          # 人工标注说明

data/judge_runs/                 # Judge 运行产物（动态文件）
└── tuning_round_N/
    ├── samples.jsonl            # 本轮抽样
    ├── subagent_prompts/        # judge prompt 文件 + SubAgent 打分结果（批次格式）
    │   ├── batch_ids.json           # 批次 ID 列表（run_judge.py 生成，供主 Agent 调度）
    │   ├── batch_001.prompt.txt     # 每个批次文件含多个 session（默认 5 个）
    │   └── batch_001.result.txt     # SubAgent 对应批次的 JSON 数组评分结果
    ├── judge_results.jsonl      # 打分结果（collect_subagent_results.py 输出）
    ├── report.md                # 质检报告（人工决策入口，v1.1 含低分证据）
    ├── root_cause_prompt.txt    # 根因分析 Prompt（analyze_root_cause.py 输出）
    ├── root_cause_analysis.md   # 根因分析结果 + 优化方案草案（Agent 流程 A 输出）
    └── optimization_plan.md     # 人工确认后的最终优化计划（流程 A 输出）
```

---

## Agent 工作流

Phase 3 的分析与决策由 Agent 执行，不写死脚本。以下三个流程按需触发：

---

### 流程 A：基于质检报告生成根因分析与优化策略

**触发**：用户说"分析一下这轮质检报告"或"给优化建议"或"根因分析"

**执行步骤**：
1. 确认 `data/judge_runs/<round>/report.md` 和 `judge_results.jsonl` 已存在
2. 运行根因分析 Prompt 生成器：
   ```bash
   python judge/scripts/analyze_root_cause.py --round <round> [--top-n 8]
   ```
   输出 `data/judge_runs/<round>/root_cause_prompt.txt`
3. 读取 `root_cause_prompt.txt`，按其中的分析任务执行：
   - 对每条失分子维度判断根因类别（多标签：skeleton / prompt_template / signal / persona / general_guideline）
   - 合并同类根因，区分系统性问题（≥2 session）与偶发问题
   - 生成最小优化方案草案（只针对系统性问题）

   根因类别定义（与 SKILL.md 数据质量问题定位指南对齐）：

   | 类别 | 含义 | 对应 SKILL.md 问题类型 | 修改入口文件 |
   |---|---|---|---|
   | skeleton | 骨架问题：路径节点定义/检查点/话术示例本身有问题 | 全局行为约束缺失 | graph.json 或 sop 骨架文件 |
   | prompt_template | Prompt 模板问题：expand-prompt.md 的约束/规则/示例表达不清晰或缺失 | 客服话术有问题 / 多条对话区分度低 / 全局行为约束缺失 | `references/expand-prompt.md` |
   | signal | 信号问题：signal_rules.json 的 mock 规则偏差，导致 LLM 生成场景不真实 | — | `data/sop/{sop_id}_signal_rules.json` |
   | persona | Persona 问题：persona.md 描述不准确，或缺少覆盖该情绪/场景的 Persona 类型 | 用户侧表现不真实 | `references/persona.md` |
   | general_guideline | 通用规范问题：话术规范表述不足，或某类问题未覆盖 | 客服话术规范违规 / 特定场景情绪处理不当 | `assets/speech-guidelines.md` 或 `data/sop/{sop_id}_emotion.md` |
4. 将分析结果写入 `data/judge_runs/<round>/root_cause_analysis.md`
5. 向用户呈现摘要（根因分布 + 优化方案草案）。**Agent 必须在此等待用户明确回复后才可执行修改，禁止在用户确认前自行进入流程 C。** 用户可要求修改方案，Agent 调整草案后重新呈现，循环直到用户确认。
6. 用户确认后：将最终优化计划写入 `optimization_plan.md`，进入流程 C 执行修订

---

### 流程 B：分析人工标注表格

**触发**：用户传入人工标注 Excel/CSV

**执行步骤**：
1. 读取人工标注表格（格式不限，自适应解析）
2. 识别表格结构（session 级 or turn 级，列名映射）
3. 分析 LLM 打分 vs 人工打分 diff
4. 识别系统性偏差（哪些维度偏高/偏低）、边界 case（分歧最大的）
5. 生成 Rubric 修订建议 + Judge Prompt 调整建议
6. 等用户确认 → 执行流程 C

---

### 流程 C：Rubric 版本迭代

**触发**：用户确认流程 A/B 的修订建议后

**执行步骤**：
1. 将修订内容写入 `judge/rubric/rubric_v<N+1>.md`
2. 更新 `judge/rubric/CHANGELOG.md`（新增版本条目）
3. 同步更新 `judge/prompts/judge_prompt_v<N+1>.md`（对齐新 Rubric）
4. 更新 `judge/prompts/CHANGELOG.md`
5. 提示：回归集 ground truth 是否需要跟着调整？（给出建议，人工决策）
6. 若有回归集：运行 `run_regression.py --version-a <old> --version-b <new>` 验证

---

### 流程 D：优化验证重跑（v1.1 新增）

**触发**：用户审核 `root_cause_analysis.md` 中的优化方案后说"跑验证"或"验证这轮优化"

**目标**：先生成优化版配置文件（不覆盖原文件），用低分 case 重跑对比新旧效果，用户确认正向后再决定是否覆盖原文件。**绝不在用户确认前修改正式文件。**

**执行步骤**：

1. **确定验证 case**
   - Agent 读取 `root_cause_analysis.md`，提取低分 session 对应的 `path_id × persona_id` 组合
   - 默认取分数最低的 5-10 条（可由用户指定）

2. **生成优化版配置（不覆盖原文件）**
   - 按 `optimization_plan.md` 中确认的优化项，将修改后的内容写入带版本号的**新文件**，原文件保持不变：
     - `expand-prompt.md` 优化 → 写入 `data/judge_runs/<round>/expand-prompt.v2.md`
     - `persona.md` 优化 → 写入 `data/judge_runs/<round>/persona.v2.md`
     - `signal_rules.json` 优化 → 写入 `data/judge_runs/<round>/signal_rules.v2.json`
   - 同时记录改动摘要到 `optimization_plan.md`（改了什么 / 改之前是什么 / 改之后是什么）
   - **⚠️ 此步骤绝不修改 `references/` 或 `assets/` 下的任何正式文件**

3. **生成验证批次（使用优化版配置）**
   ```bash
   python scripts/generate_prompts.py \
       --sop-id <sop_id> \
       --run-dir data/runs/verify_<round>_v<N> \
       --paths <低分path_id列表> \
       --personas <低分persona_id列表> \
       --samples 3 \
       --expand-prompt data/judge_runs/<round>/expand-prompt.v2.md   # 指向优化版
   ```
   仅为低分 case 重跑，samples 取 3 条即可（验证目的，不需要大量）

4. **SubAgent 生产**
   - 主 Agent 调度 SubAgent 对验证批次生产（与正常流程相同）
   - 完成后运行 postprocess.py 整理 final.jsonl

5. **对新批次跑质检**

   ⚠️ **同样必须先询问用户选择质检模式（SubAgent 或 API），不得假设延用生产侧的模式。**

   SubAgent 模式：
   ```bash
   python judge/scripts/run_judge.py \
       --batch data/runs/verify_<round>_v<N> \
       --round verify_<round>_v<N> \
       --judge-version v1.0 \
       --n -1 \
       --export-prompts
   ```
   然后调度 SubAgent 打分 → collect_subagent_results.py 汇总

   API 模式：
   ```bash
   python judge/scripts/run_judge.py \
       --batch data/runs/verify_<round>_v<N> \
       --round verify_<round>_v<N> \
       --judge-version v1.0 \
       --n -1 \
       --appid <AppId> --model <ModelName>
   ```

6. **生成前后对比报告**

   Agent 生成对比报告，输出 `data/judge_runs/verify_<round>_v<N>/compare_report.md`，包含：

   | 比较项 | 说明 |
   |---|---|
   | 得分对比 | 每条低分 case 的旧分数 vs 新分数（维度级） |
   | 整体通过率变化 | 优化前 vs 优化后 |
   | 话术对比 | 主要失分轮次的旧话术 vs 新话术（客服侧 content 字段） |
   | 结论 | 建议采用优化 / 回退 / 继续调整 |

   话术对比格式示例：
   ```
   ### Session P001_P2_001 — 维度 A1（开场未提炼信号）

   **优化前**（score_A1: 0/2 / 旧总分 3.3）：
   > 客服：您好，我来帮您查看一下这笔订单的情况。

   **优化后**（score_A1: 2/2 / 新总分 4.1）：
   > 客服：看到这笔订单目前已完成全额退款，您是想了解发票相关问题吗？

   评价：开场信息提炼明显改善，A1 从 0 提升至满分。
   ```

7. **用户决策（必须等待用户回复后才可执行）**

   Agent 展示对比报告摘要后，等待用户明确回复：

   ```
   对比报告已生成：data/judge_runs/verify_<round>_v<N>/compare_report.md

   优化前均分：X.X → 优化后均分：X.X
   请查看后回复：
     采用   → 将优化版配置覆盖正式文件（或保留两个版本，您来选）
     回退   → 保留原文件不动，丢弃优化版
     继续调整 → 在当前优化版基础上进一步修改
   ```

   - ✅ **采用 + 覆盖**：用优化版文件覆盖正式文件，删除临时版本文件，走流程 C 更新版本号
   - ✅ **采用 + 保留两版本**：将优化版重命名放入 `references/`（如 `expand-prompt-v2.md`），`generate_prompts.py` 用 `--expand-prompt` 参数指定使用哪个版本
   - ⏪ **回退**：优化无效或负向，临时版本文件不删除（留存作参考），在 `root_cause_analysis.md` 注记原因，原正式文件全程未被修改
   - 🔄 **继续调整**：在临时版本文件基础上继续修改，重新执行步骤 3-6

---

### 流程 E：Persona 定向重跑（v1.2 新增）

**触发**：用户说"P1 的表现不好，重跑一下"、"score_J 低的帮我重跑"、"帮我看看哪些 Persona 得分低"

**目标**：针对特定 Persona 或特定维度（score_J / score_K）的低分 session 进行定向筛选和重跑验证。

**前置条件**：本轮质检使用 `--judge-version v1.2`（rubric/prompt 中包含 J/K 维度）

**执行步骤**：

1. **低分筛选**
   - Agent 读取 `data/judge_runs/<round>/judge_results.jsonl`
   - 根据用户意图确定筛选条件（persona_filter / dimension_filter），调用 `pick_low_score_tasks.py` 中的 `pick_low_score_tasks()` 函数：
     ```python
     from judge.scripts.pick_low_score_tasks import pick_low_score_tasks, load_judge_results
     round_dir = Path("data/judge_runs/<round>")
     results = load_judge_results(round_dir)
     # 示例：筛 P1 下 score_J 低分的 session
     items = pick_low_score_tasks(results, threshold=0.6, persona_filter="P1", dimension_filter="score_J")
     ```
   - 将筛选结果展示给用户：session 列表、各维度分数、persona 和 path 信息

2. **确定优化方向**
   - 若 score_J（Persona 忠诚度）低分：问题在 persona.md 定义或 expand-prompt 对 persona 的引用
   - 若 score_K（骨架合规度）低分：问题在骨架 path 定义或 expand-prompt 对骨架的遵循
   - Agent 结合 `report.md` 中的 Persona 分组统计表，定位具体改进点

3. **后续流程**
   - 确定需要优化的文件后，进入流程 D（优化验证重跑）步骤 2 开始执行
   - 或进入流程 A 做完整根因分析（当问题涉及多个 Persona 时推荐）

**注意**：
- 用户不需要知道 `pick_low_score_tasks()` 函数或任何代码细节
- Agent 根据用户自然语言意图（如"P1 表现差"→ `persona_filter="P1"`）自动映射参数
- 若质检版本 < v1.2（无 score_J/score_K 字段），Agent 需提示用户先用 v1.2 重跑质检

---

## 三层协同原则（必须遵守）

Rubric × Judge Prompt × 回归集 版本必须绑定：
- Rubric 改了 → Judge Prompt 跟着调 → 检查回归集 ground truth 是否需要更新
- 三者版本号对齐，不能乱套
- 每次修订后立刻 git commit（一次改动一次提交）

---

## 附录：回归集建立方法

`judge/regression/regression_set.jsonl` 是 `run_regression.py` 的输入，每行是一个带 `ground_truth` 标注的 session 样本。

**建立时机**：第一轮 Judge 跑完、质检报告生成后，从中挑选最具代表性的 10-15 条 session 建立初始回归集。

**挑选原则**：
- 包含不同分数段（低分/中分/高分各取几条）
- 覆盖主要失分维度（至少包含各大类维度的代表 case）
- 至少 3-5 条低分 case（确保新版 Judge Prompt 改进后能检测到提升）

**格式**：从 `judge_results.jsonl` 中取对应 session 的完整数据行，追加一个 `ground_truth` 字段：

```jsonl
{"session_id": "gen_P001_P1_001_s1", "turns": [...], "所有信号": "...", "ground_truth": {"normalized_score": 0.72, "dimension_scores": {"engagement_A1": {"score": 3, "max_score": 5}, ...}, "notes": "失分原因：情绪承接不足"}}
```

**字段说明**：
- `ground_truth.normalized_score`：人工认可的基准分（可从 `judge_results.jsonl` 取 LLM 打分值，或人工修正）
- `ground_truth.dimension_scores`：各维度基准分（可选，不填则只校验总分）
- `ground_truth.notes`：备注该样本的主要特征（便于后续人工复核）

**建立命令**（基线建立）：
```bash
python judge/scripts/run_regression.py \
    --version-a v1.0 \
    --appid <AppId>
# 输出 judge/regression/results/YYYYMMDD_HHMMSS_v1.0_baseline.md
```

**后续版本对比**（新 Judge Prompt 上线前必跑）：
```bash
python judge/scripts/run_regression.py \
    --version-a v1.0 \
    --version-b v1.1 \
    --appid <AppId>
# 输出 judge/regression/results/YYYYMMDD_HHMMSS_v1.0_vs_v1.1.md
```

---

## Phase 3 完成后的交接规范（必须遵守）

**质检报告生成后，Agent 必须向用户展示以下内容，然后等待用户指令，不得主动推荐任何选项或直接进入下一步。**

展示格式如下：

```
✅ Phase 3 质检完成

当前状态：
  - round：<round_id>
  - 质检 session 数：<N> 条
  - 平均分：<avg_score>（满分 1.0）
  - 报告：data/judge_runs/<round_id>/report.md

接下来可以：
  A. 进入 Phase 4（识别低分 task，针对性重跑优化）
  B. 直接进行全量生产（当前质量可接受）
  C. 查看质检报告详情

请告诉我下一步要做什么。
```

**禁止**：
- 主动说"建议进入 Phase 4"或"建议全量生产"
- 不等用户回复直接进入下一步
