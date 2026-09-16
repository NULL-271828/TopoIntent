#!/usr/bin/env python3
"""
Stage 3: Topology Fusion and IG-level Completion
Step 1: Fuse user intent with Top-3 reference templates to fill blank areas
Step 2: Complete topology for target CIS IG level (IG2/IG3)
"""

import json
import os
import requests
import logging
from pathlib import Path

# Clear proxy env vars to ensure direct local connections
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

# === Config ===
LLM_API = os.getenv("LLM_API", "http://127.0.0.1:8005/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")
CIS_JSON = Path(__file__).resolve().parent.parent / "dataset/topology/CIS_requirements/cis_controls.json"
TRAIN_JSON = Path(__file__).resolve().parent.parent / "dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from SYSTEM_PROMPT import STAGE3_FUSION_PROMPT, STAGE3_REPAIR_PROMPT
from logging_utils import setup_logger, save_json, save_markdown

logger = None

# === CIS IG mappings (cleaned IDs) ===
CIS_IG_RULES = {
    "IG1": ["1.1", "4.4", "6.4", "9.2"],
    "IG2": ["1.1", "3.8", "3.12", "4.4", "4.9", "6.4", "9.2", "9.3",
            "12.2", "12.3", "12.5", "12.7", "13.3", "13.4", "13.5",
            "13.6", "16.8", "18.2"],
    "IG3": ["1.1", "3.8", "3.12", "4.4", "4.9", "6.4", "9.2", "9.3",
            "12.2", "12.3", "12.5", "12.7", "12.8", "13.3", "13.4", "13.5",
            "13.6", "13.8", "13.9", "13.10", "16.8", "18.2"],
}


def load_cis_requirements():
    """Load CIS requirements from JSON."""
    with open(CIS_JSON) as f:
        return json.load(f)


def load_train_templates():
    """Load training templates with CIS rules for reference."""
    with open(TRAIN_JSON) as f:
        data = json.load(f)
    templates = {}
    for item in data["items"]:
        tid = item["template_id"]
        if tid not in templates:
            templates[tid] = item["ground_truth"]
    return templates


def call_llm(system_prompt, user_message, temperature=0.1, max_tokens=8192):
    """Call LLM via OpenAI-compatible API."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if logger:
        logger.debug(f"LLM request: {user_message[:200]}...")
    # Disable proxies for local requests to avoid proxy errors
    resp = requests.post(LLM_API, json=payload, timeout=180, proxies={"http": "", "https": ""})
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    finish_reason = choice.get("finish_reason")
    result = choice["message"]["content"].strip()
    if logger:
        if finish_reason == "length":
            logger.warning(
                f"LLM response was truncated by max_tokens={max_tokens}; "
                "increase max_tokens or reduce requested output size"
            )
        logger.debug(f"LLM response: {result[:500]}...")
    return result


def parse_llm_json(text):
    """Parse JSON from LLM output, handling markdown and Python-style dicts."""
    import ast

    # 1. Strip markdown code blocks
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{") or p.startswith("["):
                text = p
                break
    
    text = text.strip()
    
    # 2. Extract JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in response")
    
    text = text[start : end + 1]
    
    # 3. Try standard JSON parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError as json_err:
        # Fallback: Try parsing as Python literal (handles single quotes, etc.)
        try:
            return ast.literal_eval(text)
        except Exception as py_err:
            tail = text[-200:] if len(text) > 200 else text
            raise ValueError(
                "Failed to parse LLM output as JSON or Python dict. "
                f"json_error={json_err}; python_error={py_err}; "
                f"chars={len(text)}; head={text[:200]!r}; tail={tail!r}"
            )


def validate_topology(topo):
    """Ensure topology has all required fields."""
    required = {"schema_version": "2.1", "template_id": "", "title": "",
                "description": "", "scenario": "", "topology_summary": "",
                "zones": [], "nodes": [], "links": {"node_links": [], "zone_links": []}}
    for k, v in required.items():
        if k not in topo:
            topo[k] = v
    if "links" not in topo:
        topo["links"] = {"node_links": [], "zone_links": []}
    if "node_links" not in topo["links"]:
        topo["links"]["node_links"] = []
    if "zone_links" not in topo["links"]:
        topo["links"]["zone_links"] = []
    return topo


def _existing_ids(items, key):
    return {item.get(key) for item in items if item.get(key)}


def _append_unique(existing, additions, key):
    seen = _existing_ids(existing, key)
    for item in additions or []:
        item_id = item.get(key)
        if not item_id or item_id in seen:
            continue
        existing.append(item)
        seen.add(item_id)


def merge_topology_additions(base_topology, additions):
    """Merge LLM-generated additions into a topology without modifying existing elements."""
    import copy

    repaired = copy.deepcopy(base_topology)
    repaired = validate_topology(repaired)
    additions = additions or {}

    _append_unique(repaired["zones"], additions.get("zones", []), "zone_id")
    _append_unique(repaired["nodes"], additions.get("nodes", []), "node_id")

    addition_links = additions.get("links", {})
    if isinstance(addition_links, dict):
        _append_unique(
            repaired["links"]["node_links"],
            addition_links.get("node_links", []),
            "link_id",
        )
        _append_unique(
            repaired["links"]["zone_links"],
            addition_links.get("zone_links", []),
            "link_id",
        )

    return repaired


def compact_reference_templates(ref_templates):
    """Keep only topology fields needed for Stage 3 prompts."""
    compact = []
    for ref in ref_templates:
        topo = ref.get("topology_template", ref)
        compact.append({
            "template_id": topo.get("template_id", ""),
            "title": topo.get("title", ""),
            "scenario": topo.get("scenario", ""),
            "topology_summary": topo.get("topology_summary", ""),
            "zones": topo.get("zones", []),
            "nodes": topo.get("nodes", []),
            "links": topo.get("links", {}),
        })
    return compact


def step1_fusion(user_intent, stage1_template, ref_templates, logger_ref=None):
    """
    Step 1: Fuse user intent with Top-3 reference templates.
    User-specified content is immutable; blank areas are filled from references.
    """
    base_topology = validate_topology(stage1_template)
    user_msg = (
        f"User intent: {user_intent}\n\n"
        f"Current Stage 1 topology:\n{json.dumps(base_topology, separators=(',', ':'))}\n\n"
        f"Top-3 reference topology patterns:\n{json.dumps(compact_reference_templates(ref_templates), separators=(',', ':'))}\n\n"
        "Return ONLY additive zones, nodes, and links needed to complete the topology. "
        "Do NOT return the full topology. Do NOT repeat existing elements."
    )

    raw = call_llm(STAGE3_FUSION_PROMPT, user_msg, max_tokens=4096)
    additions = parse_llm_json(raw)
    fused = merge_topology_additions(base_topology, additions)

    if logger_ref:
        logger_ref.info(f"Fused: {len(fused.get('zones', []))} zones, "
                        f"{len(fused.get('nodes', []))} nodes, "
                        f"{len(fused.get('links', {}).get('zone_links', []))} zone_links, "
                        f"{len(fused.get('links', {}).get('node_links', []))} node_links")

    return fused


def get_target_cis_requirements(target_ig, cis_map):
    """
    Return the target CIS safeguards for an IG level.

    Stage 3 uses these as completion targets only. Stage 4 will verify whether
    the final topology actually satisfies them.
    """
    target_rules = CIS_IG_RULES.get(target_ig, [])
    requirements = []
    for rule_id in target_rules:
        if rule_id in cis_map:
            requirements.append({
                "cis_safeguard_id": rule_id,
                "title": cis_map[rule_id]["title"],
                "description": cis_map[rule_id]["description"][:200],
            })
    return requirements


def get_unsatisfied_cis(fused_topo, ref_templates, target_ig, cis_map):
    """Backward-compatible alias for older callers."""
    return get_target_cis_requirements(target_ig, cis_map)


def step2_completion(fused_topo, target_cis, target_ig, logger_ref=None):
    """
    Step 2: Complete topology for the target IG level.
    """
    if not target_cis:
        if logger_ref:
            logger_ref.info(f"{target_ig}: No target CIS safeguards configured")
        return fused_topo

    user_msg = (
        f"Current topology:\n{json.dumps(fused_topo, separators=(',', ':'))}\n\n"
        f"Target CIS safeguards for {target_ig} completion:\n{json.dumps(target_cis, separators=(',', ':'))}\n\n"
        "Return ONLY the new zones, nodes, and links that must be added. "
        "Do NOT return the full topology. Do NOT repeat existing elements. "
        "This is completion, not verification."
    )

    raw = call_llm(STAGE3_REPAIR_PROMPT, user_msg, max_tokens=4096)
    additions = parse_llm_json(raw)
    completed = merge_topology_additions(fused_topo, additions)

    if logger_ref:
        logger_ref.info(f"Completed ({target_ig}): {len(completed.get('zones', []))} zones, "
                        f"{len(completed.get('nodes', []))} nodes, "
                        f"{len(completed.get('links', {}).get('zone_links', []))} zone_links, "
                        f"{len(completed.get('links', {}).get('node_links', []))} node_links")

    return completed


def step2_repair(fused_topo, unsatisfied_cis, target_ig, logger_ref=None):
    """Backward-compatible alias. Stage 3 now performs completion, not verification/repair."""
    return step2_completion(fused_topo, unsatisfied_cis, target_ig, logger_ref=logger_ref)


def process_single_sample(sample, cis_map, train_templates, ig_level="IG2", logger_ref=None):
    """
    Process a single sample through Stage 3 fusion + IG-level completion.

    Args:
        sample: dict with sample_id, template_id, user_intent, stage1_template, retrieved_templates
        cis_map: CIS requirements mapping
        train_templates: training templates with CIS rules
        ig_level: "IG2" or "IG3"
        logger_ref: optional logger

    Returns:
        dict with fused and completed topology results
    """
    sample_id = sample["sample_id"]
    user_intent = sample["user_intent"]
    stage1_template = sample["stage1_template"]
    retrieved = sample["retrieved_templates"]

    # Load reference templates with CIS rules
    ref_templates = []
    for r in retrieved[:3]:
        tid = r["template_id"]
        if tid in train_templates:
            ref_templates.append(train_templates[tid])

    if logger_ref:
        logger_ref.info(f"[{sample_id}] Step 1: Fusion")
    fused = step1_fusion(user_intent, stage1_template, ref_templates, logger_ref=logger_ref)

    if logger_ref:
        logger_ref.info(f"[{sample_id}] Step 2: IG Completion ({ig_level})")
    target_cis = get_target_cis_requirements(ig_level, cis_map)
    completed = step2_completion(fused, target_cis, ig_level, logger_ref=logger_ref)

    return {
        "sample_id": sample_id,
        "gt_template_id": sample["gt_template_id"],
        "fused_topology": fused,
        "target_cis_requirements": target_cis,
        "completed_topology": completed,
        # Backward-compatible alias for older scripts. Semantically, this is now completed_topology.
        "repaired_topology": completed,
    }


def evaluate_results(all_results, train_templates, logger_ref=None):
    """
    Evaluate Stage 3 results.

    Metrics:
    - Structural completeness: zones, nodes, links filled
    - Completion structural size: zones, nodes, links filled
    """
    metrics = {
        "total_samples": len(all_results),
        "structural": {"zones": [], "nodes": [], "zone_links": [], "node_links": []},
    }

    for r in all_results:
        repaired = r.get("completed_topology", r.get("repaired_topology", {}))

        # Structural completeness
        metrics["structural"]["zones"].append(len(repaired.get("zones", [])))
        metrics["structural"]["nodes"].append(len(repaired.get("nodes", [])))
        metrics["structural"]["zone_links"].append(len(repaired.get("links", {}).get("zone_links", [])))
        metrics["structural"]["node_links"].append(len(repaired.get("links", {}).get("node_links", [])))

    # Compute averages
    n = len(all_results)
    for k in list(metrics["structural"].keys()):
        metrics["structural"][f"{k}_avg"] = round(sum(metrics["structural"][k]) / n, 1) if n > 0 else 0

    if logger_ref:
        logger_ref.info(f"Structural: avg zones={metrics['structural']['zones_avg']}, "
                        f"nodes={metrics['structural']['nodes_avg']}, "
                        f"zone_links={metrics['structural']['zone_links_avg']}, "
                        f"node_links={metrics['structural']['node_links_avg']}")

    return metrics


def generate_evaluation_report(metrics):
    """Generate markdown evaluation report."""
    lines = [
        "# Stage 3 Fusion and Completion Report",
        "",
        "## Structural Completeness",
        f"- Avg zones: {metrics['structural']['zones_avg']}",
        f"- Avg nodes: {metrics['structural']['nodes_avg']}",
        f"- Avg zone_links: {metrics['structural']['zone_links_avg']}",
        f"- Avg node_links: {metrics['structural']['node_links_avg']}",
        "",
    ]

    return "\n".join(lines)
