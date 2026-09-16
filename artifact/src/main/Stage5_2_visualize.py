#!/usr/bin/env python3
"""
Stage 5.2: Generate interactive topology visualization HTML from Stage 4 XML.
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_utils import create_run_directory, setup_logger, save_json


def clean_text(value):
    return str(value or "").replace("\\n", "\n").strip()


def parse_xml_topology(xml_path):
    root = ET.parse(xml_path).getroot()
    template_id = root.get("template_id") or Path(xml_path).stem
    ig_level = root.get("ig_level") or Path(xml_path).parent.name

    zones = []
    for zone in root.findall("./Domains/Domain"):
        zones.append({
            "id": zone.get("id") or "",
            "name": clean_text(zone.get("name") or zone.get("id") or ""),
            "type": clean_text(zone.get("type") or ""),
        })

    nodes = []
    for node in root.findall("./Nodes/Node"):
        nodes.append({
            "id": node.get("id") or "",
            "name": clean_text(node.get("name") or node.get("id") or ""),
            "type": clean_text(node.get("type") or "other"),
            "zone": node.get("domain") or "",
            "zone_name": clean_text(node.get("domain_name") or ""),
            "ip": clean_text(node.get("ip") or ""),
        })

    node_ids = {node["id"] for node in nodes}
    node_by_id = {node["id"]: node for node in nodes}
    zone_links = []
    for link in root.findall("./Links/ZoneLinks/Link"):
        src = link.get("source") or link.get("a") or ""
        dst = link.get("target") or link.get("b") or ""
        if src and dst:
            zone_links.append({
                "id": link.get("id") or link.get("link_id") or f"zl{len(zone_links) + 1}",
                "source": src,
                "target": dst,
                "type": clean_text(link.get("type") or link.get("link_type") or ""),
                "label": clean_text(link.get("label") or ""),
            })

    node_links = []
    for link in root.findall("./Links/NodeLinks/Link"):
        src = link.get("source") or link.get("a") or ""
        dst = link.get("target") or link.get("b") or ""
        if src in node_ids and dst in node_ids:
            node_links.append({
                "id": link.get("id") or link.get("link_id") or f"nl{len(node_links) + 1}",
                "source": src,
                "target": dst,
                "type": clean_text(link.get("type") or link.get("link_type") or ""),
                "label": clean_text(link.get("label") or ""),
            })

    policies = []
    for policy in root.findall("./Policies/*"):
        expected = "allow" if policy.tag.lower() == "allow" else "deny"
        src_node = policy.get("src_node") or ""
        dst_node = policy.get("dst_node") or ""
        src_info = node_by_id.get(src_node, {})
        dst_info = node_by_id.get(dst_node, {})
        policies.append({
            "id": policy.get("id") or f"tc{len(policies) + 1}",
            "expected": expected,
            "src_zone": policy.get("src_zone") or "",
            "dst_zone": policy.get("dst_zone") or "",
            "src_node": src_node,
            "dst_node": dst_node,
            "src_name": src_info.get("name", src_node),
            "dst_name": dst_info.get("name", dst_node),
            "src_type": src_info.get("type", ""),
            "dst_type": dst_info.get("type", ""),
            "protocol": clean_text(policy.get("protocol") or ""),
            "port": clean_text(policy.get("port") or ""),
            "reason": clean_text(policy.findtext("Reason") or ""),
            "executable": bool(src_node and dst_node),
        })

    controls = []
    for control in root.findall("./Compliance/Control"):
        controls.append({
            "id": control.get("id") or "",
            "title": clean_text(control.get("title") or ""),
            "status": clean_text(control.get("status") or ""),
            "confidence": clean_text(control.get("confidence") or ""),
            "evidence": clean_text(control.findtext("Evidence") or ""),
            "missing_reason": clean_text(control.findtext("MissingReason") or ""),
            "remediation": clean_text(control.findtext("Remediation") or ""),
        })

    compliance_el = root.find("./Compliance")
    compliance = {
        "ig_level": compliance_el.get("ig_level") if compliance_el is not None else ig_level,
        "all_required_satisfied": compliance_el.get("all_required_satisfied") if compliance_el is not None else "",
        "controls": controls,
    }

    risk_items = derive_risks(nodes, policies, compliance)

    return {
        "xml_file": str(xml_path),
        "schema_version": root.get("schema_version") or "",
        "template_id": template_id,
        "title": clean_text(root.get("title") or template_id),
        "scenario": clean_text(root.get("scenario") or ""),
        "ig_level": ig_level,
        "description": clean_text(root.findtext("Description") or ""),
        "summary": clean_text(root.findtext("Summary") or ""),
        "zones": zones,
        "nodes": nodes,
        "zone_links": zone_links,
        "node_links": node_links,
        "policies": policies,
        "compliance": compliance,
        "risks": risk_items,
        "metrics": {
            "zones": len(zones),
            "nodes": len(nodes),
            "zone_links": len(zone_links),
            "node_links": len(node_links),
            "policies": len(policies),
            "controls": len(controls),
        },
    }


def derive_risks(nodes, policies, compliance):
    risks = []
    type_counts = Counter(node["type"] for node in nodes)
    if not type_counts.get("firewall"):
        risks.append({"level": "high", "target": "topology", "desc": "No firewall node is present."})
    if not type_counts.get("ids_ips"):
        risks.append({"level": "medium", "target": "topology", "desc": "No IDS/IPS node is present."})
    if compliance.get("all_required_satisfied") == "false":
        risks.append({"level": "high", "target": "compliance", "desc": "One or more CIS controls are not satisfied."})
    non_exec = sum(1 for policy in policies if not policy.get("executable"))
    if non_exec:
        risks.append({"level": "medium", "target": "policies", "desc": f"{non_exec} policies have no concrete node endpoint."})
    return risks


def to_cytoscape_elements(topo):
    elements = []
    positions = compute_node_positions(topo)
    for zone in topo["zones"]:
        elements.append({
            "data": {
                "id": f"zone:{zone['id']}",
                "label": f"{zone['name']}\n{zone['type']}".strip(),
                "kind": "zone",
                "name": zone["name"],
                "zone_type": zone["type"],
            },
            "classes": "zone",
        })

    zone_ids = {zone["id"] for zone in topo["zones"]}
    for node in topo["nodes"]:
        classes = ["topo-node", normalize_class(node["type"])]
        elements.append({
            "data": {
                "id": f"node:{node['id']}",
                "label": compact_label(node["name"], node["type"]),
                "kind": "node",
                "node_id": node["id"],
                "name": node["name"],
                "node_type": node["type"],
                "zone": node["zone"],
                "zone_name": node["zone_name"],
                "ip": node["ip"],
                "parent": f"zone:{node['zone']}" if node["zone"] in zone_ids else None,
            },
            "position": {
                "x": positions.get(node["id"], (0, 0))[0],
                "y": positions.get(node["id"], (0, 0))[1],
            },
            "classes": " ".join(classes),
        })

    valid_node_ids = {node["id"] for node in topo["nodes"]}
    for link in topo["node_links"]:
        if link["source"] not in valid_node_ids or link["target"] not in valid_node_ids:
            continue
        elements.append({
            "data": {
                "id": f"edge:{link['id']}",
                "source": f"node:{link['source']}",
                "target": f"node:{link['target']}",
                "kind": "node_link",
                "link_id": link["id"],
                "link_type": link["type"],
                "label": link["label"] or link["type"],
            },
            "classes": f"node-link {normalize_class(link['type'])}",
        })

    valid_zone_ids = {zone["id"] for zone in topo["zones"]}
    for link in topo["zone_links"]:
        if link["source"] not in valid_zone_ids or link["target"] not in valid_zone_ids:
            continue
        elements.append({
            "data": {
                "id": f"zoneedge:{link['id']}",
                "source": f"zone:{link['source']}",
                "target": f"zone:{link['target']}",
                "kind": "zone_link_metadata",
                "link_id": link["id"],
                "link_type": link["type"],
                "label": link["label"] or link["type"],
            },
            "classes": "zone-link",
        })
    return elements


def to_overview_elements(topo):
    positions = compute_zone_positions(topo)
    elements = []
    zone_node_counts = Counter(node["zone"] for node in topo["nodes"])
    for zone in topo["zones"]:
        zid = zone["id"]
        elements.append({
            "data": {
                "id": f"ovzone:{zid}",
                "label": f"{zone['name']}\n{zone['type']}\n{zone_node_counts[zid]} nodes",
                "kind": "overview_zone",
                "name": zone["name"],
                "zone_id": zid,
                "zone_type": zone["type"],
                "node_count": zone_node_counts[zid],
            },
            "position": {"x": positions.get(zid, (0, 0))[0], "y": positions.get(zid, (0, 0))[1]},
            "classes": f"overview-zone {normalize_class(zone['type'])}",
        })

    node_zone = {node["id"]: node["zone"] for node in topo["nodes"]}
    grouped = {}
    type_counts = defaultdict(Counter)
    for link in topo["node_links"]:
        src_zone = node_zone.get(link["source"], "")
        dst_zone = node_zone.get(link["target"], "")
        if not src_zone or not dst_zone or src_zone == dst_zone:
            continue
        key = tuple(sorted((src_zone, dst_zone)))
        grouped[key] = grouped.get(key, 0) + 1
        type_counts[key][link["type"] or "other"] += 1

    for idx, ((src_zone, dst_zone), count) in enumerate(sorted(grouped.items()), 1):
        top_type = type_counts[(src_zone, dst_zone)].most_common(1)[0][0]
        elements.append({
            "data": {
                "id": f"ovedge:{idx}",
                "source": f"ovzone:{src_zone}",
                "target": f"ovzone:{dst_zone}",
                "kind": "overview_edge",
                "label": f"{count} links",
                "link_count": count,
                "dominant_type": top_type,
            },
            "classes": f"overview-edge {normalize_class(top_type)}",
        })
    return elements


def compute_zone_positions(topo):
    zones = topo["zones"]
    nodes = topo["nodes"]
    zone_ids = [zone["id"] for zone in zones]
    connectivity = Counter()
    node_zone = {node["id"]: node["zone"] for node in nodes}
    for link in topo["node_links"]:
        src_zone = node_zone.get(link["source"], "")
        dst_zone = node_zone.get(link["target"], "")
        if src_zone:
            connectivity[src_zone] += 1
        if dst_zone and dst_zone != src_zone:
            connectivity[dst_zone] += 1
    ordered = sorted(zone_ids, key=lambda z: (-connectivity[z], z))
    cols = 4 if len(ordered) > 8 else max(1, min(3, len(ordered)))
    cell_w = 340
    cell_h = 230
    positions = {}
    for idx, zid in enumerate(ordered):
        row = idx // cols
        col = idx % cols
        stagger = 70 if row % 2 and cols > 2 else 0
        positions[zid] = (140 + col * cell_w + stagger, 120 + row * cell_h)
    return positions


def compute_node_positions(topo):
    """Compute a stable zone-grid layout and node positions for Cytoscape preset."""
    nodes = topo["nodes"]
    zones = topo["zones"]
    zone_ids = [zone["id"] for zone in zones]
    zone_nodes = defaultdict(list)
    for node in nodes:
        zone_nodes[node["zone"]].append(node)

    connectivity = Counter()
    for link in topo["node_links"]:
        src_zone = next((n["zone"] for n in nodes if n["id"] == link["source"]), "")
        dst_zone = next((n["zone"] for n in nodes if n["id"] == link["target"]), "")
        if src_zone:
            connectivity[src_zone] += 1
        if dst_zone and dst_zone != src_zone:
            connectivity[dst_zone] += 1
    for link in topo["zone_links"]:
        connectivity[link["source"]] += 1
        connectivity[link["target"]] += 1

    ordered = sorted(zone_ids, key=lambda z: (-connectivity[z], z))
    if not ordered:
        ordered = sorted({node["zone"] for node in nodes})

    cols = 4 if len(ordered) > 6 else max(1, min(3, len(ordered)))
    cell_w = 430
    cell_h = 330
    positions = {}
    for idx, zone_id in enumerate(ordered):
        row = idx // cols
        col = idx % cols
        x0 = col * cell_w
        y0 = row * cell_h
        members = sorted(zone_nodes.get(zone_id, []), key=lambda n: (n["type"], n["name"], n["id"]))
        ncols = 3 if len(members) > 4 else max(1, min(2, len(members)))
        for nidx, node in enumerate(members):
            nr = nidx // ncols
            nc = nidx % ncols
            positions[node["id"]] = (x0 + 118 + nc * 132, y0 + 126 + nr * 86)

    orphans = [node for node in nodes if node["id"] not in positions]
    base_y = ((len(ordered) + cols - 1) // cols) * cell_h + 80
    for idx, node in enumerate(orphans):
        positions[node["id"]] = (90 + (idx % 4) * 130, base_y + (idx // 4) * 80)
    return positions


def compact_label(name, node_type):
    name = clean_text(name).replace(" System", "").replace(" Service", "")
    if len(name) > 24:
        words = name.split()
        if len(words) > 1:
            name = "\n".join([" ".join(words[:2]), " ".join(words[2:])])
        else:
            name = name[:21] + "..."
    return f"{name}\n{clean_text(node_type)}"


def normalize_class(value):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "other").lower()).strip("_")
    return value or "other"


def build_variant_payload(xml_files):
    variants = []
    for xml_file in xml_files:
        topo = parse_xml_topology(xml_file)
        topo["elements"] = to_cytoscape_elements(topo)
        topo["overview_elements"] = to_overview_elements(topo)
        variants.append(topo)
    variants.sort(key=lambda item: item["ig_level"])
    return variants


def find_latest_xmls(logs_dir, ig=None, template_id=None):
    run_dirs = sorted(Path(logs_dir).glob("run_*"), key=lambda p: p.name, reverse=True)
    selected = {}
    for run_dir in run_dirs:
        for xml_file in sorted(run_dir.glob("xml/*/*.xml")):
            try:
                root = ET.parse(xml_file).getroot()
            except ET.ParseError:
                continue
            xml_ig = root.get("ig_level") or xml_file.parent.name
            xml_template = root.get("template_id") or xml_file.stem
            if ig and xml_ig != ig:
                continue
            if template_id and xml_template != template_id:
                continue
            key = (xml_template, xml_ig)
            if key not in selected:
                selected[key] = xml_file
        if template_id:
            expected_igs = [ig] if ig else ["IG2", "IG3"]
            if all((template_id, expected) in selected for expected in expected_igs):
                break
    if not selected:
        return []
    if template_id:
        return [selected[key] for key in sorted(selected) if key[0] == template_id]
    first_template = sorted(selected.keys())[0][0]
    return [selected[key] for key in sorted(selected) if key[0] == first_template]


def group_xmls_by_template(xml_files):
    grouped = defaultdict(list)
    for xml_file in xml_files:
        root = ET.parse(xml_file).getroot()
        grouped[root.get("template_id") or Path(xml_file).stem].append(Path(xml_file))
    return grouped


def write_html(variants, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    template_id = variants[0]["template_id"] if variants else "topology"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"stage5_2_visualization_{template_id}_{ts}.html"
    html = build_html(variants)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def build_html(variants):
    variants_json = json.dumps(variants, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>TopoIntent Stage 5.2 Visualization</title>
  <script src="https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js"></script>
  <style>
    :root {{
      --bg:#0c1118; --panel:#111a24; --panel2:#162230; --text:#e6edf5; --muted:#91a1b5;
      --line:rgba(255,255,255,.09); --accent:#4aa3ff; --ok:#2ec4b6; --warn:#f3b23b; --bad:#ff6b6b;
      --node:#6ea8fe; --security:#2ec4b6; --workload:#c084fc; --network:#f3b23b; --data:#87d37c;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; height:100vh; overflow:hidden; background:var(--bg); color:var(--text);
      font-family:Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Arial, sans-serif; }}
    .app {{ display:grid; grid-template-columns:1fr 390px; height:100vh; }}
    #cy {{ width:100%; height:100vh; background:
      linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px),
      #0c1118; background-size:32px 32px; }}
    .panel {{ border-left:1px solid var(--line); background:linear-gradient(180deg,var(--panel),var(--panel2));
      padding:14px; overflow:auto; }}
    .topbar {{ position:absolute; left:14px; top:14px; z-index:3; display:flex; gap:8px; align-items:center;
      background:rgba(12,17,24,.82); border:1px solid var(--line); border-radius:8px; padding:8px;
      backdrop-filter: blur(8px); }}
    .ig-switch {{ display:flex; gap:6px; }}
    .mode-switch {{ display:flex; gap:6px; }}
    .ig-btn,.tool-btn {{ border:1px solid var(--line); background:rgba(255,255,255,.04); color:var(--text);
      border-radius:6px; padding:7px 10px; font-size:12px; cursor:pointer; }}
    .ig-btn.active,.tool-btn:hover {{ border-color:rgba(74,163,255,.55); background:rgba(74,163,255,.16); }}
    .title {{ font-weight:700; font-size:15px; margin:0 0 4px; }}
    .subtle {{ color:var(--muted); font-size:12px; line-height:1.45; }}
    .card {{ border:1px solid var(--line); background:rgba(255,255,255,.035); border-radius:8px; padding:12px; margin-bottom:12px; }}
    .card h3 {{ margin:0 0 10px; font-size:13px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:8px; background:rgba(0,0,0,.15); }}
    .metric b {{ display:block; font-size:16px; }}
    .metric span {{ color:var(--muted); font-size:11px; }}
    .row {{ display:grid; grid-template-columns:90px 1fr; gap:8px; margin:6px 0; font-size:12px; }}
    .row span:first-child {{ color:var(--muted); }}
    .row span:last-child, .subtle, .item {{ white-space:pre-line; }}
    .list {{ max-height:230px; overflow:auto; padding-right:4px; }}
    .item {{ padding:7px 8px; border:1px solid var(--line); border-radius:7px; margin-bottom:6px; font-size:12px; }}
    .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
    .btn-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
    .btn-grid button {{ border:1px solid var(--line); background:rgba(255,255,255,.04); color:var(--text);
      border-radius:7px; padding:8px; cursor:pointer; }}
    .btn-grid button:hover {{ border-color:rgba(255,255,255,.2); }}
    .btn-grid .danger {{ border-color:rgba(255,107,107,.45); color:#ffd2d2; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:7px; }}
    .legend span {{ font-size:11px; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:4px 8px; }}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="ig-switch" id="igSwitch"></div>
    <div class="mode-switch">
      <button class="tool-btn active" id="modeOverview" onclick="setViewMode('overview')">Overview</button>
      <button class="tool-btn" id="modeDetail" onclick="setViewMode('detail')">Detail</button>
    </div>
    <button class="tool-btn" onclick="fitGraph()">Fit</button>
    <button class="tool-btn" onclick="toggleZoneLinks()">Zone links</button>
  </div>
  <div class="app">
    <div id="cy"></div>
    <aside class="panel">
      <div class="card">
        <div class="title" id="topTitle"></div>
        <div class="subtle" id="topSubtitle"></div>
      </div>
      <div class="card">
        <h3>Metrics</h3>
        <div class="metrics" id="metrics"></div>
      </div>
      <div class="card">
        <h3>IG Difference</h3>
        <div id="diffBox" class="subtle"></div>
      </div>
      <div class="card">
        <h3>Selection</h3>
        <div id="selection" class="subtle">Click a node, link, or zone.</div>
      </div>
      <div class="card">
        <h3>Compliance</h3>
        <div id="compliance" class="list"></div>
      </div>
      <div class="card">
        <h3>Policies</h3>
        <div id="policies" class="list"></div>
      </div>
      <div class="card">
        <h3>Risk Hints</h3>
        <div id="risks" class="list"></div>
      </div>
      <div class="card">
        <h3>Actions</h3>
        <div class="btn-grid">
          <button onclick="deleteSelected()" class="danger">Delete selected</button>
          <button onclick="exportJson()">Export JSON</button>
          <button onclick="exportXml()">Export XML</button>
          <button onclick="exportHtml()">Export HTML</button>
        </div>
      </div>
      <div class="card">
        <h3>Legend</h3>
        <div class="legend">
          <span>teal security</span><span>purple workload/service</span><span>yellow network</span><span>dashed zone metadata</span>
        </div>
      </div>
    </aside>
  </div>
<script>
const variants = {variants_json};
let currentIndex = 0;
let cy = null;
let showZoneLinks = true;
let viewMode = 'overview';

function current() {{ return variants[currentIndex]; }}

function initSwitch() {{
  const box = document.getElementById('igSwitch');
  box.innerHTML = '';
  if (variants.length <= 1) {{
    const btn = document.createElement('button');
    btn.className = 'ig-btn active';
    btn.textContent = variants[0].ig_level || 'Topology';
    box.appendChild(btn);
    return;
  }}
  variants.forEach((v, idx) => {{
    const btn = document.createElement('button');
    btn.className = 'ig-btn' + (idx === currentIndex ? ' active' : '');
    btn.textContent = v.ig_level;
    btn.onclick = () => loadVariant(idx);
    box.appendChild(btn);
  }});
}}

function loadVariant(idx) {{
  currentIndex = idx;
  initSwitch();
  const v = current();
  document.getElementById('topTitle').textContent = `${{v.title}} (${{v.ig_level}})`;
  document.getElementById('topSubtitle').textContent = v.summary || v.description || v.xml_file;
  renderMetrics(v);
  renderDiff();
  renderCompliance(v);
  renderPolicies(v);
  renderRisks(v);
  renderGraph(v);
}}

function setViewMode(mode) {{
  viewMode = mode;
  document.getElementById('modeOverview').classList.toggle('active', mode === 'overview');
  document.getElementById('modeDetail').classList.toggle('active', mode === 'detail');
  renderGraph(current());
}}

function renderGraph(v) {{
  const elements = JSON.parse(JSON.stringify(viewMode === 'overview' ? v.overview_elements : v.elements));
  if (!showZoneLinks) {{
    for (const ele of elements) {{
      if (ele.classes && ele.classes.includes('zone-link')) ele.style = {{ display: 'none' }};
    }}
  }}
  if (cy) cy.destroy();
  cy = cytoscape({{
    container: document.getElementById('cy'),
    elements,
    style: [
      {{ selector:'node.zone', style: {{
        'shape':'roundrectangle','background-color':'rgba(25,38,54,.88)','border-color':'rgba(110,168,254,.26)',
        'border-width':1.5,'label':'data(label)','font-size':13,'font-weight':700,'color':'#d7e3f4',
        'text-wrap':'wrap','text-max-width':260,'line-height':1.22,
        'text-valign':'top','text-halign':'center','text-margin-y':12,'padding':'58px',
        'min-width':260,'min-height':150
      }} }},
      {{ selector:'node.overview-zone', style: {{
        'shape':'roundrectangle','background-color':'rgba(25,38,54,.92)','border-color':'rgba(110,168,254,.5)',
        'border-width':2,'label':'data(label)','font-size':13,'font-weight':700,'text-wrap':'wrap','text-max-width':140,
        'line-height':1.22,'color':'#e6edf5','text-valign':'center','text-halign':'center',
        'width':180,'height':105
      }} }},
      {{ selector:'node.topo-node', style: {{
        'shape':'roundrectangle','background-color':'var(--node)','label':'data(label)','font-size':9.5,'text-wrap':'wrap',
        'text-max-width':96,'line-height':1.15,'color':'#f8fbff','text-valign':'center','text-halign':'center',
        'width':100,'height':48,'border-width':1,'border-color':'rgba(255,255,255,.28)',
        'overlay-opacity':0
      }} }},
      {{ selector:'.firewall, .waf, .ids_ips, .siem, .identity_server, .vpn_gateway, .bastion', style: {{ 'background-color':'var(--security)' }} }},
      {{ selector:'.workload, .server, .database, .cloud_service, .storage', style: {{ 'background-color':'var(--workload)' }} }},
      {{ selector:'.router, .switch, .load_balancer', style: {{ 'background-color':'var(--network)' }} }},
      {{ selector:'edge.node-link', style: {{
        'width':1.35,'line-color':'rgba(145,161,181,.42)','target-arrow-shape':'triangle',
        'target-arrow-color':'rgba(145,161,181,.42)','curve-style':'bezier','control-point-step-size':36,
        'label':'','font-size':7,'color':'#91a1b5','text-rotation':'autorotate','z-index':1
      }} }},
      {{ selector:'edge.node-link:selected', style: {{
        'width':3,'line-color':'#d7e3f4','target-arrow-color':'#d7e3f4','label':'data(label)','z-index':20
      }} }},
      {{ selector:'edge.zone-link', style: {{
        'width':1,'line-style':'dashed','line-color':'rgba(243,178,59,.32)','target-arrow-shape':'triangle',
        'target-arrow-color':'rgba(243,178,59,.32)','curve-style':'bezier','label':'','font-size':8,'color':'#f3b23b'
      }} }},
      {{ selector:'edge.zone-link:selected', style: {{ 'label':'data(label)','line-color':'#f3b23b','target-arrow-color':'#f3b23b' }} }},
      {{ selector:'edge.overview-edge', style: {{
        'width':'mapData(link_count, 1, 20, 1.5, 7)','line-color':'rgba(145,161,181,.62)',
        'target-arrow-shape':'triangle','target-arrow-color':'rgba(145,161,181,.62)','curve-style':'bezier',
        'label':'data(label)','font-size':10,'color':'#cbd7e8','text-background-color':'#0c1118',
        'text-background-opacity':.85,'text-background-padding':3
      }} }},
      {{ selector:':selected', style: {{ 'border-width':3,'border-color':'#ffffff','line-color':'#ffffff','target-arrow-color':'#ffffff' }} }}
    ],
    layout: {{ name:'preset', fit:true, padding:70 }}
  }});
  cy.on('tap', 'node, edge', e => renderSelection(e.target));
  cy.on('dbltap', 'node.overview-zone', e => {{
    setViewMode('detail');
    const zid = e.target.data('zone_id');
    setTimeout(() => {{
      const zone = cy.getElementById('zone:' + zid);
      if (zone && zone.length) {{
        zone.select();
        cy.animate({{ center: {{ eles: zone }}, zoom: 1.05, duration: 250 }});
      }}
    }}, 60);
  }});
  cy.on('tap', e => {{ if (e.target === cy) document.getElementById('selection').textContent='Click a node, link, or zone.'; }});
}}

function renderMetrics(v) {{
  const m = v.metrics;
  const data = [['Zones',m.zones],['Nodes',m.nodes],['Node links',m.node_links],['Zone links',m.zone_links],['Policies',m.policies],['Controls',m.controls]];
  document.getElementById('metrics').innerHTML = data.map(([k,val]) => `<div class="metric"><b>${{val}}</b><span>${{k}}</span></div>`).join('');
}}

function renderDiff() {{
  const box = document.getElementById('diffBox');
  if (variants.length < 2) {{ box.textContent = 'Only one IG level is loaded.'; return; }}
  const base = variants[0], other = variants[1];
  const rows = ['zones','nodes','node_links','zone_links','policies','controls'].map(k => {{
    const a = base.metrics[k], b = other.metrics[k], delta = b - a;
    return `${{k}}: ${{base.ig_level}}=${{a}}, ${{other.ig_level}}=${{b}} (${{delta >= 0 ? '+' : ''}}${{delta}})`;
  }});
  box.innerHTML = rows.join('<br>');
}}

function renderSelection(ele) {{
  const d = ele.data();
  const display = {{...d}};
  if (display.source && display.source.startsWith('node:')) display.source_name = nodeDisplayName(display.source.replace('node:', ''));
  if (display.target && display.target.startsWith('node:')) display.target_name = nodeDisplayName(display.target.replace('node:', ''));
  const rows = Object.keys(display).filter(k => !['parent'].includes(k)).map(k => `<div class="row"><span>${{k}}</span><span>${{display[k] ?? ''}}</span></div>`);
  document.getElementById('selection').innerHTML = rows.join('');
}}

function renderCompliance(v) {{
  const c = v.compliance;
  const head = `<div class="item">All required satisfied: <b class="${{c.all_required_satisfied === 'true' ? 'ok' : 'bad'}}">${{c.all_required_satisfied || 'unknown'}}</b></div>`;
  const items = c.controls.map(x => `<div class="item"><b>${{x.id}}</b> <span class="${{x.status === 'satisfied' ? 'ok' : 'warn'}}">${{x.status}}</span><br>${{x.title}}</div>`).join('');
  document.getElementById('compliance').innerHTML = head + items;
}}

function renderPolicies(v) {{
  document.getElementById('policies').innerHTML = v.policies.slice(0, 90).map(p =>
    `<div class="item"><b class="${{p.expected === 'allow' ? 'ok' : 'bad'}}">${{p.expected.toUpperCase()}}</b> ${{policyEndpoint(p, 'src')}} → ${{policyEndpoint(p, 'dst')}} ${{p.protocol}}/${{p.port}}<br><span class="subtle">${{p.reason}}</span></div>`
  ).join('');
}}

function nodeDisplayName(id) {{
  const n = current().nodes.find(x => x.id === id);
  return n ? `${{n.name}} (${{id}}, ${{n.type}})` : id;
}}

function policyEndpoint(p, side) {{
  const id = side === 'src' ? p.src_node : p.dst_node;
  const name = side === 'src' ? p.src_name : p.dst_name;
  const type = side === 'src' ? p.src_type : p.dst_type;
  const zone = side === 'src' ? p.src_zone : p.dst_zone;
  if (!id) return zone || '';
  return `${{name || id}} <span class="subtle">(${{id}}${{type ? ', ' + type : ''}})</span>`;
}}

function renderRisks(v) {{
  const box = document.getElementById('risks');
  if (!v.risks.length) {{ box.innerHTML = '<div class="item ok">No derived risk hints.</div>'; return; }}
  box.innerHTML = v.risks.map(r => `<div class="item"><b class="${{r.level === 'high' ? 'bad' : 'warn'}}">${{r.level}}</b> ${{r.target}}<br>${{r.desc}}</div>`).join('');
}}

function fitGraph() {{ if (cy) cy.fit(undefined, 40); }}
function toggleZoneLinks() {{ showZoneLinks = !showZoneLinks; loadVariant(currentIndex); }}
function deleteSelected() {{ if (!cy) return; cy.$(':selected').remove(); }}
function exportJson() {{ download('topology_' + current().ig_level + '.json', JSON.stringify(current(), null, 2)); }}
function exportXml() {{ download('topology_' + current().ig_level + '.xml', serializeVisibleXml()); }}
function exportHtml() {{ download('topology_visualization.html', document.documentElement.outerHTML); }}
function download(name, text) {{
  const blob = new Blob([text], {{ type:'text/plain;charset=utf-8' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  URL.revokeObjectURL(a.href);
}}
function serializeVisibleXml() {{
  const nodes = cy.nodes('[kind="node"]').map(n => n.data());
  const edges = cy.edges('[kind="node_link"]').map(e => e.data());
  let xml = `<Topology template_id="${{current().template_id}}" ig_level="${{current().ig_level}}">\\n<Nodes>\\n`;
  for (const n of nodes) xml += `  <Node id="${{n.node_id}}" name="${{escapeXml(n.name)}}" domain="${{n.zone}}" type="${{n.node_type}}" ip="${{n.ip || ''}}"/>\\n`;
  xml += `</Nodes>\\n<Links>\\n<NodeLinks>\\n`;
  for (const e of edges) xml += `  <Link id="${{e.link_id}}" source="${{e.source.replace('node:','')}}" target="${{e.target.replace('node:','')}}" type="${{e.link_type}}" label="${{escapeXml(e.label || '')}}"/>\\n`;
  xml += `</NodeLinks>\\n</Links>\\n</Topology>\\n`;
  return xml;
}}
function escapeXml(s) {{ return String(s ?? '').replace(/[<>&"']/g, ch => ({{'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&apos;'}}[ch])); }}

initSwitch();
loadVariant(0);
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate Stage 5.2 interactive topology visualization")
    parser.add_argument("--xml-file", action="append", default=None, help="XML file to visualize. Can be passed multiple times.")
    parser.add_argument("--xml-dir", type=str, default=None, help="Directory containing IG XML files")
    parser.add_argument("--ig", choices=["IG2", "IG3", "all"], default="all")
    parser.add_argument("--template-id", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    run_dir = Path(args.output_dir) if args.output_dir else create_run_directory()
    log = setup_logger("stage5_2", run_dir)
    base_dir = Path(__file__).resolve().parent.parent

    if args.xml_file:
        xml_files = [Path(p) for p in args.xml_file]
    elif args.xml_dir:
        xml_files = sorted(Path(args.xml_dir).glob("*/*.xml")) + sorted(Path(args.xml_dir).glob("*.xml"))
        if args.ig != "all":
            xml_files = [p for p in xml_files if p.parent.name == args.ig or f"_{args.ig}" in p.stem]
    else:
        xml_files = find_latest_xmls(base_dir / "logs", ig=None if args.ig == "all" else args.ig, template_id=args.template_id)

    if not xml_files:
        log.error("No XML files found for visualization")
        sys.exit(1)

    grouped = group_xmls_by_template(xml_files)
    outputs = []
    for template_id, files in grouped.items():
        if args.ig != "all":
            files = [f for f in files if parse_xml_topology(f)["ig_level"] == args.ig]
        variants = build_variant_payload(files)
        if not variants:
            continue
        html_path = write_html(variants, run_dir)
        outputs.append({
            "template_id": template_id,
            "html_file": str(html_path),
            "ig_levels": [v["ig_level"] for v in variants],
            "xml_files": [str(f) for f in files],
        })
        log.info(f"Generated visualization: {html_path}")

    save_json(outputs, run_dir, "stage5_2_manifest.json")
    print(json.dumps(outputs, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
