---
name: auto-inspection
description: |
  自动化任务产物质检 Skill，对数据生产/数据评测/数据修复/数据质检等任务的产物进行三层自动化质量检查。

  当用户提到以下任一内容时，必须使用此 skill：
  - 自动质检、自动化质检、智能质检、AI质检
  - 产物质检、数据质量检查、标注质量检查
  - 动态 rubric、动态质检标准生成
  - 3C质检、3C合规检查
  - 质检 Skill、inspection skill
  - 对话合规检查、服务标准规范检查

  适用于所有需要自动化质量检查的场景，无论是对客服对话产物、评测报告、修复结果还是质检结论。
---

# Auto-Inspection Skill

对自动化任务的产物进行三层质量检查，输出结构化质检报告。

## 定位

本 Skill 是自动化数据生产平台的产物质量检查原子能力。平台负责抽样和时间调度，Skill 只负责「拿到任务上下文及产物样本 → 执行三层质检 → 返回结构化质检报告」。

## 异常处理

在执行过程中如遇到异常中断（如 "服务异常 LLM error [535]: 模型响应无效：既无工具调用也无内容" 等空响应/无响应错误），按以下规则处理：

1. **不要中止整个质检流程**。LLM 服务异常属于外部依赖偶发故障，不代表质检逻辑错误或数据问题。
2. **保留已完成步骤的结果**。如果前面的步骤已完成，先固化已得出的结论，不要丢弃。
3. **重试当前步骤**。对当前正在执行的步骤重新发起调用，最多重试 2 次。
4. **两次重试后仍失败**：将当前步骤标记为"因服务异常未能完成"，该层检查结果状态设为"服务中断"。在报告中附注中断点（已完成步骤 + 中断步骤），提示用户可从断点处恢复。
5. **向用户说明情况**：告诉用户发生了什么（哪个步骤、什么错误）、已完成了哪些检查、如何恢复（重新运行剩余步骤即可，已完成的不需要重跑）。

## 目录结构

```
auto-inspection/
├── SKILL.md                          ← 本文件（入口调度）
├── references/
│   ├── workflow.md                   ← 端到端工作流与异常处理
│   ├── 3c-standards.md               ← 第一层：3C 合规检查 19 项标准
│   ├── general-dimensions.md         ← 第二层：通用质检四维度
│   ├── dynamic-rubric-guide.md       ← 第三层：动态 Rubric 生成方法论
│   └── risk-rating-rules.md          ← 风险评级规则与输出 Schema
```

## 输入契约

平台传入一个 JSON，包含以下结构：

```json
{
  "task_type": "数据生产 | 数据评测 | 数据修复 | 数据质检",
  "task_goal": "任务目标的自然语言描述（本次审核描述/审核要求）",
  "skill_info": "执行该任务所用 Skill 的说明文档或 Prompt 摘要",
  "reference_knowledge": "（可选）相关标准或补充说明",
  "samples": [
    {
      "sample_id": "样本唯一标识",
      "output": "该样本的实际产物内容"
    }
  ]
}
```

### 输入说明

- `task_type`、`task_goal`、`skill_info` 为必填
- `reference_knowledge` 为可选，缺失时相关检查降级处理（详见 `references/general-dimensions.md` 和 `references/workflow.md`）
- `samples` 数量不设上限，由平台侧控制分批逻辑。平台已完成抽样，Skill 收到的所有样本均需全量检查
- `content_type` 从实际样本内容推断，不再由外部传入

## 执行流程

严格按以下步骤顺序执行，详细规则见对应 reference 文件：

### 步骤 0：信息收集与输入准备（新增）

质检 Skill 启动时**不能假设自己知道任务的任何上下文**。必须先向用户/平台收集以下信息，缺一不可：

#### 0a. 必收集信息

| 信息项 | 说明 | 示例 |
|--------|------|------|
| 任务名称 | 本次任务的标识名称 | "修改订单信息数据生产-示例业务" |
| 任务目标 | 任务要达成什么、为什么生产这批数据 | "生产修改订单信息场景的数据用于模型训练" |
| task_type | 任务类型，决定后续配置发现逻辑 | "数据生产" / "数据评测" / "数据修复" / "数据质检" |
| 数据路径 | 待质检的产物文件位置 | `data/runs/xxx/final.jsonl` |
| 生产 Skill | 执行该任务所用的 Skill 名称 | "cs-data-factory" |
| 生产 Skill 所在目录 | 用于自动发现配置文件 | `{skill_dir}/cs-data-factory/` |

#### 0b. 按 task_type 自动发现配置文件

收集到 task_type 和生产 Skill 所在目录后，根据任务类型自动扫描并读取相关配置。不同 task_type 对应不同的配置文件发现逻辑（而非硬编码文件名）：

**数据生产（data-factory 类）**：
- SOP 文件：`{skill_dir}/references/sop/*.md` 或 SOP 图谱 `data/sop/*_graph.json`
- 信号规则：`{skill_dir}/data/sop/*_signal_rules.json`
- 话术规范：`{skill_dir}/assets/speech-guidelines.md` 或 Skill 内置默认
- 输出 Schema：`{skill_dir}/references/output-schema.md` 或 Skill 内置默认
- Persona 配置：`{skill_dir}/references/persona.md` 或 Skill 内置默认
- 情绪规范：`{skill_dir}/data/sop/*_emotion.md`（可选）

**数据评测**：
- 评测维度定义：`{skill_dir}/references/eval-dimensions.md` 或等效文件
- 评分标准/评分规范：`{skill_dir}/references/scoring-rubric.md` 或等效文件
- 参考样本/Golden Set：`{skill_dir}/assets/golden/*.jsonl`（可选）

**数据修复**：
- 修复规则：`{skill_dir}/references/fix-rules.md` 或等效文件
- 目标 Schema：`{skill_dir}/references/target-schema.md` 或等效文件
- 修复前后对照样本（可选）

**数据质检**：
- 质检维度定义：`{skill_dir}/references/inspection-dimensions.md` 或等效文件
- 质检通过标准：`{skill_dir}/references/pass-criteria.md` 或等效文件

> **原则**：配置文件的具体文件名不做硬编码，而是按目录结构自动发现。"先判断任务类型 → 按类型定位对应目录 → 读取目录下相关文件"这个逻辑是通用的，不同 Skill、不同任务类型都适用。

#### 0c. 输出「质检前置信息确认表」

收集完毕后，以表格形式展示所有收集到的信息，让用户确认无误后再继续。确认表至少包含：

- 任务名称、任务目标、task_type
- 数据文件路径及数据量（总会话数/总条数）
- 已读取的配置文件清单（路径 + 用途）
- 未找到的可选配置（标注为"未配置，对应检查降级"）

> 用户确认后，进入步骤 1。

### 步骤 1：解析输入与适用性判断

- 解析输入数据，提取 samples
- 根据 `task_type` 判断第一层 3C 检查是否启用：
  - **启用 3C**：`task_type` 为"数据生产"或"数据修复"
  - **关闭 3C**：`task_type` 为"数据评测"或"数据质检"
  - 3C 检查的定位是「判断数据生产/修复任务生成的客服回复是否符合 3C 质量标准」。数据评测和数据质检任务不涉及客服回复内容的生产，因此不启用 3C 检查
- 异常场景处理参见 `references/workflow.md`
- **注意**：本 Skill 收到的数据已是平台前置筛选后的「全检范围」，无需再做抽样决策，对所有样本执行全部维度检查

### 步骤 2：第一层 — 3C 合规检查

- 执行标准：`references/3c-standards.md`
- 共 19 项检查维度，分三档：红牌（3 项）、黄牌（7 项）、蓝牌（9 项）
- 逐样本、逐维度扫描，记录触发的违规项及其证据
- 汇总规则：红牌或黄牌 → 不通过；仅蓝牌 → 需关注；无问题 → 通过

### 步骤 3：第二层 — 通用质检维度

- 执行标准：`references/general-dimensions.md`
- 四个维度：
  - **准确性**：产出结果是否与事实/标准答案/正确结论相符
  - **遵循性/忠诚度**：结果是否基于给定的背景知识、标注标准、参考 wiki 得出，做到有据可依
  - **完整性**：必填字段/标注项是否都覆盖，无遗漏
  - **格式合规性**：输出是否符合约定结构/格式规范
- 每个维度独立判定：通过 / 需关注 / 不通过
- 缺失 `reference_knowledge` 时遵循性/忠诚度检查降级处理

### 步骤 4：第三层 — 动态 Rubric

> **强制执行**：无论前两层（3C、通用维度）的检查结果如何、覆盖了哪些内容，步骤 4 必须执行，不可跳过。动态 Rubric 是三层质检中不可缺少的独立一层。

#### 4a. 生成 Rubric

- 执行标准：`references/dynamic-rubric-guide.md`
- **依赖完整生产上下文**，而非仅依赖 `task_context` 的文字描述：
  - `task_context`（任务类型、目标、Skill 说明、任务标准、产物类型）——提供任务层面的元信息
  - **步骤 0b 中按 task_type 自动发现的全部配置文件内容**（如 SOP、信号规则、话术规范、输出 Schema、Persona 等）——提供生产执行的完整细节
- 生成 3-5 个专属质检维度，每个维度含检查要点、通过标准、权重
- 生成后执行自检确保维度贴合任务
- **留痕要求**：生成的 Rubric 维度必须完整写入 `layer_3_dynamic_rubric.generated_dimensions` 字段，包括维度名称（dimension_name）、检查要点（check_points）、通过标准（pass_criteria）、权重（weight），四项缺一不可。不得仅在脑中生成后跳过输出直接执行检查

> 配置文件的读取发生在步骤 0b，步骤 4a 直接使用已读取的内容。不同 task_type 对应不同的配置文件发现逻辑（见步骤 0b），保证了跨任务类型的通用性。

#### 4b. 按 Rubric 执行检查

- 用生成的 Rubric 逐样本检查
- 每个维度逐样本判定：pass / fail
- 记录不通过样本的问题描述和证据

### 步骤 5：综合风险评级

- 执行标准：`references/risk-rating-rules.md`
- 三层检查各自独立评定风险等级，最终取三层的最高风险等级
- 评级结果写入 `result.risk_level`

### 步骤 6：输出质检报告

- 严格按照 `references/risk-rating-rules.md` 中的「完整输出 JSON Schema」组装报告
- 输出完整的结构化 JSON，不附加任何 JSON 之外的内容

## 核心原则

1. **零上下文假设**：质检 Skill 启动时假设自己对任务一无所知，必须通过步骤 0 主动收集全部必要信息，不得依赖对话历史或隐含记忆
2. **实事求是**：只报告确实发现的问题，不虚构不夸大
3. **诚实标注**：3C 不适用时如实标注 not_applicable，不强行检查
4. **具体明确**：每个问题必须指明：哪个样本（sample_id）→ 什么内容（evidence）→ 什么问题（description）
5. **贴合任务**：动态 Rubric 紧扣任务类型，不生成空洞通用维度
6. **格式稳定**：输出 JSON 格式严格不变，方便平台解析
7. **中文输出**：质检报告中所有字段的值必须使用中文，不得出现英文单词。包括但不限于：风险等级（高/中/低/无）、维度状态（通过/需关注/不通过）、处置建议（放行/全量返工/部分返工）、违规严重度（红牌/黄牌/蓝牌/无）、报告中的所有描述性文字
8. **三层独立**：三层检查各自执行，互不跳过，全部完成后统一评级
