# Regression Set Schema（v1.2）

> 本文件定义 `regression_set.jsonl` 的字段规范。  
> 实际回归集在第一轮 Judge 跑完后建立，参见 `phase3-judge.md` 附录。

## 每行 JSONL 字段

```jsonl
{
  "session_id": "gen_P001_P1_001_s1_t3",
  "persona_id": "P1",
  "path_id": "P001",
  "turns": ["...（完整对话 turns）"],
  "所有信号": "（信号文本）",
  "ground_truth": {
    "normalized_score": 0.72,
    "score_action": 0.65,
    "score_H": 0.80,
    "score_I": 0.90,
    "score_J": 0.60,
    "score_K": 0.75,
    "dimension_scores": {
      "engagement_A1": {"score": 3, "max_score": 5},
      "speech_quality_H1": {"score": 1, "max_score": 1},
      "persona_J1": {"score": 1, "max_score": 1},
      "persona_J2": {"score": 0, "max_score": 1, "fail_tags": ["语言风格偏离"]},
      "skeleton_K1": {"score": 1, "max_score": 1},
      "skeleton_K2": {"score": 1, "max_score": 1}
    },
    "notes": "J2 失分：P1 为急躁型但对话中客服语气过于温和"
  }
}
```

## v1.2 新增字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `persona_id` | string | 该 session 的 Persona ID（从 session_id 解析或 judge 输出） |
| `path_id` | string | 该 session 的骨架路径 ID |
| `ground_truth.score_J` | float | Persona 忠诚度基准分（0-1） |
| `ground_truth.score_K` | float | 骨架合规度基准分（0-1） |
| `ground_truth.dimension_scores.persona_J1-J4` | object | J 维度各子项基准分 |
| `ground_truth.dimension_scores.skeleton_K1-K4` | object | K 维度各子项基准分 |

## 挑选建议（v1.2 补充）

在 v1.0/v1.1 挑选原则基础上，额外覆盖：

- 至少 2 条 score_J 低分 case（不同 Persona，验证忠诚度评分准确性）
- 至少 2 条 score_K 低分 case（不同 path，验证骨架合规评分准确性）
- 至少 1 条 J/K 同时低分的 case（验证多维度低分场景）
