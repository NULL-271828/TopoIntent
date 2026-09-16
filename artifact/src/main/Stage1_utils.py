#!/usr/bin/env python3
"""
Stage 1: Intent Analysis (LLM-driven)
Extract structured fields from user natural language intent using LLM.
Output: Initial topology template following schema_contract format.
"""

import json
import os
import requests
import logging
import ast
import time
from pathlib import Path

# === Config ===
LLM_API = os.getenv("LLM_API", "http://127.0.0.1:8005/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")

# Import system prompts
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from SYSTEM_PROMPT import STAGE1_SYSTEM_PROMPT

# Import logging utils
from logging_utils import setup_logger, save_json, save_markdown

logger = None  # Will be set by caller

VALID_SCENARIOS = {
    "cloud", "enterprise", "higher_education", "hospital",
    "industrial", "finance", "government", "other",
}
SCENARIO_ALIASES = {
    "education": "higher_education",
    "healthcare": "hospital",
    "financial": "finance",
}
VALID_ZONE_TYPES = {
    "enterprise", "dmz", "data_center", "campus", "cloud_hub",
    "cloud_spoke", "management", "security", "ot", "iot", "external", "other",
}
VALID_NODE_TYPES = {
    "router", "switch", "firewall", "waf", "load_balancer", "vpn_gateway",
    "bastion", "ids_ips", "siem", "identity_server", "wireless_controller",
    "access_point", "server", "database", "storage", "workload", "endpoint",
    "cloud_service", "ot_controller", "iot_device", "other",
}


class Stage1AnalysisError(ValueError):
    """Raised when Stage 1 cannot produce a valid template after retries."""

    def __init__(self, message, metadata):
        super().__init__(message)
        self.metadata = metadata


def call_llm(system_prompt, user_message, temperature=0.1, max_tokens=4096):
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
        logger.debug(f"LLM request: model={LLM_MODEL}, user_msg={user_message[:100]}...")
    resp = requests.post(LLM_API, json=payload, timeout=120, proxies={"http": "", "https": ""})
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    finish_reason = choice.get("finish_reason")
    result = choice["message"]["content"].strip()
    if logger:
        if finish_reason == "length":
            logger.warning(f"LLM response was truncated by max_tokens={max_tokens}")
        logger.debug(f"LLM response: {result[:500]}...")
    return result


def parse_llm_json_output(text):
    """Parse JSON output from LLM, handling markdown and minor Python-style output."""
    original = text
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                text = candidate
                break
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in LLM output: {original[:200]!r}")

    text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as json_err:
        try:
            return ast.literal_eval(text)
        except Exception as py_err:
            tail = text[-200:] if len(text) > 200 else text
            raise ValueError(
                "Failed to parse LLM output as JSON or Python dict. "
                f"json_error={json_err}; python_error={py_err}; "
                f"chars={len(text)}; head={text[:200]!r}; tail={tail!r}"
            )


def _normalize_scenario(value):
    scenario = str(value or "other").strip().lower()
    scenario = SCENARIO_ALIASES.get(scenario, scenario)
    return scenario if scenario in VALID_SCENARIOS else "other"


def _normalize_id(value, prefix, index):
    value = str(value or "").strip()
    return value if value else f"{prefix}{index}"


def _normalize_zone(zone, index):
    if not isinstance(zone, dict):
        zone = {"name": str(zone)}
    zone = dict(zone)
    zone["zone_id"] = _normalize_id(zone.get("zone_id"), "z", index)
    zone["name"] = str(zone.get("name") or f"Zone {index}")
    zone_type = str(zone.get("zone_type") or "other").strip()
    zone["zone_type"] = zone_type if zone_type in VALID_ZONE_TYPES else "other"
    return zone


def _normalize_node(node, index, valid_zone_ids):
    if not isinstance(node, dict):
        node = {"name": str(node)}
    node = dict(node)
    node["node_id"] = _normalize_id(node.get("node_id"), "n", index)
    node["name"] = str(node.get("name") or f"Node {index}")
    node_type = str(node.get("normalized_type") or "other").strip()
    node["normalized_type"] = node_type if node_type in VALID_NODE_TYPES else "other"
    zone_id = str(node.get("zone_id") or "").strip()
    node["zone_id"] = zone_id if zone_id in valid_zone_ids else (sorted(valid_zone_ids)[0] if valid_zone_ids else "")
    return node


def validate_template(template, sample_id=None, user_intent=None):
    """Ensure template has all required schema fields."""
    if not isinstance(template, dict):
        template = {}

    required = ["schema_version", "template_id", "title", "description", "scenario",
                "topology_summary", "zones", "nodes", "links"]
    for key in required:
        if key not in template:
            if key in ["zones", "nodes"]:
                template[key] = []
            elif key == "links":
                template[key] = {"node_links": [], "zone_links": []}
            else:
                template[key] = ""

    template["schema_version"] = str(template.get("schema_version") or "2.1")
    if sample_id and not template.get("template_id"):
        template["template_id"] = f"generated_{sample_id}"
    if user_intent and not template.get("description"):
        template["description"] = user_intent
    template["scenario"] = _normalize_scenario(template.get("scenario"))

    zones = template.get("zones")
    if not isinstance(zones, list):
        zones = []
    template["zones"] = [_normalize_zone(z, i + 1) for i, z in enumerate(zones)]

    zone_ids = {z["zone_id"] for z in template["zones"]}
    nodes = template.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
    template["nodes"] = [_normalize_node(n, i + 1, zone_ids) for i, n in enumerate(nodes)]

    links = template.get("links")
    if not isinstance(links, dict):
        links = {}
    node_links = links.get("node_links", [])
    zone_links = links.get("zone_links", [])
    template["links"] = {
        "node_links": node_links if isinstance(node_links, list) else [],
        "zone_links": zone_links if isinstance(zone_links, list) else [],
    }
    return template


def analyze_intent(user_intent, sample_id, logger_ref=None, return_metadata=False):
    """
    Analyze a single user intent using LLM and produce initial topology template.

    Args:
        user_intent: str, the natural language user intent
        sample_id: str, unique identifier for this sample
        logger_ref: optional logger instance

    Returns:
        dict: Initial topology template following schema_contract format
    """
    metadata = {"attempts": 0, "raw_outputs": [], "error": None, "elapsed_sec": 0.0}
    user_message = f"User intent: {user_intent}\nSample ID: {sample_id}"
    started = time.time()

    for attempt in range(2):
        metadata["attempts"] = attempt + 1
        retry_suffix = ""
        if attempt:
            retry_suffix = (
                "\n\nYour previous response was not parseable as JSON. "
                "Return exactly one valid JSON object with no markdown."
            )
        try:
            raw_output = call_llm(STAGE1_SYSTEM_PROMPT, user_message + retry_suffix)
            metadata["raw_outputs"].append(raw_output)
            template = parse_llm_json_output(raw_output)
            template = validate_template(template, sample_id=sample_id, user_intent=user_intent)
            metadata["elapsed_sec"] = round(time.time() - started, 3)
            if logger_ref:
                logger_ref.info(f"Generated template: scenario={template.get('scenario')}, "
                                f"zones={len(template.get('zones', []))}, "
                                f"nodes={len(template.get('nodes', []))}, "
                                f"attempts={metadata['attempts']}, "
                                f"elapsed={metadata['elapsed_sec']}s")
            return (template, metadata) if return_metadata else template
        except Exception as e:
            metadata["error"] = str(e)
            if logger_ref:
                logger_ref.warning(f"Attempt {attempt + 1} failed for {sample_id}: {e}")

    metadata["elapsed_sec"] = round(time.time() - started, 3)
    raise Stage1AnalysisError(metadata["error"] or f"Failed to analyze {sample_id}", metadata)


def compute_accuracy(results, logger_ref=None):
    """
    Compute Stage 1 extraction accuracy against ground truth.

    Metrics:
    - scenario_accuracy: exact match ratio
    - zone_type_recall: how many GT zone_types are covered
    - node_type_recall: how many GT node types are covered

    Args:
        results: list of dicts with generated_template and ground_truth_template
        logger_ref: optional logger instance

    Returns:
        dict with accuracy metrics and per-sample details
    """
    scenario_correct = 0
    zone_recalls = []
    node_recalls = []
    accuracy_details = []

    for r in results:
        sample_id = r["sample_id"]
        gen = r.get("generated_template", {})
        gt = r.get("ground_truth_template", {})

        gt_zone_types = set(z.get("zone_type", "") for z in gt.get("zones", []))
        gt_node_types = set(n.get("normalized_type", "") for n in gt.get("nodes", []))
        gt_scenario = gt.get("scenario", "unknown")

        # Scenario accuracy
        scenario_match = gen.get("scenario", "").lower() == gt_scenario.lower()
        if scenario_match:
            scenario_correct += 1

        # Zone type recall
        gen_zone_types = set(z.get("zone_type", "") for z in gen.get("zones", []))
        zone_overlap = gen_zone_types & gt_zone_types
        zone_recall = len(zone_overlap) / len(gt_zone_types) if gt_zone_types else 1.0

        # Node type recall
        gen_node_types = set(n.get("normalized_type", "") for n in gen.get("nodes", []))
        node_overlap = gen_node_types & gt_node_types
        node_recall = len(node_overlap) / len(gt_node_types) if gt_node_types else 1.0

        detail = {
            "sample_id": sample_id,
            "scenario_match": scenario_match,
            "gt_scenario": gt_scenario,
            "pred_scenario": gen.get("scenario"),
            "zone_type_recall": round(zone_recall, 3),
            "gt_zone_types": sorted(gt_zone_types),
            "pred_zone_types": sorted(gen_zone_types),
            "node_type_recall": round(node_recall, 3),
            "gt_node_types": sorted(gt_node_types),
            "pred_node_types": sorted(gen_node_types),
        }
        accuracy_details.append(detail)
        zone_recalls.append(zone_recall)
        node_recalls.append(node_recall)

    n = len(results)
    metrics = {
        "total_samples": n,
        "scenario_accuracy": round(scenario_correct / n, 4) if n > 0 else 0,
        "avg_zone_type_recall": round(sum(zone_recalls) / n, 4) if n > 0 else 0,
        "avg_node_type_recall": round(sum(node_recalls) / n, 4) if n > 0 else 0,
        "details": accuracy_details,
    }

    if logger_ref:
        logger_ref.info(f"Accuracy: scenario={metrics['scenario_accuracy']}, "
                        f"zone_type_recall={metrics['avg_zone_type_recall']}, "
                        f"node_type_recall={metrics['avg_node_type_recall']}")

    return metrics


def generate_accuracy_report(metrics):
    """Generate a human-readable markdown accuracy report."""
    lines = [
        "# Stage 1 Intent Analysis Accuracy Report",
        "",
        "## Summary",
        f"- Total samples: {metrics['total_samples']}",
        f"- Scenario accuracy: {metrics['scenario_accuracy']:.1%}",
        f"- Avg zone type recall: {metrics['avg_zone_type_recall']:.1%}",
        f"- Avg node type recall: {metrics['avg_node_type_recall']:.1%}",
        "",
        "## Per-Sample Details",
        "",
        "| Sample | Scenario | Zone Type Recall | Node Type Recall |",
        "|--------|----------|------------------|------------------|",
    ]

    for d in metrics["details"]:
        scenario_text = d["pred_scenario"]
        if not d["scenario_match"]:
            scenario_text = f"{d['pred_scenario']} (GT: {d['gt_scenario']})"
        lines.append(
            f"| {d['sample_id']} "
            f"| {'✅' if d['scenario_match'] else '❌'} {scenario_text} "
            f"| {d['zone_type_recall']:.2f} "
            f"| {d['node_type_recall']:.2f} |"
        )

    lines.append("")
    return "\n".join(lines)


def analyze_intents_from_json(json_path, run_dir=None, logger_ref=None):
    """
    Load training JSON and analyze all user intents using LLM.

    Args:
        json_path: str or Path, path to the JSON file
        run_dir: optional Path for logging
        logger_ref: optional logger instance

    Returns:
        tuple: (results list, accuracy metrics)
    """
    global logger
    logger = logger_ref

    with open(json_path) as f:
        data = json.load(f)

    # Build ground truth lookup
    gt_by_tid = {}
    for item in data["items"]:
        tid = item["template_id"]
        if tid not in gt_by_tid:
            gt_by_tid[tid] = item["ground_truth"]["topology_template"]

    results = []
    generated_templates = []
    raw_records = []
    failures = []

    for i, item in enumerate(data["items"]):
        sample_id = item["sample_id"]
        if logger:
            logger.info(f"[{i+1}/{len(data['items'])}] Analyzing: {sample_id}")
        else:
            print(f"  [{i+1}/{len(data['items'])}] Analyzing: {sample_id}")

        try:
            template, llm_meta = analyze_intent(
                item["synthetic_user_intent"],
                sample_id,
                logger_ref=logger,
                return_metadata=True,
            )
            status = "ok"
        except Exception as e:
            err_msg = f"Failed to analyze {sample_id}: {e}"
            if logger:
                logger.error(err_msg)
            else:
                print(f"  [WARN] {err_msg}")
            status = "failed"
            llm_meta = getattr(e, "metadata", None) or {
                "attempts": 0,
                "raw_outputs": [],
                "error": str(e),
                "elapsed_sec": 0.0,
            }
            template = validate_template({
                "schema_version": "2.1",
                "template_id": f"generated_{sample_id}",
                "title": "",
                "description": item["synthetic_user_intent"],
                "scenario": "other",
                "topology_summary": "",
                "zones": [],
                "nodes": [],
                "links": {"node_links": [], "zone_links": []},
            }, sample_id=sample_id, user_intent=item["synthetic_user_intent"])
            failures.append({"sample_id": sample_id, "error": str(e)})

        result = {
            "sample_id": sample_id,
            "template_id": item["template_id"],
            "user_intent": item["synthetic_user_intent"],
            "status": status,
            "generated_template": template,
            "ground_truth_template": gt_by_tid[item["template_id"]],
        }
        results.append(result)
        generated_templates.append(template)
        raw_records.append({
            "sample_id": sample_id,
            "template_id": item["template_id"],
            "status": status,
            "attempts": llm_meta.get("attempts", 0),
            "elapsed_sec": llm_meta.get("elapsed_sec", 0.0),
            "error": llm_meta.get("error"),
            "raw_outputs": llm_meta.get("raw_outputs", []),
        })

        if run_dir:
            save_json(results, run_dir, "stage1_results_partial.json")
            save_json(raw_records, run_dir, "stage1_llm_raw_partial.json")

    # Compute accuracy
    if logger:
        logger.info("Computing extraction accuracy...")
    metrics = compute_accuracy(results, logger_ref=logger)

    # Save results
    if run_dir:
        save_json(results, run_dir, "stage1_results.json")
        save_json(metrics, run_dir, "stage1_accuracy.json")
        save_markdown(generate_accuracy_report(metrics), run_dir, "stage1_accuracy_report.md")
        save_json(generated_templates, run_dir, "stage1_generated_templates.json")
        save_json(raw_records, run_dir, "stage1_llm_raw.json")
        save_json(failures, run_dir, "stage1_failures.json")

        # Save extracted intent fields for Stage 2 retrieval
        intent_fields = []
        for r in results:
            t = r["generated_template"]
            intent_fields.append({
                "sample_id": r["sample_id"],
                "template_id": r["template_id"],
                "status": r.get("status", "ok"),
                "description": t.get("description", ""),
                "scenario": t.get("scenario", ""),
                "topology_summary": t.get("topology_summary", ""),
                "title": t.get("title", ""),
                "zone_names": [z.get("name", "") for z in t.get("zones", [])],
                "zone_types": [z.get("zone_type", "") for z in t.get("zones", [])],
                "node_names": [n.get("name", "") for n in t.get("nodes", [])],
                "node_types": [n.get("normalized_type", "") for n in t.get("nodes", [])],
            })
        save_json(intent_fields, run_dir, "stage1_extracted_intents.json")

        if logger:
            logger.info(f"Results saved to: {run_dir}")

    return results, metrics


if __name__ == "__main__":
    from logging_utils import create_run_directory

    BASE_DIR = Path(__file__).resolve().parent
    TRAIN_JSON = BASE_DIR / "../dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"

    run_dir = create_run_directory()
    log = setup_logger("stage1", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info("Starting Stage 1: Intent Analysis")

    results, metrics = analyze_intents_from_json(TRAIN_JSON, run_dir=run_dir, logger_ref=log)

    log.info(f"Completed. Scenario accuracy: {metrics['scenario_accuracy']:.1%}")
    log.info(f"Avg zone type recall: {metrics['avg_zone_type_recall']:.1%}")
    log.info(f"Avg node type recall: {metrics['avg_node_type_recall']:.1%}")
