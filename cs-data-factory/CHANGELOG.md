# v10.13.2 — 2026-10-11

代码层：模型端点改为环境变量；模式确认不再保存或打印凭据；修复 Judge 环境变量认证的模式识别。

文档层：版本信息迁入兼容 Skill 格式的 metadata.version；清理固定凭据、内部地址、本机路径和示例手机号；使用通用接口与业务名称。

# CHANGELOG

## v10.13.1 (2026-05-06)

### fix: Phase 0-2 主链路收敛 + signal_rules 单一权威源 + 回归验收补齐

**核心变更：**

围绕 Phase 0-2 主链路完成一轮标准化收敛，重点解决 `signal_rules` 双源真相、Phase 1 契约校验不够严格，以及 graph 命名与 legacy 残留治理问题；并完成 Batch 3 的 Phase 0/1/2 全链路回归验收。

**代码与脚本收敛：**

- `lib/__init__.py`：
  - 新增 `canonicalize_signal_rules_metadata()`，统一 `signal_rules` 持久化元数据（`_format_version=2.0` + 统一 `_description`）
  - `load_signal_rules_file()` / `repair_signal_rules_file()` 统一走 metadata 归一化与运行时摘要剥离
  - 保持 `signals` 为唯一权威源，`_production_signals` 仅作为运行时派生摘要使用

- `scripts/analyze_signals.py`：
  - 改为复用 `lib` 中统一的 `SIGNAL_RULES_DESCRIPTION`，消除 `_description` 硬编码漂移
  - 持久化输出继续遵循单一权威源格式，避免旧版说明与结构再次写回

- `harness_verify/verify_prompts.py`：
  - 强化 Phase 1 契约校验，显式要求 `batch` / `output_count` / `order_signals`
  - 新增 `task_id` 与 `path_id/persona_id/batch` 一致性校验，提前拦截任务元数据漂移

**规范与命名统一：**

- 统一 SOP graph 文件命名为 `{sop_id}_graph.json`
- 清理 legacy `_verify_v2_signals.py` 残留
- `references/phase1-prompt-generation.md`：同步更新 Phase 1 校验要求，明确强校验关键任务字段

**数据与验收：**

- 批量修复 `data/sop/*_signal_rules.json` 中残留的持久化 `_production_signals` 字段
- 重新执行 `preview_phase0.py`、`verify_graph.py`、`verify_paths.py`、`verify_prompts.py`、`verify_output.py`
- Batch 3 全链路回归通过：Phase 0 / Phase 1 / Phase 2 均为 `error=0, warning=0`

- `SKILL.md`：版本号 `v10.13.0` → `v10.13.1`

---

## v10.13.0 (2026-04-29)

### feat: v2.0 信号模板替换架构 — 「所有信号」输出格式与真实样本完全一致

**核心变更：**

Phase 2 后处理（postprocess.py）的「所有信号」字段从简化的 `### 路径信号 + ### 参考信号` 格式，升级为与真实样本完全一致的模板替换格式（含 `### 用户信息`、`### 支付信息` 等原始 section headers 和全部信号 key）。

**新增函数（lib/__init__.py）：**

- `parse_signal_template()`：从真实样本第一行提取信号模板，值替换为 `__PLACEHOLDER__` 占位符；自适应设计，不硬编码任何 section 名称
- `extract_signal_keys_ordered()`：提取信号 key 列表，保持原始顺序
- `parse_signal_text_full()`：解析信号文本为 dict，保留完整值（含逗号后附加信息），区别于 `parse_signal_text()` 的截断行为
- `build_other_signal_pool()`：从真实样本按 session 聚合静态补集信号值，构建 round-robin 值池
- `fill_signal_template()`：将占位符模板 + 生成信号 + 补集信号值合并为最终文本

**修改文件：**

- `scripts/analyze_signals.py`：
  - Phase 0 信号分析新增动态补集信号提升逻辑（other+dynamic → slot，设置 `_promoted_from_other` 标记和 `mock_rule`）
  - `build_draft()` 新增 `signal_template` 和 `other_signal_pool` 字段输出到 signal_rules.json
  - `_format_version` 升级为 `"2.0"`

- `scripts/generate_prompts.py`：
  - `build_signal_mock_rules()` 扩展 `slot_fields` 集合，覆盖 signal_rules 中 `source=slot` 的信号（含提升后的动态补集信号）

- `scripts/postprocess.py`：
  - 新增 `load_signal_rules()` 函数：从 prompts_todo.jsonl 提取 sop_id，加载对应 signal_rules.json
  - 新增 `_merge_generated_signals()` 函数：合并骨架判断信号 + LLM mocked 信号为扁平 dict（骨架优先）
  - 重写 `build_all_signals_text()`：v2.0 模式调用 `fill_signal_template()` 模板替换；v1.0 降级模式保留简化格式（向后兼容）
  - `split_trajectory()` 新增 `signal_template` 和 `other_values` 参数
  - `main()` 新增 signal_rules 加载、round-robin 池计数器、补集信号值分配逻辑

- `references/output-schema.md`：
  - 「所有信号」字段格式说明更新为 v2.0 模板替换架构
  - 新增信号组装流程四步表（提取模板 → 合并生成信号 → 分配补集信号 → 模板填充）
  - 示例 JSON 更新为真实样本格式

**修复：**

- `lib/__init__.py`：`_build_pool_from_excel()` 中 openpyxl 导入失败时新增 warning 提示（原为静默返回空列表）

**设计要点：**

- 自适应：模板完全由真实样本决定，不同 SOP 场景的信号格式自动适配
- 向后兼容：无 signal_rules.json 时自动降级为 v1.0 简化格式
- 值保真：补集信号使用 `parse_signal_text_full()` 保留逗号后附加信息（如 `否,过期时间=2026-03-27`）
- Round-robin：补集信号值从真实样本池中轮询取用，保持与真实数据分布一致

---

## v10.12.0 (2026-04-18)

### feat: 统一文件发送规范 + Judge 报告增强 + 强制确认脚本 + 数据清理

**新增文件：**

- `references/file-delivery.md`（新建）：统一文件发送规范
  - 覆盖 Catclaw（大象消息）/ CatPaw / CatDesk 三种运行环境
  - 覆盖 HTML / Excel / Markdown 三种文件类型
  - 提供各文件类型的发送模板和各步骤发送速查表
  - 所有涉及文件发送的步骤统一引用此文档，消除跨步骤不一致

- `scripts/confirm_production_mode.py`（新建）：Phase 2 生产模式强制确认脚本
  - 必须在 `scripts/generate_prompts.py` 执行后、`scripts/run_batch_api.py` / SubAgent 调度前运行
  - 生成 `exec_mode.json` 作为互斥锁，缺失时后续脚本拒绝执行

- `scripts/confirm_judge_mode.py`（新建）：Phase 3 Judge 模式强制确认脚本
  - 必须在 `judge/scripts/run_judge.py` 执行前运行
  - 同样生成 `exec_mode.json` 锁文件

**功能增强：**

- `judge/scripts/gen_report.py`：
  - 新增 `load_sessions_index()` 函数，从 `data/runs/*/final.jsonl` 中按 `session_id` 提取完整对话原文
  - 低分 session（得分 < 阈值）报告中新增 `<details>` 折叠块，展示完整对话原文，便于人工复核 LLM 判断
  - 报告生成后终端打印绝对路径（与 Phase 0 HTML 报告发送方式对齐）

- `scripts/analyze_signals.py`：信号名与 Excel 样本不匹配时自动降级标记 `_no_sample`，填入默认规则并豁免校验警告

- `scripts/generate_prompts.py`：新增 `--confirmed` 参数，缺失时拒绝执行，强制 Agent 先展示摘要等待用户确认

- `scripts/postprocess.py`：执行前强制检查 `exec_mode.json` 是否存在，缺失时报错退出

- `harness_verify/verify_graph.py`：`verify_signal_rules` 豁免 `_no_sample` 信号的空规则警告

**文档更新：**

- `references/phase0-sop-parsing.md`：HTML 审查报告发送方式统一引用 `file-delivery.md § 2-A`
- `references/phase2-production.md`：Excel 导出后文件发送方式统一引用 `file-delivery.md § 2-B`；Step 3.5 明确生产模式确认的两步操作规范
- `references/phase3-judge.md`：质检报告/回归报告发送方式统一引用 `file-delivery.md § 2-C`；根因类别定义表新增"对应 SKILL.md 问题类型"和"修改入口文件"两列，与 `SKILL.md § 数据质量问题定位指南` 对齐
- `references/file-structure.md`：新增 `file-delivery.md` 条目
- `SKILL.md`：Phase 0 HTML 报告发送方式引用 `file-delivery.md`；执行红线新增"含中文/特殊字符 JSON 参数传递原则"
- `judge/prompts/judge_prompt_v1.1.md`：SubAgent 调度模板同步更新

**数据清理：**

- 删除所有动态过程文件和旧场景样例数据（`data/paths/`、`data/sop/dcdcdc_*`、`references/sop/` 下已废弃 SOP 文档）
- 删除所有 `__pycache__` 目录（已在 `.gitignore` 中排除）
- `assets/real_signals/` 目录保留 `.gitkeep`，防止目录被 git clean 误删

- `SKILL.md`：版本号 `v10.11.4` → `v10.12.0`

---

## v10.11.4 (2026-04-17)

### fix: 健壮性 + 安全性 + 文档一致性全面提升（9项改进落地）

**代码层修复：**

- `scripts/postprocess.py`：
  - 新增 `_fix_content_quotes()` 函数，统一弯引号处理 + 修复 `content/reason/summary` 等字段裸双引号
  - `parse_trajectory()` 改为三层降级逻辑：直接解析 → 裸引号修复后解析 → regex 提取 + 再修复，显著提升 LLM 输出的容错率

- `judge/scripts/collect_subagent_results.py`：
  - 同步支持批次格式（`batch_XXX.result.txt`，JSON 数组）和单 session 格式（兼容旧数据）
  - 增加 `fix_json_string_quotes()` 自动修复 reason/summary 字段裸引号

- `judge/scripts/run_judge.py`：
  - `--export-prompts` 模式改为批次导出（每批默认 5 个 session），生成 `batch_XXX.prompt.txt` + `batch_ids.json`
  - `--batch-size` 新参数（默认 5）控制每批 session 数量
  - `--appid` 接入 `lib/api_client.resolve_appid()`，支持环境变量 `CS_FACTORY_APPID`

- `lib/api_client.py`：新增 `resolve_appid()` 函数，支持命令行参数 > 环境变量 `CS_FACTORY_APPID` 的优先级链
- `scripts/run_batch_api.py`：AppId 解析统一走 `resolve_appid()`，支持明文参数 + 环境变量两种安全传入方式

**文档层修复：**

- `references/phase3-judge.md`：
  - 全面更新 Judge SubAgent 调度模板为批次格式（batch_XXX.prompt.txt + batch_ids.json）
  - 文件结构章节同步为批次格式
  - 去除 AskQuestion 工具引用，改为平台无关描述

- `references/checkpoint.md`：Phase 3-A/B 条目更新为批次格式
- `references/file-structure.md`：judge_runs 目录结构更新为批次格式
- `judge/prompts/judge_prompt_v1.1.md`：SubAgent 调度模板同步更新为批次格式，追加 JSON 字符安全规则
- `references/phase0-sop-parsing.md`：意图识别规则表中 `P1/P2/.../P9` 编号替换为描述性文字
- `references/phase2-production.md`：分批编排示例中 `P1/P2/P3` 编号替换为描述性文字
- `SKILL.md`：Persona 说明中 `P1/P2/.../P9` 编号替换为类型描述性文字
- `CHANGELOG.md`：移除 AskQuestion 工具引用

**结构性完善：**

- `assets/real_signals/.gitkeep`（已存在）：real_signals 目录常驻，防止 git clean 时丢失目录

- `SKILL.md`：版本号 `v10.11.3` → `v10.11.4`

---

## v10.11.3 (2026-04-17)

### chore/fix: 技术债清理 + 新场景接入（dcdcdc）+ API 生产模式抽取

**技术债清理（代码层）：**
- `scripts/gen_custom_batch.py`：修复 `order_signals` 过滤逻辑不一致 — 补加 `v != "__mock__"` 条件，与 `generate_prompts.py` 保持一致，避免 `__mock__` 占位符混入 `prompts_todo.jsonl`
- `scripts/postprocess.py`：将 `import datetime` 从 `split_trajectory()` 函数体内移至文件顶部（符合 PEP 8，避免每次调用重复导入）
- `scripts/real_signal_pool.py`：**删除** — 文件头自述已废弃，无任何主流程代码 import/调用

**技术债清理（文档注释层）：**
- `judge/scripts/pick_low_score_tasks.py`：`session_id_to_task_id()` 函数添加交叉引用注释，说明与 `analyze_root_cause.py` 中同名函数实现完全一致，提醒同步维护
- `judge/scripts/analyze_root_cause.py`：同上，添加指向 `pick_low_score_tasks.py` 的交叉引用注释

**新脚本 / 新目录：**
- `lib/api_client.py`（新增）：提取 兼容 Chat Completions 的模型 API 调用为共享模块，供 `run_batch_api.py`（生产侧）和 `run_judge.py`（质检侧）共用，消除跨脚本重复实现
- `lib/__init__.py`（新增）：模块包标识
- `scripts/run_batch_api.py`（新增）：Phase 2 API 模式生产脚本，替代 SubAgent spawn，直接调 API 批量执行对话生成；支持 `--workers` 并发、断点续跑、`exec_mode.json` 互斥锁

**新场景接入（dcdcdc）：**
- `data/sop/dcdcdc_graph.json`：dcdcdc 场景 SOP Graph
- `data/sop/dcdcdc_persona.md`：dcdcdc 场景 Persona 定义
- `data/sop/dcdcdc_signal_rules.json`：dcdcdc 场景信号规则
- `data/paths/dcdcdc_paths.jsonl`：dcdcdc 场景路径枚举产物
- `references/sop/dcdcdc.md`：dcdcdc 场景原始 SOP 文档

**旧场景数据清理：**
- 删除 `data/sop/daocandaopiao_graph.json`、`daocandaopiao_persona.md`、`daocandaopiao_signal_rules.json`
- 删除 `data/sop/shanguowuqishoujiedan_graph.json`
- 删除 `data/sop/xiangxiang-no-rider_graph.json`

**新版 Judge 资产：**
- `judge/prompts/judge_prompt_v1.1.md`（新增）：judge prompt v1.1
- `judge/rubric/rubric_v1.1.md`（新增）：rubric v1.1

**其他文档更新：**
- `references/aida-api-doc.md`（新增）：AIDA 工作流应用 API 文档，供 Phase 0 可选数据拉取参考
- `references/phase3-judge.md`、`phase2-production.md`、`phase1-prompt-generation.md`、`phase0-sop-parsing.md`、`file-structure.md`、`SKILL.md`：随以上变更同步更新

- `SKILL.md`：版本号 `v10.11.2` → `v10.11.3`

---

## v10.11.2 (2026-04-17)

### fix: Phase 3 Judge 模式选择强制询问用户

**背景**：Phase 3 执行时 Agent 未询问用户选择 Judge 模式（SubAgent/API），直接默认走 SubAgent 模式，与 Phase 2 生产侧的交互一致性要求不符。

**变更内容（仅文档层）：**

- `references/phase3-judge.md`：
  - 在所有执行步骤之前新增 **"步骤 0：Judge 模式选择（必须先询问用户）"**，用 `⚠️` 标注强制要求
  - 给出 Agent 需要向用户发出的询问文案（在对话中直接展示，等待用户回复）
  - 原"脚本入口（SubAgent 模式，推荐）"章节标题去掉"推荐"字样，改为中性描述
  - 流程 D 步骤 5（优化验证重跑 Judge）同样补充强制询问说明，并补全 API 模式的命令示例

- `SKILL.md`：
  - Phase 3 调度规范新增 `⚠️` 规则："进入 Phase 3 后，必须先询问用户选择 Judge 执行模式，不得假设或默认，也不得复用 Phase 2 的模式选择"
  - 版本号：`v10.11.1` → `v10.11.2`

---

## v10.11.1 (2026-04-15)

### fix: 到餐发票场景数据修正 + Phase 1/2 测试验证通过

**数据修正（daocandaopiao）：**
- `daocandaopiao_signal_rules.json`：`订单退款状态` 枚举值从 `["无退款"]` 修正为 `["全部退款", "部分退款", "无退款"]`（两处同步：`_production_signals` + `signals` 完整库）
- `daocandaopiao_graph.json`：START 节点 branches 拆分 `"部分退款/无退款"` → 两个独立 key `"部分退款"` 和 `"无退款"`（均指向 N010），路径骨架语义更清晰
- `daocandaopiao_persona.md`：删除 P1 顺从配合型（占比表格行 + 正文块），Persona 总数从 8 类调整为 7 类（P2~P8）；末尾「默认轮换」建议同步更新

**测试验证：**
- 路径 P016（A13→A15→A25→A26）× Persona P7/P8，每 task 2 条样本，共 4 session / 16 逐轮行
- `verify_prompts.py`、`postprocess.py`、`verify_output.py` 全部通过（error=0，warning=0）
- Excel 导出人工抽查通过

---

## v10.11.0 (2026-04-15)

### feat: 信号 Mock 架构重构 — 移除真实信号直接拼接，改为 LLM 内部 mock

**背景**：旧架构直接从 Excel 真实样本循环取值拼接参考信号，存在与 SOP 骨架锁定值冲突的风险（如品牌类型已锁定「指定品牌」但参考信号取到了「其他品牌」名）。本版本重构信号处理架构，改为 LLM 在生成对话前自行 mock 信号值，骨架永远优先。

**核心原则（骨架优先）**：
- 骨架「当前场景信号（已由SOP路径锁定）」中的判断信号值，不得修改
- 同名信号骨架有明确值时，以骨架为准，不需要 mock
- 信号 Mock 指令放在骨架**之后**注入 prompt，明确优先级

**新增文件：**
- `scripts/analyze_signals.py`：Phase 0-A3 信号分析脚本
  - 读取 Excel「所有信号」列，解析各信号 key 的枚举/mock/静态/动态类型
  - 输出草稿 `data/tmp/{sop_id}_signal_rules_draft.json` 和 LLM 评审 Prompt
  - 需人工审核确认后，复制为 `data/sop/{sop_id}_signal_rules.json` 正式入库
  - 信号分类：source（judgment/slot/other）× type（enum/mock）× variability（static/dynamic）

**变更文件：**
- `references/expand-prompt.md`：
  - 将 `{REF_SIGNALS}` 变量重命名为 `{SIGNAL_MOCK_RULES}`，位置保持在骨架之后
  - 新增「信号 Mock 规则（骨架优先）」说明块，含三条优先级原则
  - 输出 JSON 格式新增 `mocked_signals` 字段（判断信号 + 参考信号分组）
  - 新增 `{SIGNAL_MOCK_RULES}` 和 `mocked_signals` 格式详细说明
- `scripts/generate_prompts.py`：
  - 移除 `real_signal_pool` 导入和所有信号池调用代码
  - 新增 `load_signal_rules()` 函数，加载 `data/sop/{sop_id}_signal_rules.json`
  - 新增 `build_signal_mock_rules()` 函数，按骨架优先原则构造 `{SIGNAL_MOCK_RULES}` 文本块
  - `build_prompt()` 参数从 `ref_signals` 改为 `signal_mock_rules`
  - `prompts_todo.jsonl` 移除 `real_signal_idx` 字段（不再需要循环索引）
- `scripts/postprocess.py`：
  - 移除 `signal_pool`（real_signal_pool）相关初始化和补集信号注入逻辑
  - 新增 `build_all_signals_text()` 函数：从 `order_signals`（骨架权威值）+ `mocked_signals.参考信号` 组装「所有信号」
  - `split_trajectory()` 简化：不再需要 `real_signal_idx` / `signal_pool` 参数
  - 「所有信号」仅含「路径信号」和「参考信号」两段，不含补集信号
- `references/output-schema.md`：
  - 更新「所有信号」字段说明：分为「路径信号」（骨架判断信号）+「参考信号」（LLM mock）
  - 移除「补集信号」描述（旧架构遗留）
  - 更新输出示例，新增信号组装来源对照表
- `references/checkpoint.md`：
  - 产物分类表新增 `data/sop/{sop_id}_signal_rules.json`
  - Phase 0 流程新增 Phase 0-A3（信号分析）节点
  - Phase 0-A2 移除旧版「`_SOP_POOL_CONFIGS` 注册」前置条件
  - Phase 1-B 输入更新：从「信号池配置」改为「`{sop_id}_signal_rules.json`（可选）」
  - `prompts_todo.jsonl` 契约字段更新，新增 `order_signals` 字段说明
- `SKILL.md`：
  - 版本号：`v10.10.5` → `v10.11.0`
  - 描述：Excel 从必须改为可选，信号处理从「直接取值拼接」改为「LLM 内部 mock」
  - 核心设计图：新增 Phase 0 SubAgent-C（信号分析）
  - 执行红线：移除旧规则 4（信号池配置），替换为新规则 4（信号 Mock 架构说明）
  - 快速移植步骤：移除信号池配置步骤，新增信号分析步骤

---

## v10.10.5 (2026-04-14)

### fix: 技术债清理 — 7 处文档/代码不一致修复

**代码层：**
- `scripts/gen_custom_batch.py`：**修复 H1** — 删除 `prompts_todo.jsonl` 中的 `"prompt"` 字段写入，违反执行红线（prompt 正文写入 jsonl 会在主 Agent 读索引时污染上下文）；prompt 内容已单独写入 `subagent_prompts/{task_id}.prompt.txt`，与 `generate_prompts.py` 行为对齐
- `judge/scripts/gen_report.py`：**修复 M1** — 第 265 行 `"turn 结果"` 改为 `"session 结果"`，与 Phase 3 已改为 session 级打分的实际情况一致

**文档层：**
- `references/checkpoint.md`：**修复 H2** — Phase 3-A/B 节点中的 `{task_id}` 全部改为 `{session_id}`（文件命名实际由 `run_judge.py` 用 session_id 生成）；"每条样本一个文件" 改为 "每个 session 一个文件（文件名=session_id）"；"补跑单 task" 改为 "补跑单 session"
- `references/phase3-judge.md`：同步修复目录结构图中的 `{task_id}` → `{session_id}`
- `SKILL.md`：**修复 H3** — 核心设计图补充 SubAgent-B 一行；Phase 0 调度规范从"spawn 一个 SubAgent"改为"并行 spawn 最多两个 SubAgent（A：SOP 解析，B：Persona 分析，可选）"，完成信号也对应更新
- `references/output-schema.md`：**修复 M2** — 循环策略从硬编码"427 条"改为场景无关描述，指向 `_SOP_POOL_CONFIGS`
- `scripts/analyze_persona.py`：**修复 L1** — docstring 用法示例、输出说明、`--out` 帮助文本中的路径统一从 `references/sop/` 改为 `data/sop/`（与实际默认输出路径一致）
- `scripts/generate_prompts.py`：**修复 L2** — `--persona-file` 帮助文本示例路径从 `references/sop/` 改为 `data/sop/`

---

## v10.10.4 (2026-04-14)

### refactor: Persona 分析归入 Phase 0 并行执行，产物目录统一到 data/sop/

**背景**：v10.10.3 将 Persona 分析放在 Phase 1 之前（Step -1），逻辑归属不清晰；且产物写入 `references/sop/` 违反了 pipeline 产物应放 `data/` 目录的约定。本版本将其重构为 Phase 0 的并行 SubAgent，与 SOP 解析同级。

**变更内容：**

- `references/phase0-sop-parsing.md`：**重构为并行双 SubAgent 调度模式**
  - SubAgent-A（SOP 解析）：原有逻辑，无变化
  - SubAgent-B（Persona 分析，可选）：与 SubAgent-A 并行执行；有真实样本 Excel 且已在 `_SOP_POOL_CONFIGS` 注册时才运行
  - 明确并行条件：两者输入不同（SOP 文件 vs Excel 对话历史），互不依赖，可安全并行
  - 完成信号统一：SubAgent-A 输出「✅ SubAgent-A 完成」，SubAgent-B 输出「✅ SubAgent-B 完成」

- `references/phase1-prompt-generation.md`：移除 Step -1 详细内容
  - 替换为简短的「前置说明」，指向 Phase 0 已完成 Persona 分析的事实
  - 保留 `--persona-file` 用法说明，路径改为 `data/sop/<sop_id>_persona.md`

- `scripts/analyze_persona.py`：默认输出路径从 `references/sop/` 改为 `data/sop/`
  - Prompt 中 SubAgent 写入路径同步更新
  - 完成信号改为「✅ SubAgent-B 完成，Persona 已写入 data/sop/...」

- `references/checkpoint.md`：同步更新产物分类表和主流程 Trace 表
  - pipeline 产物新增 `data/sop/{sop_id}_persona.md`
  - 主流程新增 Phase 0-A2 节点（Persona 分析，可选）

- `SKILL.md`：快速移植步骤 4.5 描述同步调整
  - 明确「与步骤2（Phase 0）并行执行」
  - 输出路径更新为 `data/sop/{sop_id}_persona.md`
  - 步骤7中 `--persona-file` 路径同步

---

## v10.10.3 (2026-04-14)

### feat: 场景定制化 Persona 分析（基于真实对话聚类）

**新功能：从真实信号样本 Excel 中自动分析用户行为，生成场景定制化 Persona 文件。**

**新增文件：**

- `scripts/analyze_persona.py`：Persona 聚类分析脚本
  - 按 `sessionId` 分组，取每组最后一行的完整对话历史（解决多轮 session 渐进递增导致的重复/截断问题）
  - 自动适配多种对话历史列名（`对话历史` / `对话记录` / `历史上文` / `历史消息`），兼容不同 Excel 格式
  - 过滤无效对话（< 50 字），取每组唯一 session 最长完整版本
  - 构建分析 Prompt，交给 SubAgent 执行聚类（主 Agent 不在主 session 处理长文本）
  - 分析 Prompt 缓存到 `data/tmp/{sop_id}_persona_analysis.prompt.txt`，SubAgent 读文件执行
  - 分析要求强制包含**情绪渐进增强人设**（平静→不耐烦→愤怒），不依赖真实样本中一定存在此类型
  - 支持 `--dry-run`（只打印对话摘要，不生成 Prompt）、`--max-sessions`（限制分析条数）、`--excel`（显式指定路径）
  - 自动从 `_SOP_POOL_CONFIGS` 读取 Excel 路径，无需手动指定

**修改文件：**

- `scripts/generate_prompts.py`：新增 `--persona-file` 参数
  - 可指定定制化 Persona 文件（如 `references/sop/{sop_id}_persona.md`）覆盖默认 `references/persona.md`
  - 不指定时行为与旧版完全一致（无缝向后兼容）

- `references/phase1-prompt-generation.md`：新增 Step -1（可选）
  - 详细说明 Persona 定制化分析的四个步骤（脚本执行 → SubAgent 分析 → 人工抽查 → Phase 1 中指定）
  - 明确主 Agent 上下文隔离：聚类分析由 SubAgent 执行，Prompt 通过文件传递

- `SKILL.md` 快速移植步骤表：新增步骤 4.5（可选）
  - 在步骤4（添加 Excel 样本）之后、步骤5（更新 persona.md）之前
  - 说明定制化分析的使用条件和 Phase 1 命令中如何指定

---

## v10.10.2 (2026-04-14)

### docs: phase2-production.md 精确性 / 健壮性优化（最小改动）

**目标**：消除 Phase 2 执行中因文档描述不清晰导致的四类 Agent 误操作，不改任何脚本逻辑。

**变更内容（仅 `references/phase2-production.md`）：**

1. **Step 4 顶部新增上下文隔离警示**  
   明确说明主 Agent 读取 `prompts_todo.jsonl` 时**只提取 `task_id` 列表，不加载 `prompt` 字段正文**，避免大 prompt 正文堆叠污染主 session。

2. **SubAgent 调度 Prompt 模板新增 JSON 字符安全规则**  
   明确禁止话术 content 字段内嵌套 ASCII 直双引号（`"`），要求使用中文引号或转义，防止 SubAgent 生成的 result.txt 因 JSON 格式错误导致 postprocess 解析失败。

3. **Step 5 顶部新增三条强约束说明**  
   - 明确 postprocess 只接受 `result.txt`（而非 `result.json`），消除文件格式混淆  
   - 明确 result 文件必须位于 `subagent_prompts/` 目录  
   - 明确 `postprocess.py` 不支持 `--sop-id` 参数，避免参数误用报错

4. **Step 6 新增校验失败的回溯指引**  
   说明 `verify_output.py` 不修改数据，校验失败时应回溯到 Step 4 或 Step 5，而非原地修文件。

5. **Step 7 新增主动提示要求**  
   明确要求 Agent 在校验通过后**主动询问用户**是否需要导出 Excel，而非等用户提醒。

6. **恢复策略补充 result.txt 解析失败的处置路径**  
   区分"缺失 result.txt"（补跑 SubAgent）与"解析失败"（修复文件内容 → 重跑 postprocess）两种情况。

---

## v10.10.1 (2026-04-13)

### fix/refactor: 技术债清理 — 断裂调用链修复 + 校验盲区补全 + 冗余代码删除

**代码层（Bug 修复）：**
- `judge/scripts/run_regression.py`：**修复启动即崩溃** — 删除对已不存在的 `llm_utils.py` 的导入；改为从 `run_judge.py` 直接导入 `call_llm` 和 `load_judge_prompt`，同时消除了回归测试与正式质检使用不同 prompt 提取逻辑的一致性问题（原 `run_regression.py` 返回整个 `.md` 全文，`run_judge.py` 只取 ``` 块核心 prompt）

**代码层（校验盲区修复）：**
- `harness_verify/verify_prompts.py`：`TEMPLATE_PLACEHOLDERS` 正则补入 `REF_SIGNALS` 和 `EMOTION_GUIDELINES`，新增的两个占位符现在会被正确检测到未替换残留（原版本检测存在盲区）

**代码层（脆弱逻辑清理）：**
- `scripts/postprocess.py`：删除 18 行"靠 task_id 字符串猜 sop_id"的脆弱兼容逻辑（原逻辑检测 task_id 是否含 `"xiangxiang"` 来决定信号池，不可扩展且 fallback 指向的 Excel 已不存在）；`sop_id` 缺失时改为直接打印 WARNING + `signal_pool = None`
- `scripts/postprocess.py`：同步清理 `split_trajectory()` 内部的 `get_default_pool()` fallback，`signal_pool=None` 时补集内容直接为空，不再静默注入错误信号

**代码层（行为一致性）：**
- `scripts/generate_prompts.py`：信号池加载失败时从 `raise SystemExit` 改为 `print(WARNING) + signal_pool = None`，与 `gen_custom_batch.py` 的处理行为对齐（两个 Phase 1 入口现在行为一致）

**代码层（冗余清理）：**
- `scripts/generate_prompts.py`：删除 `tasks` dict 中 `"path"` / `"persona"` 两个冗余别名字段（与 `"path_id"` / `"persona_id"` 值完全相同，下游 `postprocess.py` 从不读取）
- `judge/scripts/run_judge.py`：`--export-prompts` 分支补写 `task_ids.json` 输出，与原 `export_judge_prompts.py` 功能完全对齐
- `judge/scripts/export_judge_prompts.py`：**删除** — 功能已完全被 `run_judge.py --export-prompts` 覆盖

**注释清理：**
- `scripts/postprocess.py`：删除对已删文件 `produce_batch.py` 的历史注释引用（"从 produce_batch.py 迁移，保持逻辑一致"）

---

## v10.10.0 (2026-04-13)

### docs: SKILL.md 模块化瘦身——执行细节全部下沉到 references/

**文档层：**
- 新建 `references/phase0-sop-parsing.md`：将原 SKILL.md 中 Phase 0 的 SubAgent Prompt 构造模板、Step 1~6、JSON 格式示例、自查 checklist 合并进去（原文件只有 Step 1~4）
- 新建 `references/phase1-prompt-generation.md`：搬入 Phase 1 完整执行手册（参数收集模板、脚本命令、verify_prompts 用法、prompt 抽查方式）
- 新建 `references/phase2-production.md`：搬入 Phase 2 完整执行手册（SubAgent 调度 Prompt 模板、分批编排规范、postprocess、verify_output、export_excel、恢复策略、清理命令、git 规范）
- 新建 `references/phase3-judge.md`：搬入 Phase 3 全部内容（脚本入口、Judge SubAgent 调度 Prompt 模板、工作流 A/B/C、三层协同原则、judge/ 目录文件结构树）
- 新建 `references/file-structure.md`：搬入完整文件树及入库/忽略说明
- 重写 `SKILL.md`：每个 Phase 只保留触发条件 + 调度规范要点 + reference 跳转链接；保留全链路 Trace 表格、移植表格、执行红线、Persona 说明、版本发布 Hook

**已验证**：
- 所有执行细节均完整迁入对应 reference 文件，不删除任何内容
- SKILL.md 瘦身后保留所有调度关键信息完整

---

## v10.9.3 (2026-04-10)

### fix: 全平台一致性修复 — 6 项 Bug 修复（实战问题总结）

**代码层：**
- `judge/scripts/run_judge.py`：**核心 Fix** — 新增 `_extract_prompt_block()` 函数，`load_judge_prompt()` 改为只提取 `.md` 文件中 ` ``` ` 包裹的核心 prompt 块；`--export-prompts` 生成的 `.prompt.txt` 现在是纯净可执行 Prompt，不含文档 header（`# cs-judge...`、`> 输入变量：{{历史消息}}` 等元信息）。顺带消除了头部变量占位符行与实际内容拼接的格式污染问题
- `scripts/generate_prompts.py`：注释修正，`{SEQ}` 兼容逻辑标注更清晰（`{BATCH}` 为当前模板变量名，`{SEQ}` 为向后兼容别名）

**文档层（SKILL.md）：**
- **Phase 2 Step 4**：移除误导性的"无需手动运行命令"说法；新增 **SubAgent 调度 Prompt 模板**（主 Agent 照此向生产 SubAgent 下达任务，包含输入/输出文件路径、执行步骤、完成信号）；新增"主 Agent 在全批完成后的检查点"规范
- **Phase 3**：新增 **Judge SubAgent 调度 Prompt 模板**（包含完整的输出 JSON 格式规范），替换原有仅列字段的模糊说明；新增分批编排规范提示
- **全局**：所有脚本命令示例中的 `python3` 统一改为 `python`（Windows 兼容；shebang 行 `#!/usr/bin/env python3` 不变，保持 Unix 兼容）

**已验证**：
- 全部脚本 Python 语法检查通过（16 个文件）
- Phase 1 → Phase 2 SubAgent → postprocess → verify_output 全链路验证通过
- Phase 3 judge prompt 生成 → Judge SubAgent 打分 → collect_subagent_results → gen_report 全链路验证通过

---

## v10.9.2 (2026-04-08)

### feat: 新增 export_excel.py — final.jsonl → Excel 可选导出

**代码层：**
- `scripts/export_excel.py`：**新增** Phase 2 可选导出工具，将 `final.jsonl` 转换为带格式 Excel（深蓝表头 + 交替行底色 + 首行冻结 + 自动筛选，支持 `--out` 自定义输出路径）

**文档层：**
- `SKILL.md` Phase 2：在 Step 6 校验之后新增 **Step 7（可选）导出 Excel** 说明及命令示例
- `SKILL.md` 文件结构图：补充 `export_excel.py` 条目

---

## v10.9.1 (2026-04-08)

### fix/docs: 实战测试 Bug 修复（闪购无骑手接单场景）

**代码层：**
- `scripts/postprocess.py`：修复注释中残留的"蛋麻"非正式称谓，改为"主 Agent"
- `scripts/gen_custom_batch.py`：**修复 Bug** — 补全 `subagent_prompts/` 目录创建及独立 `.prompt.txt` 文件写出逻辑，与 `generate_prompts.py` 行为对齐（原版本运行后 `verify_prompts.py` 会报 subagent_prompts 目录不存在）
- `scripts/real_signal_pool.py`：新增 `shanguowuqishoujiedan` 场景配置（`_SOP_POOL_CONFIGS`）

**文档层：**
- `SKILL.md` Phase 1 Step 3：移除 Windows 专用的 `type` 命令示例，改为 Agent 直接读文件的描述
- `SKILL.md` Phase 1 Step 2 快速测试示例：修正硬编码 `--sop-id invoice` 为 `--sop-id <sop_id>`，`--samples` 默认值从 5 改为推荐 2
- `SKILL.md` 文件结构图：补充 `gen_custom_batch.py` 注释（同样输出 .prompt.txt）

**新场景数据：**
- 新增 `data/sop/shanguowuqishoujiedan_graph.json`：闪购·无骑手接单场景 SOP Graph（30节点 12动作）
- `run_20260408_001`：18 session / 78 turn，验证通过（error=0，warning=0）

---

## v10.9.0 (2026-04-08)

### refactor: 目录规范化 + Pipeline 校验增强 + pipeline_check 合并

**目录结构重构：**
- `data/sop/`：SOP Graph JSON 统一迁移至此目录（原 `data/sop-graph-{id}.json` → `data/sop/{id}_graph.json`）
- `data/paths/`：路径枚举产物迁移至此（原 `data/{id}_paths.jsonl` → `data/paths/{id}_paths.jsonl`）
- `data/runs/`：生产运行目录（原 `runs/` → `data/runs/`）
- `data/judge_runs/`：Judge 运行目录（原 `judge/runs/` → `data/judge_runs/`）
- `data/generated/`：最终归档产物目录（保持不变）
- `references/sop/`：SOP 原始文档目录（新增）

**脚本层：**
- `scripts/pipeline_check.py`：**删除**，功能合并至 `harness_verify/verify_output.py`
- `harness_verify/verify_output.py`：合并原 pipeline_check 功能，新增 `--json` 报告输出、`--scan-dir` 批量扫描、`--strict` 模式；`load_valid_actions()` 路径更新为 `data/sop/{sop_id}_graph.json`
- `harness_verify/verify_prompts.py`：**新增**，Phase 1 产物完整性校验（prompts_todo.jsonl 字段、.prompt.txt 存在性、无残留占位符、文件数一致性）
- 全部脚本路径常量同步更新（`DATA_DIR`、`RUNS_DIR`、`SOP_DIR` 等）

**文档层：**
- `SKILL.md`：补充 Phase 1-C 校验节点（Pipeline 节点总览表）；新增 Step 2.5 产物校验命令；新增 Phase 0 SubAgent 调度 Prompt 模板；文件结构图补充 `verify_prompts.py`
- `references/checkpoint.md`：新增 Phase 1-C Prompt 校验节点行；Phase 2-D 校验工具更新为 `verify_output.py`（移除 pipeline_check 引用）

**工程规范：**
- `.gitignore`：补充 `judge/runs/` / `runs/` 排除规则，防止中间产物误入库

---

## v10.8.1 (2026-04-07)

### docs: 补齐统一 Trace & Schema，并精简 SKILL.md 为索引式入口

**文档层：**
- `SKILL.md`：新增“索引导航”，将 `persona.md` / `output-schema.md` / `checkpoint.md` 明确为单独 reference 入口
- `SKILL.md`：将“预期输出”重写为简版 Trace & Schema 总览，不再在正文重复维护大段字段样例
- `SKILL.md`：将 Persona 具体明细从正文移除，改为索引到 `references/persona.md`
- `SKILL.md`：修正 Phase 0 红线中的旧路径表述（统一为 `data/sop-graph-{sop_id}.json`）
- `SKILL.md`：修正 SOP 解析规则中的“判断信号”标记写法
- `references/checkpoint.md`：重写为统一的全链路 Trace / Checkpoint / Schema 文档，覆盖节点输入、输出、强约束、校验与恢复规则
- `references/output-schema.md`：补充对 `checkpoint.md` 的索引，并修正真实样本路径描述为 `assets/real_signals/real_signals_sample_{sop_id}.xlsx`

---

## v10.8.0 (2026-04-07)

### chore/docs: sop-graph 迁移至 data/ + .gitignore 过程产物排除

**代码层：**
- `scripts/generate_prompts.py`：`load_sop_graph()` 优先从 `data/` 查找，兼容旧路径 `references/`
- `scripts/enumerate_paths.py`：`--graph` 自动查找逻辑同步更新（`data/` → `references/` 兼容回退）
- `scripts/pipeline_check.py`：新增 `DATA_DIR` 常量，`load_valid_actions()` 优先查 `data/`
- `harness_verify/verify_graph.py`：路径查找逻辑同步更新（`data/` 优先），帮助文本更新
- `harness_verify/verify_output.py`：`load_valid_actions()` 同步更新

**文件迁移：**
- `references/sop-graph-invoice.json` → `data/sop-graph-invoice.json`
- `references/sop-graph-xiangxiang-no-rider.json` → `data/sop-graph-xiangxiang-no-rider.json`

**新增文件：**
- `.gitignore`：排除 `*.prompt.txt` / `*.result.txt` / `data/runs/` / `data/judge_runs/` / `*.xlsx` / `data/generated/` 等过程产物和大文件

**文档层（SKILL.md）：**
- 文件结构图：`sop-graph-{sop_id}.json` 从 `references/` 移至 `data/`，注明"pipeline 产物，非用户原始输入"
- Phase 0 Step 4：说明持久化路径改为 `data/sop-graph-{sop_id}.json`
- Phase 2 恢复策略后：新增 🧹 清理过程产物说明（何时清理 + git 纯净版规范）

---

## v10.7.0 (2026-04-07)

### docs/refactor: 工程愿景对齐优化（SKILL.md + 快速失败 + judge prompt JSON 规范）

**代码层：**
- `real_signal_pool.py`：xlsx 文件不存在时改为 `raise FileNotFoundError`（快速失败），不再 warn-and-continue 静默产出空信号

**文档层（SKILL.md）：**
- `description` / 核心设计图 / 输入清单 对齐实际情况（增加 Excel 输入、主 Agent 调度层说明、输入清单）
- Phase 0 新增「推荐由 SubAgent 执行」说明，保持主 session 上下文干净
- ⚠️ 红线第4条：移除硬编码已支持场景列表，改为指向 `_SOP_POOL_CONFIGS`
- 分批编排规范：明确主 Agent 只传 task_id 列表 + 文件路径，SubAgent 自行 fetch 文件内容
- Persona 说明：新增后续规划（从真实样本提炼 Persona，SubAgent 自动更新）
- 删除 Phase 3 脚本入口处冗余的「LLM 由 sessions_spawn 执行」说明
- 版本发布 Hook 第2条：更正为只维护 CHANGELOG.md，SKILL.md 不重复维护版本详情
- 文件结构图：新增 `references/sop/` 目录说明

**话术规范：**
- `assets/speech-guidelines.md`：精简为四部分简明版（核心人设 / 表达红线 / 回复标准）

**质检系统：**
- `judge/prompts/judge_prompt_v1.0.md`：输出格式前新增 JSON 安全写法说明 + 完整正确示例，降低 reason/summary 字段裸双引号率（EXP-20260407-C）

---

## v10.6.0 (2026-04-07)

### refactor: 删除废弃文件 + 新增 harness_verify 节点校验目录

**变更内容：**
- 删除废弃脚本：`scripts/produce_batch.py`、`scripts/llm_utils.py`、`scripts/generate_prompts_custom.py`（功能已由主流程覆盖）
- 新增 `harness_verify/` 目录，包含三个阶段校验脚本：
  - `verify_graph.py`：Phase 0 节点校验，检查 sop-graph.json 格式完整性（节点引用、action_id 唯一性）
  - `verify_paths.py`：Phase 0 路径校验，检查 `*_paths.jsonl` 字段完整性（path_id 唯一性、turns/actions 一致性）
  - `verify_output.py`：Phase 2 输出校验，检查 `final.jsonl` 字段与动作合规性，替代原 `pipeline_check.py` 的核心职责
- Excel 样本目录规范化：从 `data/raw/` 统一移至 `assets/real_signals/`，路径在 `real_signal_pool.py` 的 `_SOP_POOL_CONFIGS` 中更新
- `generate_prompts.py`：修复信号池路由，改用 `get_pool_for_sop(args.sop_id)` 替代硬编码的 `get_default_pool()`
- `pipeline_check.py`：修复正则表达式，支持含 `_s\d+` 后缀的 session ID
- `judge/scripts/run_judge.py`：移除废弃注释，将 `urllib` 导入移至模块顶层
- `SKILL.md`：同步更新文件结构图、脚本引用、迁移步骤说明

---

## v10.5.1 (2026-04-02, commit e6b39a8)

### fix: 多场景信号池支持 + gen_custom_batch 信号注入

**根因**：`gen_custom_batch.py`（新脚本）生成 `prompts_todo.jsonl` 时未写入 `order_signals` / `real_signal_idx` / `sop_id` 字段，postprocess 展平时 `所有信号` 全部为空。同时 `postprocess.py` 的 `split_trajectory` 硬编码 `get_default_pool()`（发票427条样本），换场景后补集信号注入错误数据。

**修复内容：**
- `real_signal_pool.py`：新增 `get_pool_for_sop(sop_id)` 多场景工厂函数 + `_SOP_POOL_CONFIGS` 配置表，支持按 sop_id 路由到对应样本池。已登记场景：`invoice`（发票427条）、`xiangxiang-no-rider`（小象76条）。未登记 sop_id 直接报错（不静默失败）。
- `gen_custom_batch.py`：补全信号注入逻辑，写入 `order_signals`、`real_signal_idx`、`sop_id` 三字段。
- `postprocess.py`：`split_trajectory` 新增 `signal_pool` 参数；`main()` 按 `sop_id` 自动路由，兼容旧 run（无 `sop_id` 字段时从 task_id 猜测）。
- `generate_prompts.py`：`prompts_todo` 补写 `path_id` / `persona_id` / `sop_id` 字段，对齐 `gen_custom_batch`。
- `SKILL.md`：新增「换场景必须在 `_SOP_POOL_CONFIGS` 登记」红线警告。

**新场景接入方式**：在 `_SOP_POOL_CONFIGS` 添加一条配置（excel 路径 + ref/judge signal keys），无需改其他代码。

---

## v10.5 (2026-04-02, commit 3580b93)

### fix: generate_prompts_custom 补接真实信号样本池

**根因**：`generate_prompts_custom.py` 从未接入 `real_signal_pool`，导致 `prompts_todo.jsonl` 缺少 `real_signal_idx` 字段，postprocess 展平时补集信号被跳过，`所有信号` 只有路径信号。

**修复内容（`scripts/generate_prompts_custom.py`）：**
- 新增 import：懒加载 `real_signal_pool.get_default_pool`，失败时降级（打警告不崩溃）
- main() 初始化 `signal_pool` 和循环索引 `real_signal_idx = 0`
- task 写入时追加 `"real_signal_idx": assigned_real_idx`，每个 task 后 `+= 1`

**效果**：下次通过 `generate_prompts_custom.py` 生成的批次，postprocess 展平后 `所有信号` 将包含完整三层（路径信号 + 参考信号 + 真实样本补集）。

---

## v10.4.1 (2026-04-02, commit 040a3f6)

### expand-prompt 对齐优化（拉扯/情绪/E1/E3/F2）

**references/expand-prompt.md：**
- 约束1：补充「用户不认可方案时，可自由生成 2-4 轮拉扯对话，之后再推进检查点」
- 约束2：新增`【方案拒绝/拉扯规则】`— 用户不认可时应从新角度提出异议，客服重新承接情绪+再劝，P2/P5 尤其需要体现；新增`【情绪演变规则】`— 用户情绪不一成不变，开头激动、被安抚后可能再出现新不满，最终趋于接受
- 约束3：新增背景先行要求（E1：有背景信息时先交代背景再给方案）；新增情感引导/劝说要求（E3：给方案时必须有情感引导，不能仅事务性确认）；新增个性化结束语要求（F2：结合用户进线情景给个性化祝福，避免通用套话）

**小批测试（run_expand_test_0402，P002/P008/P011 × P2）：**
- ✅ 拉扯出来了（2-3轮异议），情绪演变有了，E1/E3/F2 基本达标
- 待跟进：P2 情绪强度稍温和，F2 个性化程度中等，P008 裸双引号 JSON 问题待处理

---

## v10.4 (2026-04-02)

### 文档清理 + 交互式参数收集

**文档层：**
- 全文替换"蛋麻" → "Agent"/"主 Agent"，"粑粑" → "用户"（SKILL.md 通用化）
- 删除所有历史纠错痕迹表述（"不调 API" 括号说明等）
- SKILL.md 末尾 CHANGELOG 明细全部移至 CHANGELOG.md，SKILL.md 只保留版本号+摘要+索引
- 版本发布 Hook：移除"更新外部记忆"步骤，改为只更新 CHANGELOG.md
- `produce_batch.py` 废弃说明简化

**功能层：**
- Phase 1 新增 Step 0"参数收集"：Agent 启动前询问路径集、Persona集、采样模式、--samples，用户不填走默认
- `generate_prompts.py` 命令示例补充 `--samples` 参数

---

## v10.3 (2026-04-02)

### 批量修复（全面审查 19 问题，跳过 H4）

**代码层：**
- H1：`run_regression.py` 补 `import sys`（启动即崩溃，已修复）
- H2：`pipeline_check.py` 字段 schema 对齐（移除废弃的订单/用户信号字段，改为`所有信号`）
- H3：`generate_prompts.py:417` 删除废弃的 `produce_batch.py` 引导提示
- H5：`compute_seq_map` 正则支持 `_s{n}_t{n}` 后缀，序号不再从 001 重置
- H6：`real_signal_pool.py` 构造函数支持传入自定义信号字段，不再硬编码发票场景
- H9（部分）：`generate_prompts_custom.py` 删废弃 produce_batch 提示
- M4：`run_judge.py` 断点续跑改为固定采样集（不随机重置）
- M5：`gen_report.py extract_session_id()` 同时去除 `_s{n}/_t{n}` 后缀
- M6：`postprocess.py` / `produce_batch.py` 历史消息末尾补 `\n`
- L1：`generate_prompts.py` 删除 `if True:` 死代码块
- L3：`enumerate_paths.py` DFS 新增环路检测 + `load_graph()` 异常处理

**文档层：**
- M1：SKILL.md 输出示例字段对齐（删废弃字段，补`所有信号`）
- M2：SKILL.md Phase 2 补 `pipeline_check.py` 调用步骤
- M3：SKILL.md Phase 2 补 SubAgent 失败恢复策略
- M7：SKILL.md Phase 2 补 SubAgent 分批编排规范（建议≤50任务/批）
- H7：`references/checkpoint.md` 完整重写为 v10.x SubAgent 架构（删废弃 produce_batch/phase1_results 描述）
- H8：SKILL.md 新增「换场景7步移植指南」（元 Skill 核心功能兑现）
- L2：SKILL.md 文件树补充 `make_batch_50.py` / `generate_prompts_custom.py` 说明

**跳过：** H4（generate_prompts_custom.py 信号不在前置上下文中，符合预期设计）

---

## v10.2 (2026-04-02)

### 测试与修复（30路径全面测试）

- **run_test_v2_30**：30路径（P001~P010 × P1/P3/P7），566行，30/30 全绿（commit ce07100）
- **postprocess.py** 新增英文键兼容（`session_id`/`conversation`/`assistant`）
- 遗留：P008 全批次 60字超标（LLM 遵循度问题，非 prompt 问题）
- **全面审查**：派 4 SubAgent 并行审查，发现 19 个问题（🔴9/🟡7/🟢3）

---

## v10.1 (2026-04-01)

### 修复与优化（4 问题修复）

- OpenClaw 配置：`maxChildrenPerAgent=10, maxConcurrent=10`（Gateway restart）
- SKILL.md Persona 表修正（P1-P9 无 P6，加建议占比）
- `generate_prompts.py` 新增 `--sample-mode one-per-path`（25路径=25任务，轮换分配 Persona）
- 新增 `scripts/postprocess.py`（纯后处理，不调 LLM），标记 `produce_batch.py` 废弃

---

## v10.0 (2026-04-01)

### 新特性
- **Phase 3 质检子模块上线**：cs-judge 整合进 `judge/` 子目录，不再是独立 Skill
- 新增 `judge/scripts/run_judge.py`：批量打分，按 session 抽样，断点续跑，temperature=0
- 新增 `judge/scripts/gen_report.py`：质检报告（维度通过率 / fail tags TOP5 / 最差 sessions / 人工决策 checklist）
- 新增 `judge/scripts/run_regression.py`：A/B 版本回归测试框架（回归集为空时友好退出）
- 新增 `judge/rubric/rubric_v1.0.md`：7 动作标签 × A～G 七大维度评分标准
- 新增 `judge/prompts/judge_prompt_v1.0.md`：3-Step 合并一次调用，占位符 `{{字段名}}` 对齐生产数据列名
- 新增 `judge/annotation-guide.md`：人工标注说明文档 v1.0
- SKILL.md 新增完整 Phase 3 工作流（流程 A/B/C + 三层协同原则）
- SKILL.md 新增完整 CHANGELOG（v9.1～v10.0）

### 文件变更
- 新增：`judge/` 子目录（rubric/ + prompts/ + scripts/ + regression/results/ + annotation-guide.md）
- 删除：旧 `skills/cs-judge/` 独立目录

---

## v9.8 (2026-03-31)

### 新特性
- **真实信号接入**：新增 `scripts/real_signal_pool.py`，加载 `data/raw/real_signals_sample.xlsx`（427条线上真实信号样本）
- **参考信号真实化**：`generate_prompts.py` 生成任务时，从真实样本池循环取参考信号（品牌名、电话），替换原 mock 值；循环索引递增，覆盖所有真实样本且重复最少
- **输出字段变更**：
  - `订单信号` → 替换为 `所有信号`（路径信号 + 真实样本补集信号）
  - 删除 `用户信号`（原固定 null 字段）
- **所有信号格式**：分两段，`### 路径信号`（判断+参考信号）+ 真实样本补集（原格式）

### 文件变更
- 新增：`scripts/real_signal_pool.py`
- 新增：`data/raw/real_signals_sample.xlsx`（真实样本，不纳入 git）
- 修改：`scripts/generate_prompts.py`（接入 RealSignalPool，注入参考信号）
- 修改：`scripts/produce_batch.py`（split_trajectory 输出字段变更）
- 修改：`references/output-schema.md`（字段说明更新） — cs-data-factory

---

## v9.7.2（2026-03-27）

**experience.md 新增 EXP-20260326-C（Skill 进化扫描 consolidation）**

- EXP-20260326-C：骨架约束用「检查点」不用「步骤序列」——步骤暗示执行语义导致一步一轮，检查点语义释放对话连发自由度（v9.7 核心设计洞察）

---

## v9.7.1（2026-03-26）

**新增 experience.md（Skill 进化扫描 consolidation）**

- 新建 `experience.md`，收录3条从近期调试中提炼的可复用经验：
  - EXP-20260325：脚本修复后必须重跑所有下游依赖文件
  - EXP-20260326-A：Persona 示例注入必须用完整对话块，不能只取短句
  - EXP-20260326-B：结构化字段（信号/动作）不能靠 LLM 生成，必须从元数据直接取

---

## v9.7（2026-03-26）

**骨架重构：步骤序列 → 对话检查点，释放用户连发创造性**

- `generate_prompts.py`：`build_path_content()` 改为检查点格式；新增「对话检查点说明」段，明确约束/不约束边界；「用户反应已锁定」→「用户态度锚点（不限句数）」；加「特别要求：态度锚点处至少2条连续消息」
- `expand-prompt.md`：约束1/2/4措辞同步更新；约束2强化「应该用多句话表达」，情绪化Persona每个锚点至少2条连发

**效果验证**：P013×P5，用户在每个态度锚点处自然产出2-3句连发，对话更真实

---

## v9.6（2026-03-26）

**一次 API 调用生成5条样本，token 节省约5x**

- `expand-prompt.md`：输出格式改为 JSON array，一次输出 `{OUTPUT_COUNT}` 条；新增多样性约束；变量说明表补充 `{BATCH}`（批次号）和 `{OUTPUT_COUNT}`（样本数，`--samples N` 控制，默认5）
- `generate_prompts.py`：每路径×Persona 只生成1条任务（不再按 sample 循环）；新增 `--samples N` 参数；`seq` → `batch`；`build_prompt()` 新增 `output_count` 参数
- `produce_batch.py`：`parse_trajectory()` 支持 array/object 双格式；单条解析失败记 warning 不中断整批，全部失败才算 error

---

## v9.5（2026-03-26）

**Persona 所有对话示例全量注入**

- `generate_prompts.py`：之前只取第一个对话示例块（`dialog_blocks[0]`），改为合并所有示例块（如 P1 有两个示例均注入），用空行分隔

---

## v9.4（2026-03-26）

**话术格式修复 + 完整对话示例注入**

- `produce_batch.py split_trajectory()`：对 agent content 清洗换行（`\n` → 空格）
- `pipeline_check.py`：新增 warning：话术回复含换行符
- `generate_prompts.py load_personas()`：改为提取完整对话示例块（保留连发结构，≤8行），降级才用「」短句
- `expand-prompt.md`：Persona 示例标题改为"真实对话片段，仅供语气/节奏/连发习惯参考"

---

## v9.3（2026-03-26）

**订单信号修复 + invoice_paths 刷新**

- `generate_prompts.py`：路径 signals 存入 task 元数据（`order_signals` 字段）
- `produce_batch.py split_trajectory()`：订单信号从 task 元数据取，不靠 LLM 输出
- 重新运行 `enumerate_paths.py` 刷新 `invoice_paths.jsonl`（修复外呼路径关联动作字段错误）

---

## v9.2（2026-03-26）

**对话鲜活度提升 + `/` 分隔符修复**

- `persona.md`：替换为基于150条真实样本的新版（删 P6，新增 P7 顺从配合/P8 信息主动/P9 焦虑担忧，附真实对话示例）
- `generate_prompts.py`：自动提取每个 Persona 真实对话中的用户话语作为 examples
- `expand-prompt.md`：新增 `{PERSONA_EXAMPLES}` 变量注入；约束3加"严禁用 `/` 拼接"
- `assets/speech-guidelines.md`：删除"超过100字用 `/` 分割"规则，改为禁用 `/`

---

## v9.1（2026-03-26）

**JSON 解析容错提升**

- `produce_batch.py`：JSON 解析失败改为 WARNING + 存储原始 raw 文本，不直接报错中断；validate 阶段兜底

---

## v9.0（2026-03-25）

### 重大重构：面向元 Skill，用户只需提供 SOP.md

**架构变更：**
- Phase 0 改为 Agent LLM 执行：直接读原始 SOP.md → 理解解析 → 自查 → 持久化 sop-graph.json，不再依赖手写 JSON 或专用脚本
- 生产拆分为两步：`generate_prompts.py`（纯拼装，不调 API）→ `produce_batch.py`（只调 API），用户有检查窗口
- Persona 动态读取：`generate_prompts.py` 真正读取并解析 `persona.md`，不再硬编码

**新增文件：**
- `scripts/generate_prompts.py`：Phase 1 第一步，Prompt 拼装 + 路径/Persona 过滤

**删除文件：**
- `scripts/inject_examples.py`：功能并入 Phase 0 Agent 流程，不再需要
- `scripts/llm_client.py`：pipeline_check.py 不再调 LLM，无调用方，删除
- `references/sop-parsed.md`：被 sop-graph.json 替代
- `references/sop-graph.json`：改为按 sop-id 命名（`sop-graph-{sop_id}.json`），不预置发票版
- `data/paths/invoice_paths.jsonl`：生成产物，不再预置

**修改文件：**
- `scripts/produce_batch.py`：重写，职责收窄为"读 prompts_todo.jsonl → 调 API → 写 checkpoint"；去掉硬编码 PERSONA_DEFS 和 PATHS_FILE 死变量
- `scripts/pipeline_check.py`：重写，改为格式层质检（不再调 LLM）；VALID_ACTIONS 从 sop-graph.json 动态加载；去掉硬编码 KNOWN_PATHS 和 ACT-XXX 格式；修复"商家/商户"错误
- `references/sop-example.md`：替换为发票场景真实 SOP（原始 MD 格式），作为用户参考示例
- `references/checkpoint.md`：更新为新的两步生产流程，移除 split.py/validate.py 死引用
- `SKILL.md`：全面重写，Phase 0~2 完整流程，信号解耦设计说明

**信号设计变更（v9.0 核心）：**
- 判断信号（`signal_fields`）与参考信号（`slot_fields`）解耦，分别记录
- 填槽时若字段同时在 signal_fields，取路径约束值，不 mock

---

## v8.8（2026-03-25）

- sop-graph.json 完成发票场景全量补全（25路径，39动作）
- 修复 enumerate_paths.py 的去重逻辑

## v8.x（2026-03-24）

- SubAgent 生产方式迁移至直接 API 调用（urllib.request），性能更稳
- produce_batch.py 支持 workers=8 并发，加入断点续跑逻辑
- run 目录结构规范化

## v7.5（2026-03-21）

- Skill 重命名：invoice-data-producer → cs-data-factory（通用元 Skill）
- Phase 0 加入 inject_examples.py 注入流程
- 版本管理规则确立

## v6.2（2026-03-19）

- 首批 5 条生产结果验证通过
- 逐轮预测格式对齐算法侧要求
- 话术关联动作/话术共识动作字段确立