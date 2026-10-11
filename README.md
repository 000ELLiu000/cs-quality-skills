# 自动化质检 Skills

这个仓库包含两个独立的 Skill，供 Agent 学习和执行自动化质检。本文重点说明各自的检查逻辑、执行 SOP、判定规则和结果留痕。

| Skill | 质检对象 | 核心方法 | 主要产物 |
|---|---|---|---|
| [cs-data-factory](cs-data-factory/SKILL.md) | 根据 SOP 生成的客服多轮对话 | 确定性格式校验 + 固定版本的 LLM Judge + 低分根因分析与回归 | 校验报告、逐会话评分 JSONL、质量报告、待修复清单 |
| [auto-inspection](auto-inspection/SKILL.md) | 数据生产、评测、修复、质检任务的产物 | 3C 合规检查 + 四个通用维度 + 任务专属动态 Rubric | 三层检查结果、原文证据、综合风险等级、人工关注事项 |

## 1. cs-data-factory：怎么质检生成对话

### 定位和输入

cs-data-factory 的完整能力是“解析 SOP → 枚举路径 → 生成 Prompt → 批量生产对话 → 质检 → 定向迭代”。本节聚焦生产之后的质量检查。

质检需要生产产物 `final.jsonl`、SOP 图谱、Judge Prompt 与 Rubric。使用 v1.2 时还需要 Persona 定义和路径骨架，以核对生成内容是否忠实于预设角色与服务流程。

### 质检 SOP

1. **先做确定性校验。** `harness_verify/verify_output.py` 检查必填字段、会话 ID、时间格式、回复非空、动作值域等。上游另有图谱、路径和 Prompt 校验器。这一步回答“产物能否按契约被消费”，不判定话术语义质量。
2. **选择 Judge 执行模式并固定版本。** 可导出 Prompt 交给 SubAgent 评分，也可调用配置的模型 API。`confirm_judge_mode.py` 写入执行模式，防止同一轮混用。Prompt 和 Rubric 按版本维护，v1.2 需要显式指定，脚本默认仍为 v1.0。
3. **按会话组织待检样本。** 同一 session 的所有轮次组成一个质检单元。`run_judge.py` 默认抽取 20 个 session；`--n -1` 检查全部 session。SubAgent 模式默认每个批次文件包含 5 个 session，每个 session 独立出分。
4. **按固定 Rubric 评分。** Judge 先识别服务动作，再检查对应动作维度；v1.2 额外检查话术策略、口语自然度、Persona 忠诚度和骨架合规度。每个失分项输出分数、失分标签与理由。
5. **汇总并复核低分。** API 模式直接写入 `judge_results.jsonl`；SubAgent 模式由 `collect_subagent_results.py` 汇总。`gen_report.py` 生成维度通过率、失分标签 TOP5、低分会话、原文证据及 Persona 分组统计。
6. **定位根因并回归。** 用 `analyze_root_cause.py` 组织低分证据，判断应修改生产 Prompt、Persona、路径/SOP，还是 Judge 标准；用 `pick_low_score_tasks.py` 定位定向重跑任务。已有人工标注回归集时，用 `run_regression.py` 比较 Judge 版本。

### Judge 检查什么

| 维度组 | 检查内容 |
|---|---|
| A–G：服务动作质量 | 开场与厘清、外呼确认、直接询问、索要凭证、方案协商、结束语、共情；按命中的动作检查 |
| H：话术策略 | 是否承接情绪、交代背景、避开禁用套话、自然收尾，以及方案引导和会话内多样性 |
| I：口语自然度 | 机器腔、过度书面化、内部术语泄露、语体降级 |
| J：Persona 忠诚度 | 用户情绪强度、表达风格、行为模式与人设一致性 |
| K：骨架合规度 | 动作序列、信号响应、解决方案方向与对话轮次是否符合路径骨架 |

v1.2 的 Rubric 规定：先将子维度得分归一化，再把 A–G 动作组与 H、I、J、K 四组等权聚合，映射为 `1 + 4 × normalized_score`，得到 1–5 分。报告脚本消费 Judge 输出的分数，不重新计算全部评分。会话通过阈值默认是归一化分数 0.6，可用 `--pass-threshold` 调整；维度通过率统计当前固定使用 0.6。

**配置缺失会影响分数解释：** v1.2 当前在缺少 Persona 或路径骨架时，会把对应 J/K 维度按满分处理。执行前应补齐这些配置；报告中的高分不能证明缺失的上下文已被核验。质检分数也需要通过人工复核和固定回归集校准。

### 执行示例

以下命令在 `cs-data-factory/` 目录执行，假设生产产物和 SOP 图谱已准备好。

```bash
# 格式检查
python harness_verify/verify_output.py --file data/runs/demo/final.jsonl --sop-id demo --json

# 选择 SubAgent 模式，导出全量会话的 v1.2 Judge Prompt
python scripts/confirm_judge_mode.py --round review_demo --mode A
python judge/scripts/run_judge.py --batch data/runs/demo --round review_demo --judge-version v1.2 --n -1 --export-prompts

# Agent 按 phase3-judge.md 的 SOP 完成各批次评分，写入 result 文件后汇总
python judge/scripts/collect_subagent_results.py --round review_demo
python judge/scripts/gen_report.py --round review_demo
python judge/scripts/analyze_root_cause.py --round review_demo
```

需要 API 模式时，设置 `CS_FACTORY_API_ENDPOINT` 为完整的 Chat Completions URL，设置 `CS_FACTORY_APPID` 为 API Key，并显式指定自己的模型名称。保留 `--appid` 参数是为了兼容原有调用方式；推荐通过环境变量提供凭据。模式确认文件只保存模式和模型。

**深入阅读：** [Phase 3 执行 SOP](cs-data-factory/references/phase3-judge.md)、[v1.2 Rubric](cs-data-factory/judge/rubric/rubric_v1.2.md)、[v1.2 Judge Prompt](cs-data-factory/judge/prompts/judge_prompt_v1.2.md)、[断点与文件契约](cs-data-factory/references/checkpoint.md)、[人工标注指南](cs-data-factory/judge/annotation-guide.md)。

## 2. auto-inspection：怎么质检任务产物

### 定位和输入

auto-inspection 对已提供的产物样本执行三层检查。平台负责抽样、调度和分批，Skill 收到的样本全部检查。当前实现是 Agent 执行协议和参考标准，没有独立批处理脚本。

核心输入包括 `task_type`、`task_goal`、`skill_info` 和 `samples`，可选 `reference_knowledge`。如果从本地文件启动，还要提供任务名称、数据路径和生产 Skill 目录，供 Agent 发现任务配置。

```json
{
  "task_type": "数据生产",
  "task_goal": "检查生成的客服对话是否遵循给定服务流程",
  "skill_info": "本次生产 Skill 的说明、输出字段和执行约束",
  "reference_knowledge": "本次任务的 SOP 和参考标准",
  "samples": [
    {
      "sample_id": "demo_001",
      "output": "这里放一条待检产物的实际内容"
    }
  ]
}
```

### 质检 SOP

1. **收集任务上下文。** 读取任务目标、产物与执行 Skill；按任务类型发现 SOP、信号规则、Schema、Persona、评测标准或修复规则。列出已读取的配置与缺失项，确认检查依据。
2. **判断适用性。** 数据生产、数据修复任务启用第一层 3C；数据评测、数据质检任务将该层标记为不适用。涉及具体规则时继续判断该规则是否适用于样本。
3. **第一层：3C 合规。** 按 19 项服务标准逐样本检查，分红牌 3 项、黄牌 7 项、蓝牌 9 项。命中红/黄牌即不通过；仅蓝牌为需关注。保留违规项、样本 ID、原文证据和判定理由。
4. **第二层：通用维度。** 分别检查准确性、遵循性/忠诚度、完整性、格式合规性，独立记录通过率和问题。缺少参考知识时，事实对照与标准遵循检查按文档降级，并在结果中注明。
5. **第三层：动态 Rubric。** 根据任务目标和已读取的完整执行配置生成 3–5 个专属维度。先明确维度名称、检查要点、通过标准和权重，再逐样本检查；完整 Rubric 写入 `generated_dimensions`。前两层的结果不能替代这一层。
6. **计算综合风险。** 三层独立评级，最终取最高风险。动态 Rubric 的权重表示维度重要程度，当前不参与风险计算。
7. **输出结构化 JSON。** 包含总体风险、三层结果、问题样本数、问题率和人工关注事项。每个问题都能追溯到 `sample_id` 与原文证据。遇到模型服务异常，保留已完成结果，当前步骤最多重试两次，仍失败则标明中断点。

### 三层怎么汇总风险

| 层级 | 评级规则 |
|---|---|
| 3C | 红/黄牌 → 高；仅蓝牌 → 低；无违规或不适用 → 无 |
| 通用：准确性、遵循性 | 通过率 ≥90% → 无；80%–不足90% → 中；不足80% → 高 |
| 通用：完整性 | 100% → 无；95%–不足100% → 低；不足95% → 中 |
| 通用：格式合规性 | 100% → 无；90%–不足100% → 低；不足90% → 中 |
| 动态 Rubric | 问题率 ≥30% → 高；10%–不足30% → 中；大于0且不足10% → 低；0 → 无 |

动态 Rubric 的问题率按“至少一个专属维度不通过的样本数 / 样本总数”计算，一个样本有多项问题仍只计一次。最终风险取三层最高值，因此通用维度表现良好也不能抵消严重合规违规。

仓库内的 3C 是可调整的服务标准示例；正式使用时应对齐本次业务的审核要求。缺失依据或服务中断应在报告中保留，不能把未完成的检查理解为质量通过。

**深入阅读：** [端到端工作流](auto-inspection/references/workflow.md)、[3C 标准](auto-inspection/references/3c-standards.md)、[通用维度](auto-inspection/references/general-dimensions.md)、[动态 Rubric 生成 SOP](auto-inspection/references/dynamic-rubric-guide.md)、[风险规则与输出结构](auto-inspection/references/risk-rating-rules.md)。

## 环境与文件管理

- 将需要的 Skill 文件夹交给支持 `SKILL.md` 的 Agent 加载，或复制到该 Agent 的技能目录。
- cs-data-factory 的脚本需要 Python 3.10+；读写 Excel 需要 `openpyxl`。可选知识库拉取示例需要 `requests`，接口地址由使用者提供。
- API 模式通过环境变量配置网关与凭据，不包含默认内部端点。HTML 预览依赖公开 CDN 的 D3、dagre-d3。
- 仓库发布 Skill 文档、脚本和规则示例；真实样本、运行记录、评分结果与模型原始响应留在各自的运行目录，并由 `.gitignore` 排除。运行时仍应保存这些文件用于复核与恢复。
- 本仓库提供能力定义与本地执行流程。接入生产平台时，由平台负责并发、权限、成本预算、审计、版本绑定和质量放行。

## 给 Agent 的学习提示词

```text
请阅读这个仓库的 README 和两个 Skill 的质检参考文档，分别讲解 cs-data-factory 与 auto-inspection 的自动化质检 SOP：输入依据、检查维度、评分/风险汇总、证据留痕、人工复核与异常恢复。各用一个简单例子说明判定过程，标明配置缺失时的限制。
```
