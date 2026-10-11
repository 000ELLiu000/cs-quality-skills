# Phase 0 SOP 解析执行规范

> 本文档供主 Agent 调度 Phase 0（SOP 解析 + Persona 分析 + 信号分析）时参考。  
> Phase 0 最多启动三个 SubAgent：SubAgent-A 和 SubAgent-B **并行**执行；SubAgent-C **在 SubAgent-A 完成后**才可启动（依赖 graph.json）。

---

## 📥 输入数据获取方式（启动 Phase 0 前提示用户）

**主 Agent 在启动 Phase 0 之前，需告知用户以下两种输入数据来源方式，由用户选择：**

- **方式一（手动提供）**：将 SOP.md 和真实信号样本 Excel 直接放入对应目录，或在对话中发送给 Agent，Agent 写入本地文件后继续。
- **方式二（AIDA 接口拉取）**：若团队已在知识库 平台建好知识库和工作流，可提供 API Key + inputs 参数，由 Agent 远程拉取内容后写入本地文件再继续。详见本文档末尾「附：通过 AIDA 工作流接口拉取输入数据」。

用户选定方式、输入数据就位后，再启动以下 SubAgent 流程。

---

## 主 Agent 调度方式（三阶段 SubAgent）

主 Agent 按以下顺序调度 SubAgent：

- **SubAgent-A（SOP 解析，必选）**：读 SOP.md → 生成 `data/sop/{sop_id}_graph.json`
- **SubAgent-B（Persona 分析，与 A 并行）**：读真实样本 Excel 对话历史 → 生成 `data/sop/{sop_id}_persona.md`
- **SubAgent-C（信号分析，必选，A 完成后才启动）**：运行 `analyze_signals.py` → 草稿 → LLM 评审 → 人工确认 → 入库 `data/sop/{sop_id}_signal_rules.json`

全部 SubAgent 完成后，主 Agent 再执行 Step 5（路径枚举）。

> ✅ **并行边界：SubAgent-A 和 SubAgent-B 各读各的文件，互不需要对方的结果，可安全并行。SubAgent-C 依赖 SubAgent-A 生成的 graph.json，不可与 A 并行。**

---

### SubAgent-A 调度 Prompt 模板（SOP 解析）

**主 Agent 照此向 SubAgent-A 下达任务：**

```
你的任务是将以下 SOP 文档解析为结构化 JSON Graph，然后将结果写入文件。

## SOP 文档全文
（此处粘贴 references/sop/{sop_id}.md 的全文）

## 输出格式参考
（此处粘贴 references/sop-example.md 的完整内容）

## 执行规范
详见 references/phase0-sop-parsing.md Step 1~4，按步骤逐步执行。

## 输出要求
1. 严格按照参考格式生成 JSON，不要输出任何其他内容
2. 生成完成后立即将结果写入 data/sop/{sop_id}_graph.json
3. 写入后运行 `python harness_verify/verify_graph.py --sop-id {sop_id}`
4. 若校验通过，输出「✅ SubAgent-A 完成，graph 已写入」；若失败，列出错误后自行修正并重新写入，最多重试 2 次
```

---

### SubAgent-B 调度 Prompt 模板（Persona 分析，可选）

> **Persona 策略（主 Agent 在启动 SubAgent-A 前必须向用户确认）：**
>
> Agent 向用户展示以下提示，**等待用户明确回复**后再决定是否启动 SubAgent-B：
>
> ```
> Persona 分析需要读取真实对话样本（assets/real_signals/real_signals_sample_{sop_id}.xlsx），
> 会消耗额外 Token 且分析结果未必优于预设模板。
>
> 请选择 Persona 来源：
>   A. 使用全局预设模板（references/persona.md）——默认，0 Token 消耗，推荐
>   B. 基于真实样本分析生成定制 Persona（需要 Excel 样本已就位）
>
> 请回复 A / B，或直接回车 / 回复"默认"使用选项 A。
> ```
>
> - 用户选 **A** 或默认 → **跳过 SubAgent-B**，直接使用 `references/persona.md`；Phase 1 中 `generate_prompts.py` 自动加载该文件
> - 用户选 **B** → 检查 `assets/real_signals/real_signals_sample_{sop_id}.xlsx` 是否存在：
>   - 文件存在 → 启动 SubAgent-B（按下方模板）
>   - 文件不存在 → 告知用户文件缺失，等待用户手动放入后再重新确认
>
> ⚠️ **不得静默跳过此询问直接启动 SubAgent-B，即使目录下已有 Excel 文件。**

**主 Agent 首先运行脚本生成 Persona 分析 Prompt （不调用 LLM）：**

```bash
python scripts/analyze_persona.py --sop-id <sop_id>
# 输出：将分析 Prompt 缓存到 data/tmp/{sop_id}_persona_analysis.prompt.txt
```

**然后主 Agent 照此向 SubAgent-B 下达任务：**

```
你的任务是分析真实对话样本，生成场景定制化 Persona 文件。

读取分析 Prompt：data/tmp/<sop_id>_persona_analysis.prompt.txt
执行分析，生成 Persona 内容，写入：data/sop/<sop_id>_persona.md

写入完成后输出「✅ SubAgent-B 完成，Persona 已写入 data/sop/<sop_id>_persona.md」
```

**主 Agent 在全部 SubAgent 返回后的检查点：**
- SubAgent-A 输出含「✅ SubAgent-A 完成」；若含错误，主 Agent 接手手动修正
- SubAgent-B（若启动）输出含「✅ SubAgent-B 完成」；若含错误，主 Agent 接手手动修正
- SubAgent-A 和 SubAgent-B 均返回后，**可立即启动 SubAgent-C（若有 Excel 样本）**，同时执行 Step 5（路径枚举）；SubAgent-C 与路径枚举也可并行，两者互不依赖
- SubAgent-C 完成入库确认后，输出「✅ SubAgent-C 完成，信号规则已入库 data/sop/{sop_id}_signal_rules.json」

---

### SubAgent-C 调度 Prompt 模板（信号分析，必选）

> **触发条件**：SubAgent-A 已完成（`data/sop/{sop_id}_graph.json` 已生成），SubAgent-C 必须在 A 完成后立即启动。真实样本 Excel 为必选输入。

**Step C-1. 主 Agent 运行脚本（不调用 LLM）：**

```bash
python scripts/analyze_signals.py --sop-id <sop_id>
# 输出：
#   data/tmp/{sop_id}_signal_rules_draft.json   （信号规则草稿）
#   data/tmp/{sop_id}_signal_review_prompt.txt  （LLM 评审 Prompt）
```

**Step C-2. 主 Agent 读取 `data/tmp/{sop_id}_signal_review_prompt.txt`，照此向 SubAgent-C 下达任务：**

```
你的任务是为客服数据工厂的信号字段补充 mock 规则。

以下是需要评审的信号字段信息：
（此处粘贴 data/tmp/{sop_id}_signal_review_prompt.txt 的全文）

请为每个 type=mock 的信号字段写出简洁的 mock_rule（自然语言描述，例：随机生成11位手机号）。

只输出纯 JSON，格式：
{
  "信号字段名": "mock 规则描述",
  ...
}
不要任何额外解释。
```

**Step C-3. 主 Agent 将 SubAgent-C 返回的 JSON 合并到草稿，并向用户展示本次生产信号清单：**

合并步骤（使用内置脚本，直接更新 `signals` 权威表；`_production_signals` 在读取/预览时自动派生，无需手动维护）：

**Step C-3-1. 将 SubAgent-C 返回的 JSON 写入临时文件（跨平台统一方式，无引号解析风险）：**

使用 write 工具将 SubAgent-C 返回的 JSON 内容写入：`data/tmp/<sop_id>_llm_mock_rules.json`

**Step C-3-2. 再调用脚本合并（文件路径形式，适用全平台）：**

```bash
python scripts/analyze_signals.py --sop-id <sop_id> \
    --merge-mock-rules data/tmp/<sop_id>_llm_mock_rules.json
```

> **⚠️ 跨平台强制规范**：
> - **禁止**使用内联 JSON 字符串形式（`--merge-mock-rules '{"key": "value"}'`），在 Windows PowerShell 中，含中文、引号或特殊字符的 JSON 字符串会被 Shell 词法分析破坏，导致 argparse 解析失败。
> - **必须**使用文件路径形式：先由 Agent 用 write 工具将 JSON 写入临时文件，再传入文件路径。此方式在 Windows/macOS/Linux 三平台均可靠。
> - 脚本会打印每个字段的写入状态（如 `✓ 已写入权威表，运行时摘要可派生` / `⚠ 字段不存在`），便于排查遗漏。

展示步骤（筛选依据为草稿中的 `signals` 结合 graph.json 的 signal_fields + slot_fields 实时派生）：

```
========== 本次生产信号清单确认 ==========

【一、判断信号（骨架锁定，不需要 mock）】
来源：SOP 决策树分支节点（graph.json signal_fields）
作用：决定路径走向，值由 enumerate_paths.py 锁定在骨架中，生产时直接取用

  - <信号名>：枚举值 → [<值1>, <值2>, ...]
  - <信号名>：枚举值 → [<值1>, ...]
  （若 enum_values 为空，说明 Excel 中未出现该信号，仍会在骨架中锁定值）

【二、参考信号（需要 mock，注入话术）】
来源：SOP 参考信号表（graph.json slot_fields）
作用：话术槽变量，LLM 在生成对话前按 mock 规则生成值，输出到 mocked_signals 字段

  - <信号名>（static/dynamic）：<mock_rule>
  - <信号名>（static/dynamic）：枚举值 → [<值1>, <值2>, ...]
  （mock_rule 为空说明 LLM 未给出规则，将按常识生成）

【三、补集信号（Excel 有但 SOP 未用，本次生产不涉及）】
  共 <N> 个（详见草稿文件中 source=other 的信号），不注入 prompt，不进入训练数据

==========================================
以上是本次生产将实际使用的信号及其逻辑。
如需调整 mock_rule 或 enum_values，请直接告知，Agent 修改草稿后重新展示。
信号规则将在 HTML 审查报告（Step 7-1）中通过「信号规则」标签页一并呈现，请在 HTML 审查环节统一确认，无需单独回复入库指令。
```

**Step C-4. HTML 审查用户确认后，主 Agent 执行入库：**

> 入库时机：Step 7-1 HTML 审查报告中，用户回复「确认」后，Agent 在执行 Phase 1 前先执行本步入库操作，再继续流程。

```bash
python -c "import shutil; shutil.copy('data/tmp/{sop_id}_signal_rules_draft.json', 'data/sop/{sop_id}_signal_rules.json')"
```

> 使用 Python shutil 保证跨平台（Windows/macOS/Linux）一致行为，无需区分 `copy` 与 `cp` 语法。

输出「✅ SubAgent-C 完成，信号规则已入库 data/sop/{sop_id}_signal_rules.json」。

> **用户可维护性**：入库后，用户可直接编辑 `data/sop/{sop_id}_signal_rules.json` 手动调整 `enum_values`、`mock_rule`、`variability` 等字段，Phase 1 每次运行都会重新读取最新版本。

---

## Step 1. 读取 SOP.md 全文，按以下规则解析

| 元素 | 识别方式 | 解析为 |
|---|---|---|
| 判断信号 | 分支节点处的 `判断信号：xxx` | `signal_fields`（决定路径走向） |
| 参考信号 | SOP 末尾 `## 参考信号表` 章节的字段名列表，**或**话术内部的 `[参考信号：xxx]` 标注 | `slot_fields`（话术/动作填槽变量，Phase 1 从真实样本取值注入 prompt） |
| 话术示例 | `[示例|客服：xxx]` | 节点 `speech_examples` |
| 话术关联动作 | `「话术关联动作：xxx」` | 节点 `action_tag`，未写则「无」 |
| 话术共识动作 | `「话术共识动作：xxx」` | 节点 `consensus_tag`，未写则「无」 |
| 叶子节点 | 无后继的节点 | 路径终止点 |

---

## Step 2. 生成草稿 `data/sop/{sop_id}_graph.json`，结构如下

```json
{
  "sop_id": "invoice",
  "scene": "到餐客服·发票场景",
  "signal_fields": ["订单退款状态", "订单状态", "品牌类型", "外呼结果"],
  "slot_fields": ["订单对应的品牌名称", "开票联系商户电话"],
  "graph": {
    "START": {"type": "signal_check", "signal": "订单退款状态", "branches": {...}},
    "N001": {"type": "agent_action", "action": "A01", "next": "N002"},
    "N002": {"type": "user_reaction", "branches": {"认可": "TERM_001", "不认可": "N003"}},
    "TERM_001": {"type": "terminal"}
  },
  "actions": {
    "A01": {
      "desc": "告知全额退款无法开票",
      "action_tag": "无",
      "consensus_tag": "无",
      "speech_examples": ["查看到您当前反馈的订单已经退款，是无法开具发票的..."],
      "required_slots": []
    }
  }
}
```

> **信号填槽约束**：`slot_fields` 中若有字段同时出现在 `signal_fields`，生产时该槽位值必须取路径上已确定的判断信号值，不可 mock。

> **字段名一致性**：`slot_fields` 中的字段名须与 Excel「所有信号」文本中的 key **完全一致**（大小写、空格均敏感）。若 SOP 里的字段名和 Excel 里的 key 不同，Phase 1 取值时会静默跳过（不报错），请确保两边命名对齐。

> **参考信号表写法**（放在 SOP 文件末尾）：
> ```markdown
> ## 参考信号表
> | 字段名 | 用途说明 |
> |---|---|
> | 商品名称 | 用户购买的商品，可出现在用户或客服话术中 |
> | 订单金额 | 退款/赔付金额，客服操作时引用 |
> | 可操作赔付金额范围 | 赔付区间（如 3-5元），执行赔付动作时引用具体值 |
> ```
> Agent 解析时，提取表格第一列所有字段名，写入 `slot_fields` 列表。**用途说明列仅供人类阅读，不写入 graph.json。**

---

## Step 3. Agent 自查（全部通过才持久化）

- ✅ 所有非叶子节点都有明确后继
- ✅ 动作 ID（A01、A02...）唯一无冲突
- ✅ 信号字段的枚举值已在节点中定义
- ✅ 有写 `「话术关联动作」` 的节点，解析结果中该字段必须非空
- ✅ 外呼分支数量与 SOP 定义一致
- 自查不通过 → 列出问题 → 修正 → 重检

---

## Step 4. 持久化 `data/sop/{sop_id}_graph.json`

将通过自查的草稿写入 `data/sop/{sop_id}_graph.json`。

> 此文件是 pipeline 产物（非用户原始输入），存放于 `data/sop/` 目录。

完成后输出「✅ Phase 0 完成，graph 已写入」，若校验失败则列出错误后自行修正并重新写入，最多重试 2 次。

---

## Step 5. 路径枚举

```bash
python scripts/enumerate_paths.py --sop-id <sop_id>
```

输出 `data/paths/{sop_id}_paths.jsonl`，向用户展示路径数量和摘要（含各路径的信号值组合概览）。

**Agent 必须在此处等待用户确认覆盖范围无误，收到用户明确反馈后才可继续执行 Step 6。** 不得静默跳过此确认直接继续。

告知用户示例：「路径枚举完成，共生成 N 条路径（详见 data/paths/{sop_id}_paths.jsonl）。请确认路径覆盖范围是否符合预期，如有缺失或错误的路径请直接告知，Agent 将修正 graph.json 并重新枚举。确认无误后请回复"继续"。」

> 此文件是 pipeline 产物，存放于 `data/paths/` 目录。

---

## Step 6. 节点校验（建议）

```bash
python harness_verify/verify_graph.py --sop-id <sop_id>
python harness_verify/verify_paths.py --sop-id <sop_id>
```

格式层校验：节点引用完整性、action_id 唯一性、paths 字段完整性，有问题立即暴露。

---

## Step 7. 用户审查报告 & 修正循环

> 用户打开 HTML 审查报告后，可能提出修正请求。Agent 按本节规范识别意图、修改产物、重新校验，直到用户确认无误为止。**确认无误后才可进入 Phase 1。**

### 7-1. 生成 HTML 审查报告（可与 Step 5/6 同步执行）

```bash
python scripts/preview_phase0.py --sop-id <sop_id>
# 输出：data/tmp/{sop_id}_phase0_preview.html
```

**报告发送方式**：按 `references/file-delivery.md` 第 2-A 节（HTML 文件）执行（Catclaw 发大象附件；CatPaw/CatDesk 提供绝对路径；环境不明时同时输出两种）。

告知用户：「Phase 0 审查报告已生成，请用浏览器打开 data/tmp/{sop_id}_phase0_preview.html，逐项检查以下三个标签页：

① **SOP 流程图**：核对节点、分支、动作是否与原始 SOP.md 展示一致；

② **信号规则**：确认判断信号的枚举值是否完整、参考信号的 mock 规则是否符合预期（即 SubAgent-C 生成的信号规则，确认后即视为入库同意）；

③ **Persona**：确认人设列表和示例话术是否匹配当前场景。

确认全部无误后请回复“确认”，或直接告知发现的问题。**Agent 在收到您的明确确认之前不会进入 Phase 1。**」

---

### 7-2. Agent 意图识别规则

用户反馈后，Agent 按以下规则定位需要修改的产物文件：

| 用户提到的关键词 / 内容 | 修改目标文件 |
|---|---|
| 节点、分支、branches、next、动作、action、signal_check、agent_action、user_reaction、terminal、START、signal_fields、slot_fields | `data/sop/{sop_id}_graph.json` |
| mock_rule、枚举值、enum_values、variability、参考信号规则、信号类型 | `data/sop/{sop_id}_signal_rules.json`（已入库）或 `data/tmp/{sop_id}_signal_rules_draft.json`（草稿阶段） |
| Persona、Persona 类型名称（如急迫催促型/情绪化抱怨型）、耐心、合作度、语言特征、典型行为、对话示例 | `data/sop/{sop_id}_persona.md`（场景定制）或 `references/persona.md`（默认） |

> 若用户描述跨越多个文件（例如"信号名改了，节点里的 signal 字段也要同步"），Agent 需**同时修改所有相关文件**，不可只改其中一个。

---

### 7-3. 修正流程（每次修正后必须完整执行）

每收到一条修正反馈，Agent 按以下步骤执行：

**Step A. 理解并修改文件**
- 理解用户描述的修改意图
- 直接修改对应 JSON / md 文件（不生成中间文件，不让用户手动操作）

**Step B. 重新校验**
- 若修改了 `graph.json`：
  ```bash
  python harness_verify/verify_graph.py --sop-id <sop_id>
  ```
- 若修改了 `signal_rules.json` 或 `_draft.json`：无需重新跑 verify_graph，跳过此步

**Step C. 重新生成 HTML 报告**
```bash
python scripts/preview_phase0.py --sop-id <sop_id>
```

**Step D. 告知用户**
- 输出修改摘要（改了什么文件、改了什么字段/节点）
- 按 `references/file-delivery.md` 第 2-A 节重新推送 HTML（Catclaw 重新发送附件；CatPaw/CatDesk 告知用户刷新浏览器，路径不变）
- 告知「HTML 报告已刷新，请重新打开/刷新浏览器确认修复已生效」
- 展示本次 verify 结果（✅ 校验通过 / ❌ N 个错误 / ⚠️ N 个警告）

---

### 7-4. 特殊情况处理

**情况 A：graph.json 修改后**

必须重新枚举路径，否则 `data/paths/{sop_id}_paths.jsonl` 将与 graph 不一致，Phase 1 可能报错：

```bash
python scripts/enumerate_paths.py --sop-id <sop_id>
```

执行后告知用户路径数量是否变化。

**情况 B：signal_rules 仍在草稿阶段（未入库）**

- 修改目标为 `data/tmp/{sop_id}_signal_rules_draft.json`
- 修改后在 Step D 中额外提示：「信号规则草稿已更新，修改将在您于 HTML 审查报告中回复"确认"后由 Agent 自动入库，无需手动操作。」

**情况 C：verify 修正后仍有错误**

- Agent 自行尝试修正最多 2 次
- 2 次仍未解决则列出剩余错误，等待用户进一步指示

**情况 D：信号在 signal_fields 与 slot_fields 之间移动（高风险操作）**

此类操作必须同时修改 `graph.json` 和 `signal_rules.json`，且修改后必须重新枚举路径并告知用户路径数量变化。

| 操作方向 | graph.json 改动 | signal_rules.json 改动 | 路径数量影响 |
|---|---|---|---|
| 判断信号 → 参考信号 | 从 `signal_fields` 移除，加入 `slot_fields`；对应 `signal_check` 节点须重构（分支合并）| `source` 改为 `slot` | **必然减少**（分支合并）；减少数量须向用户明确确认 |
| 参考信号 → 判断信号 | 从 `slot_fields` 移除，加入 `signal_fields`；如需驱动分支须补充 `signal_check` 节点 | `source` 改为 `judgment` | 通常增加（分支拆分） |

> ⚠️ **高风险提示**：`signal_check` 节点的每条 `branches` 对应一条独立路径；从 `signal_fields` 移除后，所有通过该节点的路径将合并，可能导致部分场景分支丢失。Agent 必须在执行前向用户说明此影响，确认后再操作。

**特殊模式：信号既驱动路径分支，又需要在外呼触发轮次展开为完整对话文本**

典型案例：外呼结果类信号（如"外呼记录"）——它的枚举值决定外呼后走哪条分支（需留在 `signal_fields`），同时在话术里需要呈现完整的客服-商家通话记录（不能只写摘要）。

正确做法：

1. 信号**保留在 `signal_fields`**，保证路径枚举器能遍历所有外呼结果分支
2. 在 `signal_rules.json` 中设置 `"outbound_expand": true`（`analyze_signals.py` 对 `source=judgment` 且 `variability=dynamic` 的信号会自动推断并设置此属性）
3. `outbound_expand=true` 的效果：prompt 里生成"外呼记录展开规则"块，LLM 在触发外呼的那一轮 `outbound_update` 字段里生成完整多轮对话（每行以「客服:」或「商家:」开头），而非一句摘要
4. 最终数据里，`外呼信号` 字段：只有外呼触发那轮填对话文本，其他轮均为"无外呼记录"

错误做法：把信号从 `signal_fields` 移入 `slot_fields` → 所有外呼结果分支合并为一条路径，路径覆盖度大幅下降。

---

### 7-5. 审查循环退出条件

用户明确回复以下任意一项，退出审查循环，进入 Phase 1：

- 「确认」/「没问题」/「可以进入下一步」
- HTML 报告顶部显示「✅ 校验通过」且用户无进一步反馈

> 循环次数无上限。在用户明确确认之前，不得启动 Phase 1 生产。

---

### 7-6. 用户自定义 Persona & 路径

> 用户在任意阶段（Phase 0 审查期间或 Phase 1 之前）说"自定义 Persona"/"新增路径"/"捏一个..."等，Agent 立即执行本节流程，完成后继续之前的审查或生产流程。

---

#### 7-6-A. 自定义 Persona

**触发词**：「自定义 Persona」/「新增 Persona」/「捏一个 Persona」/「我要加一类用户」/「加一个用户类型」

**Step 1. Agent 向用户输出模板：**

```
请按照以下模板填写新 Persona，填好后直接发给我：

Persona ID：PXX（建议用 P10/P11/... 以避免与现有 P1-P9 冲突）
名称：（如"礼貌感谢型"）
耐心：（极高 / 高 / 中 / 低 / 极低）
合作度：（高 / 中 / 低）
语言特征：（一句话描述，如"短句、多谢字、语气温和"）
典型行为：（一句话描述）
示例话术（至少 2 条，用「」括起来）：
  - 「xxx」
  - 「xxx」
对话示例（可选，一段多轮对话，用"用户:"/"客服:"开头）：
```

**Step 2. 用户填好后，Agent 将内容转换为标准格式并追加写入目标文件：**

目标文件优先级：
1. 若 `data/sop/{sop_id}_persona.md` 存在（SubAgent-B 生成的场景定制 Persona）→ 追加到该文件
2. 否则追加到 `references/persona.md`

追加格式（严格与现有格式一致）：

```markdown
### PXX：<名称>

- **耐心**：<值>
- **合作度**：<值>
- **语言特征**：<值>
- **典型行为**：<值>
- **示例**：
  - 「xxx」
  - 「xxx」

**对话示例（可选）：**
\`\`\`
用户: xxx
客服: xxx
\`\`\`
```

**Step 3. 追加完成后：**
- 告知用户新 Persona 已追加，Phase 1 中可用 `--personas PXX` 单独测试
- 重新生成 HTML 报告（`python scripts/preview_phase0.py --sop-id <sop_id>`），让用户在 Persona 标签页确认新增内容格式正确

---

#### 7-6-B. 自定义路径

**触发词**：「自定义路径」/「新增路径」/「捏一条路径」/「加一个特殊 case」/「边缘路径」/「我要加一条路径」

**Step 1. Agent 先展示当前 `graph.json` 中可用的动作 ID 清单供参考，再向用户输出模板：**

```
可用动作 ID（来自 graph.json actions 表）：
  A01：<desc>
  A02：<desc>
  ...

请按照以下模板填写自定义路径，填好后直接发给我：

路径描述：（简要说明这条路径代表什么 case，如"用户坚持要开发票，客服多次安抚无效最终挂断"）
信号值：（列出该路径的判断信号取值）
  <信号名>: <取值>
  （可参考现有枚举值，也可以写新的取值）
动作序列：（经过的动作 ID，用 → 连接，需在上方动作表中存在）
  A01 → A05 → A07
```

**Step 2. 用户填好后，Agent 构造标准骨架 JSON 并追加到 `data/paths/{sop_id}_paths.jsonl`：**

- `path_id`：格式为 `CUST_XXX`，序号自动递增（Agent 先读文件找最大现有 CUST 序号）
- `actions` 中每个 `action_id` 从 `graph.json` actions 表中查找 `desc`/`关联动作`/`共识动作`/`outbound` 字段自动填入
- `_custom: true` 和 `_custom_desc` 字段标记为用户自定义路径，`enumerate_paths.py` 重新运行时不会覆盖 CUST_ 条目

追加格式（与自动枚举路径完全一致，额外带 `_custom` 标记）：

```json
{
  "path_id": "CUST_001",
  "sop_id": "<sop_id>",
  "scene": "<scene>",
  "signals": { "<信号名>": "<取值>", ... },
  "actions": [
    { "action_id": "A01", "desc": "...", "关联动作": "...", "共识动作": "...", "outbound": false },
    ...
  ],
  "turns": <动作数>,
  "has_outbound": <是否含外呼>,
  "_custom": true,
  "_custom_desc": "<路径描述>"
}
```

**Step 3. 追加完成后：**

校验约束（Agent 写入前检查，有问题先告知用户修正）：
- `actions` 中每个 `action_id` 必须在 `graph.json` actions 表中存在（硬性约束，不通过不写入）
- `signals` 中信号名不在 `signal_fields`/`slot_fields` 时报 warning（不阻断写入）

写入后：
- 运行 `python harness_verify/verify_paths.py --sop-id <sop_id>` 校验格式
- 告知用户路径已加入，Phase 1 中可用 `--paths CUST_001` 单独生产

**Step 4. enumerate_paths.py 重新运行时的保护：**

`enumerate_paths.py` 当前会覆盖整个 `paths.jsonl`。若用户在写入自定义路径后又修改了 graph 并重新枚举，Agent 需在重新枚举后将已有 CUST_ 路径重新追加回去（读取原 paths.jsonl 中 `_custom: true` 的行，追加到新生成的文件末尾）。

---

## 附：通过 AIDA 工作流接口拉取输入数据（可选）

> Phase 0 所需的 SOP 文档、真实信号样本等输入数据，除了用户手动提供外，还可以通过**自建知识库 工作流接口**远程拉取。适合已在 AIDA 平台建好知识库的团队。

**前置条件（由用户自行完成）：**
1. 在 AIDA 平台创建知识库，将 SOP 文档、信号样本等上传至知识库
2. 创建 AIDA 工作流，配置检索逻辑（决定如何查询和返回内容）
3. 发布工作流，获取 `API Key`（用于 Authorization Bearer）
4. 确认工作流的 `inputs` 变量名（即检索参数的 key，如 `scene`、`sop_name` 等）

**用户准备好后，告知 Agent 以下信息：**
- API Key
- inputs 参数（键值对，与工作流变量对应）
- 本次拉取的目标（SOP / 信号样本 / Persona 等）

**Agent 调用方式（POST `/chat-messages`，blocking 模式）：**

```python
import os
import requests

resp = requests.post(
    os.environ["CS_FACTORY_KNOWLEDGE_ENDPOINT"],
    headers={
        "Authorization": "Bearer <API_KEY>",
        "Content-Type": "application/json"
    },
    json={
        "inputs": { "<变量名>": "<取值>", ... },   # 用户提供的检索参数
        "query": "请返回完整内容",
        "response_mode": "blocking",
        "user": "cs-data-factory"
    }
)
content = resp.json().get("answer", "")
# 将 content 写入对应本地文件（如 references/sop/{sop_id}.md）后，按正常 Phase 0 流程继续
```

**Agent 拿到内容后的处理：**
- SOP 文档 → 写入 `references/sop/{sop_id}.md`，按正常 Phase 0-A 流程解析
- 真实信号样本 → 写入 `assets/real_signals/real_signals_sample_{sop_id}.xlsx`（若为结构化数据）或直接作为分析输入
- Persona 定义 → 写入 `data/sop/{sop_id}_persona.md` 或 `references/persona.md`

> **API 文档参考**：`references/aida-api-doc.md`
