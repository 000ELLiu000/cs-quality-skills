# 质检工作流与异常处理

## 端到端执行流程

```
接收输入 JSON
  │
  ├─→ 解析 task_type、task_goal、skill_info：理解任务类型与目标
  ├─→ 解析 samples：获取待检样本列表
  │
  ▼
Phase 1: 适用性判断
  │
  ├─ task_type == "数据生产" 或 "数据修复" → 第一层启用，执行 3C 检查
  ├─ task_type == "数据评测" 或 "数据质检" → 第一层标记 不适用
  │
  ▼
Phase 2: 逐层执行检查
  │
  ├─→ Layer 1: 3C 合规检查
  │     逐样本 × 逐维度扫描
  │     输出：violations 列表
  │
  ├─→ Layer 2: 通用质检维度
  │     四个维度分别评估：准确性、遵循性/忠诚度、完整性、格式合规性
  │     输出：每维度 status + issues
  │
  ├─→ Layer 3: 动态 Rubric
  │     Step A: 根据 task_type、task_goal、skill_info 生成 3-5 个维度（不看样本）
  │     Step B: 用生成的维度逐样本检查
  │     输出：generated_dimensions + results
  │
  ▼
Phase 3: 综合评级
  │
  ├─→ 按风险评级规则，综合三层结果判定 risk_level
  ├─→ 根据 risk_level 给出 recommendation
  ├─→ 提炼 human_attention_items
  │
  ▼
Phase 4: 组装输出
  │
  └─→ 严格按 risk-rating-rules.md 中的 JSON Schema 输出报告
```

## 各层执行顺序与依赖关系

- 三层检查之间**相互独立**，不存在「上一层不通过则跳过下一层」的逻辑
- 三层**全部执行完**之后，才进入综合评级阶段
- 动态 Rubric 的**生成**不依赖样本内容，只依赖 task_type、task_goal、skill_info
- 动态 Rubric 的**执行**依赖样本内容

---

## 异常场景处理

### 样本列表为空

```json
{
  "inspection_result": {
    "risk_level": "无",
    "risk_summary": "无样本可检查",
    "recommendation": "放行",
    "recommendation_reason": "samples 列表为空，无法执行质检"
  },
  "sampling_info": { "total_samples": 0, "problematic_samples": 0, "problem_rate": "0%" },
  "layer_1_3c": { "applicable": false, "status": "不适用", "highest_severity": "无", "violations": [] },
  "layer_2_general": {
    "accuracy": { "status": "通过", "pass_rate": "100%", "pass_count": 0, "total": 0, "issues": [] },
    "fidelity": { "status": "通过", "pass_rate": "100%", "pass_count": 0, "total": 0, "issues": [] },
    "completeness": { "status": "通过", "pass_rate": "100%", "pass_count": 0, "total": 0, "issues": [] },
    "format_conformity": { "status": "通过", "pass_rate": "100%", "pass_count": 0, "total": 0, "issues": [] }
  },
  "layer_3_dynamic_rubric": { "generated_dimensions": [], "results": [] },
  "human_attention_items": []
}
```

### 样本产物内容为空 / null

- 该样本在第二层「完整性」维度标记为「不通过」
- 在第三层中同样标记为问题样本
- 不影响其他样本的检查

### reference_knowledge 缺失

- 第二层「准确性」降级为逻辑自洽性检查（无法做事实核验）
- 在 accuracy 的第一个 issue 中注明「无参考知识库，仅检查逻辑一致性」
