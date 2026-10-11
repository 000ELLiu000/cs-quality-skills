#!/usr/bin/env python3
"""
preview_phase0.py
-----------------
Phase 0 产物可视化 + 内嵌 verify。

读取 Phase 0 三个产物，执行内置格式校验，生成自包含 HTML 报告供人工审查：
  - Tab 1：SOP 流程图（dagre-d3 交互式有向图，含节点详情 tooltip）
  - Tab 2：信号规则（判断信号 / 参考信号 / 补集信号分组展示）
  - Tab 3：Persona 摘要（从 persona.md 解析，列出各 Persona 特征和示例）

HTML 报告顶部展示 verify 结果（通过 / 错误 / 警告），一眼判断产物健康度。

用法：
  python scripts/preview_phase0.py --sop-id daocandaopiao
  python scripts/preview_phase0.py --sop-id daocandaopiao --out data/tmp/daocandaopiao_review.html
  python scripts/preview_phase0.py --sop-id daocandaopiao --persona-file data/sop/daocandaopiao_persona.md

产物：
  默认输出到 data/tmp/{sop_id}_phase0_preview.html
  如果 signal_rules.json 不存在，信号 Tab 会显示"未生成"提示，不会报错。
  如果 persona_file 不存在，Persona Tab 显示默认 references/persona.md。
"""

import json
import re
import sys
import argparse
import datetime
from pathlib import Path

# ─── 路径 ────────────────────────────────────────────────────────────────────
SKILL_ROOT = Path(__file__).parent.parent
DATA_DIR = SKILL_ROOT / "data"
SOP_GRAPH_DIR = DATA_DIR / "sop"
TMP_DIR = DATA_DIR / "tmp"
REFERENCES_DIR = SKILL_ROOT / "references"
DEFAULT_PERSONA_FILE = REFERENCES_DIR / "persona.md"

# ─── 导入 verify 模块（复用 harness_verify，消除重复实现）────────────────────
sys.path.insert(0, str(SKILL_ROOT))
from harness_verify.verify_graph import verify as verify_graph, verify_signal_rules
from lib import load_signal_rules_file, normalize_signal_rules


# ─── Persona 解析 ─────────────────────────────────────────────────────────────

def parse_personas(persona_file: Path) -> list[dict]:
    """
    解析 persona.md，返回 [{id, name, patience, coop, lang, behavior, examples_short, dialog_blocks}]
    """
    text = persona_file.read_text(encoding="utf-8")
    persona_section_start = text.find("### P")
    if persona_section_start == -1:
        return []
    persona_content = text[persona_section_start:]
    stop_match = re.search(r'\n## [^#]', persona_content)
    if stop_match:
        persona_content = persona_content[:stop_match.start()]

    personas = []
    blocks = re.split(r'\n(?=### P\d)', persona_content)
    for block in blocks:
        m = re.match(r'### (P\d+)：(.+)', block.strip())
        if not m:
            continue
        pid = m.group(1)
        pname = m.group(2).strip()
        patience = re.search(r'\*\*耐心\*\*：(.+)', block)
        coop = re.search(r'\*\*合作度\*\*：(.+)', block)
        lang = re.search(r'\*\*语言特征\*\*：(.+)', block)
        behavior = re.search(r'\*\*典型行为\*\*：(.+)', block)
        examples = re.findall(r'「(.+?)」', block)

        # 对话块（``` ... ```）
        dialog_blocks = re.findall(r'```\n([\s\S]+?)\n```', block)
        dialog_previews = []
        for blk in dialog_blocks[:2]:  # 最多展示2段
            lines = [l.strip() for l in blk.strip().splitlines() if l.strip()][:10]
            dialog_previews.append(lines)

        personas.append({
            "id": pid,
            "name": pname,
            "patience": patience.group(1).strip() if patience else "",
            "coop": coop.group(1).strip() if coop else "",
            "lang": lang.group(1).strip() if lang else "",
            "behavior": behavior.group(1).strip() if behavior else "",
            "examples_short": examples[:4],
            "dialog_blocks": dialog_previews,
        })
    return personas


# ─── HTML 生成 ────────────────────────────────────────────────────────────────

def js_str(s: str) -> str:
    """Python str → JS 安全字符串（JSON encode）"""
    return json.dumps(s, ensure_ascii=False)


def build_html(
    graph: dict,
    signal_rules: dict | None,
    personas: list[dict],
    verify_issues: list[dict],
    persona_file_name: str,
    generated_at: str,
) -> str:
    sop_id = graph.get("sop_id", "unknown")
    scene = graph.get("scene", "")
    node_count = len(graph.get("graph", {}))
    action_count = len(graph.get("actions", {}))
    signal_count = len(graph.get("signal_fields", []))
    slot_count = len(graph.get("slot_fields", []))

    errors = [i for i in verify_issues if i["level"] == "error"]
    warnings = [i for i in verify_issues if i["level"] == "warning"]

    # ── verify 徽章 ──
    if errors:
        verify_badge = f'<span class="badge badge-error">❌ {len(errors)} 个错误</span>'
        if warnings:
            verify_badge += f' <span class="badge badge-warn">⚠️ {len(warnings)} 个警告</span>'
    elif warnings:
        verify_badge = f'<span class="badge badge-warn">⚠️ {len(warnings)} 个警告</span> <span class="badge badge-ok">结构通过</span>'
    else:
        verify_badge = '<span class="badge badge-ok">✅ 校验通过</span>'

    # ── verify 详情列表 HTML ──
    verify_detail_html = ""
    if verify_issues:
        verify_detail_html = '<div class="verify-detail">'
        for issue in verify_issues:
            cls = "issue-error" if issue["level"] == "error" else "issue-warn"
            icon = "❌" if issue["level"] == "error" else "⚠️"
            verify_detail_html += f'<div class="{cls}">{icon} {_esc(issue["msg"])}</div>'
        verify_detail_html += "</div>"

    # ── 信号规则 Tab HTML ──
    signal_tab_html = _build_signal_tab(graph, signal_rules)

    # ── Persona Tab HTML ──
    persona_tab_html = _build_persona_tab(personas, persona_file_name)

    # ── graph JSON for JS ──
    graph_json = json.dumps(graph, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Phase 0 审查报告 — {_esc(sop_id)}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f5f7fa; color: #1a1a1a; }}

/* Header */
.header {{ position: fixed; top: 0; left: 0; right: 0; z-index: 200; background: #fff; border-bottom: 1px solid #e8e8e8; padding: 10px 20px; display: flex; align-items: center; gap: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); flex-wrap: wrap; }}
.logo-icon {{ font-size: 17px; }}
.logo-text {{ font-size: 14px; font-weight: 700; background: linear-gradient(135deg, #1890ff, #722ed1); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
.header-divider {{ width: 1px; height: 18px; background: #e0e0e0; }}
.header h1 {{ font-size: 14px; font-weight: 600; color: #1a1a1a; }}
.badge {{ font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }}
.badge-ok {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
.badge-warn {{ background: #fffbe6; color: #faad14; border: 1px solid #ffe58f; }}
.badge-error {{ background: #fff1f0; color: #ff4d4f; border: 1px solid #ffa39e; }}
.badge-blue {{ background: #e6f7ff; color: #1890ff; border: 1px solid #91d5ff; }}
.header-meta {{ margin-left: auto; font-size: 11px; color: #999; }}

/* Tabs */
.tabs-wrap {{ position: fixed; top: 52px; left: 0; right: 0; z-index: 199; background: #fff; border-bottom: 1px solid #e8e8e8; padding: 0 20px; display: flex; gap: 0; }}
.tab-btn {{ padding: 10px 20px; font-size: 13px; color: #666; cursor: pointer; border: none; background: none; border-bottom: 2px solid transparent; margin-bottom: -1px; transition: all 0.15s; white-space: nowrap; }}
.tab-btn:hover {{ color: #1890ff; }}
.tab-btn.active {{ color: #1890ff; border-bottom-color: #1890ff; font-weight: 600; }}

/* Tab panels */
.tab-panel {{ display: none; position: fixed; top: 92px; left: 0; right: 0; bottom: 0; overflow: hidden; }}
.tab-panel.active {{ display: block; }}
.tab-panel-scroll {{ height: 100%; overflow-y: auto; padding: 20px; }}

/* Verify detail */
.verify-detail {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; }}
.issue-error {{ font-size: 12px; color: #ff4d4f; padding: 3px 0; }}
.issue-warn {{ font-size: 12px; color: #faad14; padding: 3px 0; }}

/* Card */
.card {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 16px 20px; margin-bottom: 14px; }}
.card-title {{ font-size: 13px; font-weight: 600; color: #333; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }}
.card-title .cnt {{ font-size: 11px; font-weight: 400; color: #999; }}

/* Signal table */
.signal-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.signal-table th {{ background: #fafafa; padding: 7px 10px; text-align: left; font-weight: 600; color: #555; border-bottom: 1px solid #f0f0f0; }}
.signal-table td {{ padding: 7px 10px; border-bottom: 1px solid #f7f7f7; vertical-align: top; line-height: 1.5; }}
.signal-table tr:last-child td {{ border-bottom: none; }}
.tag {{ display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px; margin: 1px; }}
.tag-enum {{ background: #f0f5ff; color: #2f54eb; }}
.tag-mock {{ background: #fff7e6; color: #fa8c16; }}
.tag-static {{ background: #f6ffed; color: #52c41a; }}
.tag-dynamic {{ background: #fff0f6; color: #eb2f96; }}
.tag-j {{ background: #e6f7ff; color: #1890ff; }}
.tag-s {{ background: #f9f0ff; color: #722ed1; }}
.tag-o {{ background: #f5f5f5; color: #8c8c8c; }}
.enum-list {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }}
.enum-val {{ display: inline-block; background: #f5f5f5; border: 1px solid #e8e8e8; border-radius: 3px; padding: 1px 6px; font-size: 11px; color: #555; }}
.mock-rule {{ color: #595959; font-size: 12px; }}
.empty-tip {{ color: #bfbfbf; font-size: 12px; font-style: italic; }}

/* Stats row */
.stats-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }}
.stat-item {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 12px 18px; flex: 1; min-width: 120px; }}
.stat-num {{ font-size: 22px; font-weight: 700; color: #1890ff; }}
.stat-label {{ font-size: 11px; color: #888; margin-top: 2px; }}

/* Persona */
.persona-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }}
.persona-card {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 14px 16px; }}
.persona-id {{ display: inline-block; font-size: 11px; font-weight: 700; background: #e6f7ff; color: #1890ff; padding: 2px 8px; border-radius: 10px; margin-bottom: 6px; }}
.persona-name {{ font-size: 14px; font-weight: 600; color: #1a1a1a; margin-bottom: 8px; }}
.persona-attr {{ font-size: 12px; color: #555; margin: 3px 0; }}
.persona-attr span {{ color: #999; }}
.persona-example-badge {{ display: inline-block; background: #f5f5f5; border-radius: 3px; padding: 1px 7px; font-size: 11px; color: #595959; margin: 2px 2px 2px 0; }}
.persona-dialog {{ margin-top: 8px; background: #fafafa; border-left: 3px solid #1890ff; padding: 8px 10px; border-radius: 2px; font-size: 11px; line-height: 1.7; color: #555; max-height: 160px; overflow-y: auto; }}
.persona-dialog .d-user {{ color: #1890ff; }}
.persona-dialog .d-agent {{ color: #52c41a; }}
.persona-toggle {{ font-size: 11px; color: #1890ff; cursor: pointer; margin-top: 4px; user-select: none; }}
.persona-dialog-wrap {{ display: none; }}
.persona-dialog-wrap.open {{ display: block; }}

/* Graph tab */
#graph-canvas {{ width: 100%; height: 100%; }}
#graph-canvas svg {{ width: 100%; height: 100%; }}
.node rect, .node polygon, .node circle, .node ellipse {{ stroke-width: 1.5px; cursor: pointer; transition: filter 0.15s; }}
.node:hover rect, .node:hover polygon, .node:hover ellipse {{ filter: brightness(0.95) drop-shadow(0 2px 4px rgba(0,0,0,0.15)); }}
.node text {{ font-size: 12px; font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }}
.edgePath path {{ stroke-width: 1.5px; fill: none; }}
.edgeLabel text {{ font-size: 11px; fill: #666; font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }}

/* Tooltip */
.graph-tooltip {{ position: fixed; max-width: 420px; background: #fff; border: 1px solid #d9d9d9; border-radius: 8px; padding: 12px 16px; font-size: 13px; line-height: 1.6; color: #333; box-shadow: 0 4px 16px rgba(0,0,0,0.12); z-index: 300; pointer-events: none; display: none; }}
.tt-title {{ font-weight: 600; margin-bottom: 5px; color: #1a1a1a; }}
.tt-tag {{ display: inline-block; font-size: 11px; background: #f0f5ff; color: #2f54eb; padding: 1px 6px; border-radius: 3px; margin: 2px 2px 2px 0; }}
.tt-speech {{ margin-top: 8px; padding: 8px 10px; background: #fafafa; border-left: 3px solid #1890ff; border-radius: 2px; font-size: 12px; color: #555; }}

/* Legend */
.graph-legend {{ position: absolute; bottom: 16px; left: 16px; background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 10px 14px; font-size: 12px; color: #666; box-shadow: 0 2px 8px rgba(0,0,0,0.08); z-index: 100; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; margin: 3px 0; }}
.legend-dot {{ width: 18px; height: 14px; border-radius: 3px; display: inline-block; }}
.graph-controls {{ position: absolute; top: 8px; right: 12px; display: flex; gap: 6px; z-index: 100; }}
.graph-controls button {{ padding: 4px 12px; border: 1px solid #d9d9d9; border-radius: 4px; background: #fff; cursor: pointer; font-size: 12px; color: #555; }}
.graph-controls button:hover {{ border-color: #1890ff; color: #1890ff; }}
</style>
</head>
<body>

<div class="header">
  <span class="logo-icon">⚙️</span>
  <span class="logo-text">cs-data-factory</span>
  <div class="header-divider"></div>
  <h1>Phase 0 审查报告</h1>
  <span class="badge badge-blue">{_esc(sop_id)}</span>
  {f'<span class="badge badge-blue" style="background:#f9f0ff;color:#722ed1;border-color:#d3adf7;">{_esc(scene)}</span>' if scene else ''}
  {verify_badge}
  <span class="header-meta">生成于 {_esc(generated_at)}</span>
</div>

<div class="tabs-wrap">
  <button class="tab-btn active" onclick="switchTab('graph')">📊 SOP 流程图</button>
  <button class="tab-btn" onclick="switchTab('signals')">🔢 信号规则</button>
  <button class="tab-btn" onclick="switchTab('persona')">👤 Persona</button>
</div>

<!-- ── Tab: Graph ── -->
<div class="tab-panel active" id="tab-graph">
  <div style="position:relative;height:100%;">
    <div id="graph-canvas"><svg id="graph-svg"></svg></div>
    <div class="graph-controls">
      <button onclick="zoomFit()">适应画布</button>
      <button onclick="zoomIn()">放大 +</button>
      <button onclick="zoomOut()">缩小 −</button>
    </div>
    <div class="graph-legend">
      <div style="font-weight:600;margin-bottom:5px;">图例</div>
      <div class="legend-item"><div class="legend-dot" style="background:#e6f7ff;border:1.5px solid #69c0ff;clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%);"></div> 信号判断</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f6ffed;border:1.5px solid #95de64;"></div> 客服动作</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f0e6ff;border:1.5px solid #b37feb;"></div> 用户反应</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f0f0f0;border:1.5px solid #bfbfbf;border-radius:50%;width:14px;height:14px;"></div> 终端</div>
    </div>
  </div>
</div>
<div class="graph-tooltip" id="graph-tooltip"></div>

<!-- ── Tab: Signals ── -->
<div class="tab-panel" id="tab-signals">
  <div class="tab-panel-scroll">
    {verify_detail_html}
    {signal_tab_html}
  </div>
</div>

<!-- ── Tab: Persona ── -->
<div class="tab-panel" id="tab-persona">
  <div class="tab-panel-scroll">
    {persona_tab_html}
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/d3@5.16.0/dist/d3.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre-d3@0.6.4/dist/dagre-d3.min.js"></script>
<script>
// ── Tab switching ──
function switchTab(name) {{
  document.querySelectorAll('.tab-btn').forEach((b,i) => {{
    const names = ['graph','signals','persona'];
    b.classList.toggle('active', names[i] === name);
  }});
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'graph') setTimeout(zoomFit, 80);
}}

// ── Persona dialog toggle ──
function toggleDialog(id) {{
  const el = document.getElementById(id);
  el.classList.toggle('open');
  const btn = el.previousElementSibling;
  btn.textContent = el.classList.contains('open') ? '▲ 收起示例' : '▼ 展开对话示例';
}}

// ── Graph data ──
const sopData = {graph_json};

// ── Build dagre graph ──
const g = new dagreD3.graphlib.Graph().setGraph({{
  rankdir: 'TB', ranksep: 55, nodesep: 36, edgesep: 18, marginx: 36, marginy: 36
}});

const graphNodes = sopData.graph;
const actionsMap = sopData.actions || {{}};

function mergeEdges(nodeId, node) {{
  if (!node.branches) return [];
  const tm = {{}};
  for (const [label, target] of Object.entries(node.branches)) {{
    if (!tm[target]) tm[target] = [];
    tm[target].push(label);
  }}
  return Object.entries(tm).map(([target, labels]) => ({{ target, label: labels.join(' / ') }}));
}}

function trunc(s, n) {{ return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); }}

for (const [id, node] of Object.entries(graphNodes)) {{
  let label, style, shape;
  if (node.type === 'signal_check') {{
    label = '◇ ' + node.signal;
    style = 'fill: #e6f7ff; stroke: #69c0ff;';
    shape = 'diamond';
  }} else if (node.type === 'agent_action') {{
    const act = actionsMap[node.action] || {{}};
    label = node.action + ': ' + trunc(act.desc || '', 18);
    style = 'fill: #f6ffed; stroke: #95de64;';
    shape = 'rect';
  }} else if (node.type === 'user_reaction') {{
    label = '👤 用户反应';
    style = 'fill: #f0e6ff; stroke: #b37feb;';
    shape = 'rect';
  }} else {{
    label = '●';
    style = 'fill: #f0f0f0; stroke: #bfbfbf;';
    shape = 'circle';
  }}
  const opts = {{ label, style, shape, rx: 5, ry: 5, paddingLeft: 12, paddingRight: 12, paddingTop: 8, paddingBottom: 8 }};
  if (node.type === 'terminal') {{
    Object.assign(opts, {{ width: 22, height: 22, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 }});
  }}
  g.setNode(id, opts);
}}

for (const [id, node] of Object.entries(graphNodes)) {{
  if (node.branches) {{
    for (const {{ target, label }} of mergeEdges(id, node)) {{
      g.setEdge(id, target, {{ label: trunc(label, 22), style: 'stroke:#8c8c8c;stroke-width:1.5px;', arrowheadStyle: 'fill:#8c8c8c;', curve: d3.curveBasis }});
    }}
  }}
  if (node.next) {{
    g.setEdge(id, node.next, {{ label: '', style: 'stroke:#bfbfbf;stroke-width:1.5px;', arrowheadStyle: 'fill:#bfbfbf;', curve: d3.curveBasis }});
  }}
}}

const svg = d3.select('#graph-svg');
const inner = svg.append('g');
const render = new dagreD3.render();
render(inner, g);

const zoom = d3.zoom().scaleExtent([0.15, 3]).on('zoom', () => {{ inner.attr('transform', d3.event.transform); }});
svg.call(zoom);

function zoomFit() {{
  const panel = document.getElementById('tab-graph');
  if (!panel.classList.contains('active')) return;
  const bounds = inner.node().getBBox();
  const fullWidth = panel.clientWidth;
  const fullHeight = panel.clientHeight;
  const width = bounds.width + 80;
  const height = bounds.height + 80;
  const scale = Math.min(fullWidth / width, fullHeight / height, 1.5);
  const tx = (fullWidth - bounds.width * scale) / 2 - bounds.x * scale;
  const ty = (fullHeight - bounds.height * scale) / 2 - bounds.y * scale;
  svg.transition().duration(480).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}}

function zoomIn() {{ svg.transition().duration(300).call(zoom.scaleBy, 1.3); }}
function zoomOut() {{ svg.transition().duration(300).call(zoom.scaleBy, 0.7); }}
setTimeout(zoomFit, 150);

// ── Tooltip ──
const tooltip = document.getElementById('graph-tooltip');
svg.selectAll('.node').on('mouseover', function(nodeId) {{
  const node = graphNodes[nodeId];
  if (!node || node.type === 'terminal') return;
  let html = '';
  if (node.type === 'signal_check') {{
    html = `<div class="tt-title">◇ 信号判断：${{node.signal}}</div><div style="margin-top:4px;">`;
    for (const [val, target] of Object.entries(node.branches)) {{
      html += `<span class="tt-tag">${{val}} → ${{target}}</span> `;
    }}
    html += '</div>';
  }} else if (node.type === 'agent_action') {{
    const act = actionsMap[node.action] || {{}};
    html = `<div class="tt-title">${{node.action}}：${{act.desc || ''}}</div>`;
    const actionTagVal = act.action_tag || '无';
    const consensusTagVal = act.consensus_tag || '无';
    html += `<div style="margin:4px 0;"><span class="tt-tag">话术关联动作：${{actionTagVal}}</span></div>`;
    html += `<div style="margin:4px 0;"><span class="tt-tag" style="background:#fff0f6;color:#eb2f96;">话术共识动作：${{consensusTagVal}}</span></div>`;
    if (act.speech_examples && act.speech_examples.length > 0) html += `<div class="tt-speech">💬 ${{act.speech_examples[0]}}</div>`;
  }} else if (node.type === 'user_reaction') {{
    html = `<div class="tt-title">👤 用户反应</div><div style="margin-top:4px;">`;
    for (const [val, target] of Object.entries(node.branches)) {{
      html += `<span class="tt-tag">${{val}} → ${{target}}</span> `;
    }}
    html += '</div>';
  }}
  tooltip.innerHTML = html;
  tooltip.style.display = 'block';
}}).on('mousemove', function() {{
  const x = d3.event.clientX + 16, y = d3.event.clientY + 12;
  const ttW = tooltip.offsetWidth, ttH = tooltip.offsetHeight;
  tooltip.style.left = (x + ttW > window.innerWidth ? x - ttW - 32 : x) + 'px';
  tooltip.style.top = (y + ttH > window.innerHeight ? y - ttH - 24 : y) + 'px';
}}).on('mouseout', function() {{ tooltip.style.display = 'none'; }});
</script>
</body>
</html>"""


def _esc(s: str) -> str:
    """HTML 转义"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_signal_tab(graph: dict, signal_rules: dict | None) -> str:
    signal_fields = graph.get("signal_fields", [])
    slot_fields = graph.get("slot_fields", [])

    signal_rules = normalize_signal_rules(signal_rules, graph=graph)
    signals_cfg = signal_rules.get("signals", {}) if signal_rules else {}
    prod = signal_rules.get("_production_signals", {}) if signal_rules else {}
    # 运行时派生 judgment/slot 摘要，供 HTML 直接渲染生产信号清单
    prod_judgment = prod.get("judgment", {}) if prod else {}
    prod_slot = prod.get("slot", {}) if prod else {}

    # Stats
    other_count = prod.get("other_count", 0) if prod else (
        sum(1 for v in signals_cfg.values() if v.get("source") == "other") if signals_cfg else 0
    )

    html = f"""<div class="stats-row">
  <div class="stat-item"><div class="stat-num">{len(signal_fields)}</div><div class="stat-label">判断信号（signal_fields）</div></div>
  <div class="stat-item"><div class="stat-num">{len(slot_fields)}</div><div class="stat-label">参考信号（slot_fields）</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#8c8c8c;">{other_count}</div><div class="stat-label">补集信号（不注入 Prompt，后处理随机拼接）</div></div>
</div>"""

    # ── 判断信号 ──
    html += '<div class="card"><div class="card-title">🔵 判断信号（骨架锁定，决定路径走向）<span class="cnt">— source: judgment</span></div>'
    if not signal_fields:
        html += '<div class="empty-tip">SOP 中无判断信号声明</div>'
    else:
        html += '<table class="signal-table"><thead><tr><th>信号名</th><th>类型</th><th>变化性</th><th>枚举值 / mock 规则</th></tr></thead><tbody>'
        for key in signal_fields:
            # 优先读运行时派生摘要，缺失时回退到 signals 权威表
            cfg = prod_judgment.get(key) or signals_cfg.get(key, {})
            sig_type = cfg.get("type", "—")
            variability = cfg.get("variability", "—")
            enum_values = cfg.get("enum_values", [])
            mock_rule = cfg.get("mock_rule", "")
            type_tag = f'<span class="tag tag-enum">enum</span>' if sig_type == "enum" else f'<span class="tag tag-mock">mock</span>' if sig_type == "mock" else "—"
            var_tag = f'<span class="tag tag-dynamic">dynamic</span>' if variability == "dynamic" else f'<span class="tag tag-static">static</span>' if variability == "static" else "—"
            if enum_values and mock_rule:
                # 同时有枚举值（路径骨架锁定）和 mock_rule（通话记录生成规则）时，两者都显示
                val_html = (
                    '<div class="enum-list">' + "".join(f'<span class="enum-val">{_esc(v)}</span>' for v in enum_values) + "</div>"
                    + f'<div style="margin-top:6px;padding-top:6px;border-top:1px dashed #f0f0f0;"><span style="font-size:11px;color:#999;">mock_rule：</span>'
                    + f'<span class="mock-rule">{_esc(mock_rule)}</span></div>'
                )
            elif enum_values:
                val_html = '<div class="enum-list">' + "".join(f'<span class="enum-val">{_esc(v)}</span>' for v in enum_values) + "</div>"
            elif mock_rule:
                val_html = f'<span class="mock-rule">{_esc(mock_rule)}</span>'
            elif not signals_cfg:
                val_html = '<span class="empty-tip">信号规则未生成</span>'
            else:
                val_html = '<span class="empty-tip">枚举值待填充（骨架路径枚举时会锁定）</span>'
            html += f'<tr><td><b>{_esc(key)}</b></td><td>{type_tag}</td><td>{var_tag}</td><td>{val_html}</td></tr>'
        html += "</tbody></table>"
    html += "</div>"

    # ── 参考信号 ──
    html += '<div class="card"><div class="card-title">🟣 参考信号（需要 mock，注入话术）<span class="cnt">— source: slot</span></div>'
    if not slot_fields:
        html += '<div class="empty-tip">SOP 中无参考信号（slot_fields）声明</div>'
    else:
        html += '<table class="signal-table"><thead><tr><th>信号名</th><th>类型</th><th>变化性</th><th>mock 规则 / 枚举值</th></tr></thead><tbody>'
        for key in slot_fields:
            # 优先读运行时派生摘要，缺失时回退到 signals 权威表
            cfg = prod_slot.get(key) or signals_cfg.get(key, {})
            sig_type = cfg.get("type", "—")
            variability = cfg.get("variability", "—")
            enum_values = cfg.get("enum_values", [])
            mock_rule = cfg.get("mock_rule", "")
            type_tag = f'<span class="tag tag-enum">enum</span>' if sig_type == "enum" else f'<span class="tag tag-mock">mock</span>' if sig_type == "mock" else "—"
            var_tag = f'<span class="tag tag-dynamic">dynamic</span>' if variability == "dynamic" else f'<span class="tag tag-static">static</span>' if variability == "static" else "—"
            if sig_type == "enum" and enum_values:
                val_html = '<div class="enum-list">' + "".join(f'<span class="enum-val">{_esc(v)}</span>' for v in enum_values) + "</div>"
            elif mock_rule:
                val_html = f'<span class="mock-rule">{_esc(mock_rule)}</span>'
            elif not signals_cfg and not prod_slot:
                val_html = '<span class="empty-tip">信号规则未生成（执行 analyze_signals.py 后可见）</span>'
            else:
                val_html = '<span class="empty-tip" style="color:#ff4d4f;">⚠ mock_rule 为空，LLM 将按常识生成</span>'
            html += f'<tr><td><b>{_esc(key)}</b></td><td>{type_tag}</td><td>{var_tag}</td><td>{val_html}</td></tr>'
        html += "</tbody></table>"
    html += "</div>"

    # ── 补集信号 ──
    if signals_cfg:
        production_keys = set(signal_fields) | set(slot_fields) | set(prod_judgment.keys()) | set(prod_slot.keys())
        other_signals = {
            k: v for k, v in signals_cfg.items()
            if v.get("source") == "other" and k not in production_keys
        }
        if other_signals:
            html += f'<div class="card"><div class="card-title">⚪ 补集信号（不注入 Prompt；后处理阶段会随机拼接，用于构建完整信号）<span class="cnt">— 共 {len(other_signals)} 个</span></div>'
            html += '<table class="signal-table"><thead><tr><th>信号名</th><th>类型</th><th>变化性</th><th>示例值</th></tr></thead><tbody>'
            for key, cfg in other_signals.items():
                sig_type = cfg.get("type", "—")
                variability = cfg.get("variability", "—")
                examples = cfg.get("examples", []) or cfg.get("enum_values", [])
                type_tag = f'<span class="tag tag-enum">enum</span>' if sig_type == "enum" else f'<span class="tag tag-mock">mock</span>' if sig_type == "mock" else "—"
                var_tag = f'<span class="tag tag-dynamic">dynamic</span>' if variability == "dynamic" else f'<span class="tag tag-static">static</span>' if variability == "static" else "—"
                ex_html = '<div class="enum-list">' + "".join(f'<span class="enum-val">{_esc(str(v))}</span>' for v in examples[:5]) + "</div>" if examples else '<span class="empty-tip">无</span>'
                html += f'<tr><td style="color:#8c8c8c;">{_esc(key)}</td><td>{type_tag}</td><td>{var_tag}</td><td>{ex_html}</td></tr>'
            html += "</tbody></table></div>"

    if signal_rules is None:
        html += '<div class="card" style="border-color:#ffe58f;background:#fffbe6;"><div class="card-title" style="color:#faad14;">⚠️ 信号规则文件未生成</div><div style="font-size:12px;color:#595959;">请执行：<code>python scripts/analyze_signals.py --sop-id &lt;sop_id&gt;</code><br>生成草稿 → LLM 评审 → 人工确认后入库，再重新生成本报告。</div></div>'

    return html


def _build_persona_tab(personas: list[dict], persona_file_name: str) -> str:
    if not personas:
        return '<div class="card"><div class="card-title">⚠️ Persona 解析失败</div><div class="empty-tip">请检查 persona.md 格式（需含 ### P1：... 块）</div></div>'

    html = f'<div style="font-size:12px;color:#888;margin-bottom:12px;">来源文件：{_esc(persona_file_name)} &nbsp;·&nbsp; 共 {len(personas)} 类 Persona</div>'
    html += '<div class="persona-grid">'
    for i, p in enumerate(personas):
        did = f"persona-dialog-{i}"
        dialog_html = ""
        for blk in p["dialog_blocks"]:
            dialog_html += '<div>'
            for line in blk:
                if line.startswith("用户:") or line.startswith("用户："):
                    dialog_html += f'<div class="d-user">{_esc(line)}</div>'
                elif line.startswith("客服:") or line.startswith("客服："):
                    dialog_html += f'<div class="d-agent">{_esc(line)}</div>'
                else:
                    dialog_html += f'<div>{_esc(line)}</div>'
            dialog_html += '</div><hr style="border:none;border-top:1px solid #f0f0f0;margin:6px 0;">'

        ex_badges = "".join(f'<span class="persona-example-badge">「{_esc(e)}」</span>' for e in p["examples_short"])

        has_dialog = bool(p["dialog_blocks"])
        toggle_btn = f'<div class="persona-toggle" onclick="toggleDialog(\'{did}\')">▼ 展开对话示例</div>' if has_dialog else ''
        dialog_wrap = f'<div class="persona-dialog-wrap" id="{did}"><div class="persona-dialog">{dialog_html}</div></div>' if has_dialog else ''

        html += f"""<div class="persona-card">
  <div class="persona-id">{_esc(p["id"])}</div>
  <div class="persona-name">{_esc(p["name"])}</div>
  <div class="persona-attr"><span>耐心：</span>{_esc(p["patience"])}</div>
  <div class="persona-attr"><span>合作度：</span>{_esc(p["coop"])}</div>
  <div class="persona-attr"><span>语言特征：</span>{_esc(p["lang"])}</div>
  <div class="persona-attr"><span>典型行为：</span>{_esc(p["behavior"])}</div>
  <div style="margin-top:8px;">{ex_badges}</div>
  {toggle_btn}
  {dialog_wrap}
</div>"""

    html += "</div>"
    return html


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="cs-data-factory Phase 0 产物可视化 + 校验")
    parser.add_argument("--sop-id", required=True, help="SOP 标识（如 daocandaopiao）")
    parser.add_argument("--out", default="", help="输出 HTML 路径（默认：data/tmp/{sop_id}_phase0_preview.html）")
    parser.add_argument("--persona-file", default="", help="Persona 文件路径（默认：data/sop/{sop_id}_persona.md，不存在则用 references/persona.md）")
    args = parser.parse_args()

    sop_id = args.sop_id

    # ── 定位文件 ──
    graph_path = SOP_GRAPH_DIR / f"{sop_id}_graph.json"
    # 优先读草稿（Phase 0 流程中草稿先于正式文件存在）；草稿不存在时回退到已入库正式文件
    _draft_path = TMP_DIR / f"{sop_id}_signal_rules_draft.json"
    _published_path = SOP_GRAPH_DIR / f"{sop_id}_signal_rules.json"
    signal_rules_path = _draft_path if _draft_path.exists() else _published_path

    if args.persona_file:
        persona_path = SKILL_ROOT / args.persona_file if not Path(args.persona_file).is_absolute() else Path(args.persona_file)
    else:
        persona_path = SOP_GRAPH_DIR / f"{sop_id}_persona.md"
        if not persona_path.exists():
            persona_path = DEFAULT_PERSONA_FILE

    out_path = Path(args.out) if args.out else TMP_DIR / f"{sop_id}_phase0_preview.html"
    if not out_path.is_absolute():
        out_path = SKILL_ROOT / out_path

    # ── 加载 graph ──
    if not graph_path.exists():
        print(f"❌ graph.json 不存在：{graph_path}", file=sys.stderr)
        print(f"   请先完成 Phase 0-A SOP 解析", file=sys.stderr)
        sys.exit(1)
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)
    print(f"📂 graph：{graph_path.name}  节点={len(graph.get('graph',{}))}  动作={len(graph.get('actions',{}))}")

    # ── 加载 signal_rules（可选）──
    if signal_rules_path.exists():
        signal_rules = load_signal_rules_file(signal_rules_path, graph=graph)
        _src_label = "草稿" if signal_rules_path == _draft_path else "已入库"
        print(f"📂 signal_rules（{_src_label}）：{signal_rules_path.name}  信号数={len(signal_rules.get('signals',{}))}")
    else:
        signal_rules = None
        print(f"⚠️  signal_rules 未生成（可选），请先完成 SubAgent-C 信号分析：{signal_rules_path.name}")

    # ── 加载 persona ──
    if persona_path.exists():
        personas = parse_personas(persona_path)
        persona_file_name = persona_path.name
        print(f"📂 persona：{persona_path.name}  Persona 数={len(personas)}")
    else:
        personas = []
        persona_file_name = persona_path.name
        print(f"⚠️  persona 文件不存在：{persona_path}")

    # ── Verify ──
    print(f"\n🔍 执行 verify...")
    issues = verify_graph(graph)
    issues += verify_signal_rules(signal_rules, graph)

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    if errors:
        print(f"   ❌ {len(errors)} 个错误：")
        for e in errors:
            print(f"      {e['msg']}")
    if warnings:
        print(f"   ⚠️  {len(warnings)} 个警告：")
        for w in warnings:
            print(f"      {w['msg']}")
    if not issues:
        print(f"   ✅ 校验通过")

    # ── 生成 HTML ──
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = build_html(graph, signal_rules, personas, issues, persona_file_name, generated_at)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"\n✅ 报告已生成：{out_path}")
    print(f"   用浏览器打开即可审查（三标签页：SOP流程图 / 信号规则 / Persona）")


if __name__ == "__main__":
    main()
