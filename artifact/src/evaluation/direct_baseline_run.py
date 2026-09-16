#!/usr/bin/env python3
"""Run the Direct baseline: raw user intent + Schema Contract -> topology JSON."""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_DIR = PROJECT_ROOT / "main"
sys.path.insert(0, str(MAIN_DIR))

from Stage1_utils import call_llm, parse_llm_json_output, validate_template
from logging_utils import setup_logger


DATASET_DIR = PROJECT_ROOT / "dataset/topology/metadata_process/Stage4_synthetic_intent"
DATASET_FILES = {
    "test": DATASET_DIR / "Stage4_synthetic_intent_test.json",
    "train": DATASET_DIR / "Stage4_synthetic_intent_train.json",
}
EVALUATION_RUNS_DIR = PROJECT_ROOT.parents[1] / "outputs" / "full_runs"
OUTPUT_DIR = PROJECT_ROOT.parents[1] / "outputs" / "original-metric-tools"


DIRECT_SCHEMA_PROMPT = """You are a network topology generation model.

Given a user's natural-language network/security intent, directly generate ONE complete topology JSON that follows the Schema Contract below.

This is a direct baseline:
- Do not retrieve templates.
- Do not mention intermediate reasoning.
- Do not output markdown.
- Output only one valid JSON object.

Schema Contract:
{
  "schema_version": "2.1",
  "template_id": "generated_<sample_id>",
  "title": "short topology title",
  "description": "1-2 sentence description of the requested topology",
  "scenario": "cloud|enterprise|higher_education|hospital|industrial|finance|government|other",
  "topology_summary": "2-3 sentence topology summary",
  "zones": [
    {
      "zone_id": "z1",
      "name": "zone name",
      "zone_type": "enterprise|dmz|data_center|campus|cloud_hub|cloud_spoke|management|security|ot|iot|external|other"
    }
  ],
  "nodes": [
    {
      "node_id": "n1",
      "name": "node name",
      "normalized_type": "router|switch|firewall|waf|load_balancer|vpn_gateway|bastion|ids_ips|siem|identity_server|wireless_controller|access_point|server|database|storage|workload|endpoint|cloud_service|ot_controller|iot_device|other",
      "zone_id": "z1"
    }
  ],
  "links": {
    "node_links": [
      {
        "link_id": "nl1",
        "source": "n1",
        "target": "n2",
        "link_type": "peering|vpn|routing|trunk|uplink|wireless|management|monitoring|data_flow|other",
        "label": "short link label"
      }
    ],
    "zone_links": [
      {
        "link_id": "zl1",
        "source": "z1",
        "target": "z2",
        "link_type": "peering|vpn|routing|trunk|uplink|wireless|management|monitoring|data_flow|other",
        "label": "short link label"
      }
    ]
  }
}

Generation requirements:
1. Include all zones, nodes, and links that are directly stated or strongly implied by the user intent.
2. Use concrete node-to-node links when a device/service/data flow is implied.
3. Zones are logical metadata, not physical devices.
4. Keep IDs unique and stable within the topology.
5. Do not include fields outside the Schema Contract.
"""


def slug(text):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_")
    return text[:90] or "sample"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def make_run_dir(run_id):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EVALUATION_RUNS_DIR / f"{slug(run_id)}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "samples").mkdir(exist_ok=True)
    return run_dir


def pct(num, den):
    return round(num / den, 4) if den else None


def template_metrics(pred, gt):
    pred = pred or {}
    gt = gt or {}
    gt_scenario = gt.get("scenario", "")
    pred_scenario = pred.get("scenario", "")
    gt_zones = {z.get("zone_type", "") for z in gt.get("zones", []) or [] if z.get("zone_type")}
    pred_zones = {z.get("zone_type", "") for z in pred.get("zones", []) or [] if z.get("zone_type")}
    gt_nodes = {n.get("normalized_type", "") for n in gt.get("nodes", []) or [] if n.get("normalized_type")}
    pred_nodes = {n.get("normalized_type", "") for n in pred.get("nodes", []) or [] if n.get("normalized_type")}
    return {
        "scenario_correct": 1 if gt_scenario and pred_scenario == gt_scenario else 0,
        "zone_type_recall": pct(len(gt_zones & pred_zones), len(gt_zones)),
        "node_type_recall": pct(len(gt_nodes & pred_nodes), len(gt_nodes)),
        "gt_zone_types": sorted(gt_zones),
        "pred_zone_types": sorted(pred_zones),
        "gt_node_types": sorted(gt_nodes),
        "pred_node_types": sorted(pred_nodes),
    }


def average(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def load_dataset(args):
    path = Path(args.dataset_file).expanduser().resolve() if args.dataset_file else DATASET_FILES[args.dataset]
    data = load_json(path)
    items = data.get("items", [])
    if args.sample_id:
        items = [item for item in items if item.get("sample_id") == args.sample_id]
    if args.limit:
        items = items[: args.limit]
    return path, items


def generate_direct_topology(item, logger):
    sample_id = item["sample_id"]
    user_intent = item["synthetic_user_intent"]
    message = (
        f"Sample ID: {sample_id}\n"
        f"User intent:\n{user_intent}\n\n"
        "Return exactly one valid JSON object following the Schema Contract."
    )
    started = time.time()
    raw = call_llm(DIRECT_SCHEMA_PROMPT, message, temperature=0.1, max_tokens=8192)
    parsed = parse_llm_json_output(raw)
    template = validate_template(parsed, sample_id=sample_id, user_intent=user_intent)
    elapsed = round(time.time() - started, 3)
    logger.info(
        f"{sample_id}: scenario={template.get('scenario')}, "
        f"zones={len(template.get('zones', []))}, nodes={len(template.get('nodes', []))}, "
        f"elapsed={elapsed}s"
    )
    return template, raw, elapsed


def main():
    parser = argparse.ArgumentParser(description="Run Direct LLM baseline on synthetic intents")
    parser.add_argument("--dataset", choices=sorted(DATASET_FILES), default="test")
    parser.add_argument("--dataset-file", type=str, default=None)
    parser.add_argument("--sample-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-id", type=str, default="direct_baseline_test")
    args = parser.parse_args()

    dataset_path, items = load_dataset(args)
    if not items:
        raise SystemExit("No samples selected.")

    run_dir = make_run_dir(args.run_id)
    log = setup_logger("direct_baseline", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info(f"Dataset: {dataset_path}")
    log.info(f"Samples: {len(items)}")

    records = []
    metric_rows = []
    failures = []
    started = time.time()

    for idx, item in enumerate(items, 1):
        sample_id = item["sample_id"]
        sample_dir = run_dir / "samples" / slug(sample_id)
        sample_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"[{idx}/{len(items)}] Direct generation: {sample_id}")
        try:
            topology, raw, elapsed = generate_direct_topology(item, log)
            gt = (item.get("ground_truth", {}) or {}).get("topology_template", {})
            metrics = template_metrics(topology, gt)
            topology_path = write_json(sample_dir / "direct_topology.json", topology)
            raw_path = sample_dir / "raw_output.txt"
            raw_path.write_text(raw, encoding="utf-8")
            record = {
                "sample_id": sample_id,
                "template_id": item.get("template_id", ""),
                "status": "ok",
                "elapsed_sec": elapsed,
                "direct_topology": str(topology_path),
                "raw_output": str(raw_path),
            }
            records.append(record)
            metric_rows.append({
                "sample_id": sample_id,
                "template_id": item.get("template_id", ""),
                "scenario_correct": metrics["scenario_correct"],
                "zone_type_recall": metrics["zone_type_recall"],
                "node_type_recall": metrics["node_type_recall"],
                "zones": len(topology.get("zones", []) or []),
                "nodes": len(topology.get("nodes", []) or []),
                "node_links": len((topology.get("links", {}) or {}).get("node_links", []) or []),
                "zone_links": len((topology.get("links", {}) or {}).get("zone_links", []) or []),
                "gt_zone_types": ";".join(metrics["gt_zone_types"]),
                "pred_zone_types": ";".join(metrics["pred_zone_types"]),
                "gt_node_types": ";".join(metrics["gt_node_types"]),
                "pred_node_types": ";".join(metrics["pred_node_types"]),
            })
        except Exception as exc:
            failure = {
                "sample_id": sample_id,
                "template_id": item.get("template_id", ""),
                "type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            log.exception(f"Failed direct baseline for {sample_id}: {exc}")

        write_json(run_dir / "records_partial.json", records)
        write_json(run_dir / "failures_partial.json", failures)
        write_csv(run_dir / "metrics_partial.csv", metric_rows)

    summary = {
        "variant": "Direct",
        "definition": "Raw user intent plus Schema Contract prompt directly generates topology JSON. No Stage1 extraction, no RAG, no fusion, no repair, and no Stage5 feedback.",
        "run_dir": str(run_dir),
        "dataset": args.dataset,
        "dataset_file": str(dataset_path),
        "samples": len(items),
        "success_samples": len(records),
        "failed_samples": len(failures),
        "scenario_accuracy": average(r["scenario_correct"] for r in metric_rows),
        "zone_type_recall": average(r["zone_type_recall"] for r in metric_rows),
        "node_type_recall": average(r["node_type_recall"] for r in metric_rows),
        "cis_satisfaction_rate": None,
        "post_acl_pass_rate": None,
        "note": "CIS satisfaction and Post-ACL pass rate are N/A for Direct because this baseline intentionally skips Stage4 and Stage5.",
        "elapsed_sec": round(time.time() - started, 3),
    }

    write_json(run_dir / "direct_baseline_summary.json", summary)
    write_json(run_dir / "records.json", records)
    write_json(run_dir / "failures.json", failures)
    write_csv(run_dir / "direct_baseline_metrics.csv", metric_rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = run_dir.name
    write_json(OUTPUT_DIR / f"{prefix}_direct_baseline_summary.json", summary)
    write_csv(OUTPUT_DIR / f"{prefix}_direct_baseline_metrics.csv", metric_rows)

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
