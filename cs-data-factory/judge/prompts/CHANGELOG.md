# Judge Prompt CHANGELOG

## v1.2 — 2026-04-28
- 对应 Rubric v1.2
- 新增输入占位符：`{{persona_definition}}`（Persona 定义文本）、`{{path_skeleton}}`（骨架路径 JSON）
- 新增 Step 6：维度 J 评分（J1-J4，Persona 忠诚度，始终执行）
- 新增 Step 7：维度 K 评分（K1-K4，骨架合规度，始终执行）
- Step 8 聚合计算更新：score_action / score_H / score_I / score_J / score_K 五维度等权均值
- 输出 JSON 新增字段：persona_id / path_id / score_J / score_K
- 维度键名规范：J 维度用 persona_J1-J4，K 维度用 skeleton_K1-K4
- SubAgent 调度模板新增 persona/skeleton 上下文注入
- 向后兼容：run_judge.py 自动检测模板是否含 `{{persona_definition}}`，不含则不注入（v1.0/v1.1 行为不变）

## v1.1 — 2026-04-16
- 对应 Rubric v1.1
- 新增 Step 3：维度 H 评分（H1-H6，始终执行，session 级）
- 新增 Step 4：维度 I 评分（I1-I4，始终执行，session 级）
- Step 5 聚合计算更新：score_action / score_H / score_I 三维度等权均值
- 输出 JSON 新增字段：score_action / score_H / score_I
- 维度键名规范：H 维度用 speech_quality_H1-H6，I 维度用 fluency_I1-I4

## v1.0 — 2026-04-01
- 从 `cs-judge/judge-prompt.md` 迁移至 `judge/prompts/judge_prompt_v1.0.md`
- 对应 Rubric v1.0
- 3-Step 合并一次调用：识别动作标签 → 逐维度打分 → 归一化得分
- 输入占位符：`{{历史消息}}` / `{{话术回复}}` / `{{话术关联动作}}` / `{{话术共识动作}}`（对齐生产数据列名，双括号格式）
- 输出格式：JSON（action_tags / emotion_level / dimension_scores / normalized_score / summary）
