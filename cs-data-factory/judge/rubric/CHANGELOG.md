# Rubric CHANGELOG

## v1.2 — 2026-04-28
- 新增维度 J：Persona 忠诚度（4 子项，session 级，始终评）
  - J1 情绪基调偏离 / J2 语言风格偏离 / J3 行为模式偏离 / J4 核心诉求偏离
- 新增维度 K：骨架合规度（4 子项，session 级，始终评）
  - K1 节点遗漏 / K2 节点顺序错乱 / K3 检查点未覆盖 / K4 超出骨架范围
- 聚合规则更新：score_action / score_H / score_I / score_J / score_K 五维度等权聚合（各归一化后取均值）
- J/K 维度均为防御性二元评估（无问题=满分，有问题=0分），与 H/I 维度扣分模式一致
- 优雅降级：当 Judge Prompt 未提供 persona/skeleton 上下文时，J/K 给满分（向后兼容 v1.0/v1.1）

## v1.1 — 2026-04-16
- 新增维度 H：客服话术策略质量（6 子项，session 级，始终评）
  - H1 情绪承接缺失 / H2 背景交代缺失 / H3 禁用套话 / H4 结束语自然度 / H5 方案情感引导 / H6 session 内话术多样性
- 新增维度 I：口语自然度（4 子项，session 级，始终评）
  - I1 机器腔 / I2 过度书面化 / I3 内部术语泄露 / I4 语体降级
- 聚合规则更新：A-G 动作维度 / H 话术策略维度 / I 口语自然度 三者等权聚合（各归一化后取均值）
- H/I 维度均为防御性二元评估（无问题=满分，有问题=0分），扣分标签对应根因分析

## v1.0 — 2026-04-01
- 从 `cs-judge/rubric-draft.md` 迁移至 `cs-data-factory/judge/rubric/rubric_v1.0.md`
- 7 个服务动作标签：open_clarify / info_outbound / info_direct_ask / info_evidence / solution_propose / closing / empathy
- A～G 七大维度评分标准，含正例/反例示例锚点、扣分标签
- 共情维度（G）：情景 × 情绪等级二维评分（低/中/高 × 初始负面/消费体验/投诉/不信任）
- 禁用话术清单（4 条）
- Session 聚合：等权均值 → 映射 1-5 分（`1 + avg × 4`）
