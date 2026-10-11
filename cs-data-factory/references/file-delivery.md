# 文件发送规范

> 本文档规定 cs-data-factory 各阶段产出文件的统一发送方式，适用于跨平台环境（Catclaw / CatPaw / CatDesk）。  
> **凡需向用户发送文件的步骤，Agent 必须按本文档执行，禁止只打印路径而不告知发送方式。**

---

## 一、环境判断

| 环境 | 特征 | 发送方式 |
|---|---|---|
| 平台 **Catclaw** | Agent 具备 `message(action=send)` 能力 | 调用 `message(action=send, media="<文件绝对路径>")` 通过大象 channel 以附件形式发送 |
| 平台 **CatPaw / CatDesk** | 本地桌面 IDE，无大象发送能力 | 向用户提供文件的**绝对路径**，并告知用浏览器/Excel/Markdown 阅读器打开 |

> **跨平台原则**：Agent 无法确定当前运行环境时，**同时输出两种方式**，让用户选择适合自己的方式打开。

---

## 二、各文件类型的发送模板

### 2-A. HTML 文件（浏览器打开）

适用文件：
- Phase 0 审查报告：`data/tmp/{sop_id}_phase0_preview.html`

**Catclaw 环境**：
```
message(action=send, media="<HTML 文件绝对路径>")
```
发送后告知用户：「Phase 0 审查报告已发送，请在大象中下载后用浏览器打开。」

**CatPaw / CatDesk 环境**：
```
✅ Phase 0 审查报告已生成
   绝对路径：<HTML 文件绝对路径>

请用浏览器打开上方路径的 HTML 文件进行审查，或在文件管理器中双击打开。
```

**跨平台兜底（环境不明时）**：
```
✅ Phase 0 审查报告已生成
   绝对路径：<HTML 文件绝对路径>

打开方式：
  • 本地桌面环境（CatPaw / CatDesk）：直接用浏览器打开上方路径
  • 平台 Catclaw：已通过大象 channel 发送附件
```

---

### 2-B. Excel 文件（Excel / WPS 打开）

适用文件：
- Phase 2 生产结果导出：`data/runs/<run_id>/final.xlsx`

**Catclaw 环境**：
```
message(action=send, media="<Excel 文件绝对路径>")
```
发送后告知用户：「final.xlsx 已发送，请在大象中下载后用 Excel 或 WPS 打开。」

**CatPaw / CatDesk 环境**：
```
✅ Excel 已导出
   绝对路径：<Excel 文件绝对路径>

请在文件管理器中打开，或复制路径在 Excel / WPS 中打开。
```

**跨平台兜底（环境不明时）**：
```
✅ Excel 已导出
   绝对路径：<Excel 文件绝对路径>

打开方式：
  • 本地桌面环境（CatPaw / CatDesk）：直接用 Excel / WPS 打开上方路径
  • 平台 Catclaw：已通过大象 channel 发送附件
```

---

### 2-C. Markdown 报告（文本编辑器 / IDE 打开）

适用文件：
- Phase 3 质检报告：`data/judge_runs/<round>/report.md`
- Phase 3 回归对比报告：`judge/regression/results/<timestamp>_<version>_baseline.md` 或 `..._diff.md`

**Catclaw 环境**：
```
message(action=send, media="<Markdown 文件绝对路径>")
```
发送后告知用户：「报告已发送，请在大象中下载后用文本编辑器或 Markdown 阅读器打开。」

**CatPaw / CatDesk 环境**：
```
✅ 报告已生成
   绝对路径：<Markdown 文件绝对路径>

请在 IDE（如 CatPaw）或 Markdown 阅读器中打开查阅。
```

**跨平台兜底（环境不明时）**：
```
✅ 报告已生成
   绝对路径：<Markdown 文件绝对路径>

打开方式：
  • 本地桌面环境（CatPaw / CatDesk）：直接在 IDE 或 Markdown 阅读器中打开上方路径
  • 平台 Catclaw：已通过大象 channel 发送附件
```

---

## 三、各步骤文件发送速查表

| 阶段 | 步骤 | 文件 | 类型 | 适用模板 |
|---|---|---|---|---|
| Phase 0 | Step 7-1 生成审查报告 | `data/tmp/{sop_id}_phase0_preview.html` | HTML | 2-A |
| Phase 0 | Step 7-3 修正循环后刷新 | `data/tmp/{sop_id}_phase0_preview.html` | HTML | 2-A（CatPaw 告知刷新即可，路径不变） |
| Phase 2 | Step 7 导出 Excel | `data/runs/<run_id>/final.xlsx` | Excel | 2-B |
| Phase 3 | 步骤 4 生成质检报告 | `data/judge_runs/<round>/report.md` | Markdown | 2-C |
| Phase 3 | 回归测试（流程 C Step 6） | `judge/regression/results/<timestamp>_*.md` | Markdown | 2-C |

---

## 四、注意事项

1. **路径必须完整**：发送路径时必须使用**绝对路径**，不得使用相对路径（用户从其他目录运行时相对路径无效）。
2. **文件必须存在**：发送前确认文件确实已生成（脚本执行成功），不可在文件生成失败时仍尝试发送。
3. **修正循环中的 HTML 重发**：Phase 0 每次修正后重新生成 HTML，Catclaw 环境须重新 `message(action=send)`；CatPaw/CatDesk 环境只需告知用户「刷新浏览器」即可（路径不变，无需重发）。
4. **不要自动打开文件**：Agent 不主动调用系统命令打开文件（`start`/`open`/`xdg-open`），只提供路径，由用户自行决定。
