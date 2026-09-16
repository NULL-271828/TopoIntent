#!/usr/bin/env python3
"""Run RAG w/o Fusion baseline: Stage1 + Stage2 Top-1 template as output."""

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

from logging_utils import setup_logger
from Stage1_utils import analyze_intent
from Stage2_utils import retrieve_for_template
from Stage3_utils import load_train_templates


DATASET_DIR = PROJECT_ROOT / "dataset/topology/metadata_process/Stage4_synthetic_intent"
DATASET_FILES = {
    "test": DATASET_DIR / "Stage4_synthetic_intent_test.json",
    "train": DATASET_DIR / "Stage4_synthetic_intent_train.json",
}
EVALUATION_RUNS_DIR = PROJECT_ROOT.parents[1] / "outputs" / "full_runs"
OUTPUT_DIR = PROJECT_ROOT.parents[1] / "outputs" / "original-metric-tools"


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


def average(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


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


def load_dataset(args):
    path = Path(args.dataset_file).expanduser().resolve() if args.dataset_file else DATASET_FILES[args.dataset]
    data = load_json(path)
    items = data.get("items", [])
    if args.sample_id:
        items = [item for item in items if item.get("sample_id") == args.sample_id]
    if args.limit:
        items = items[: args.limit]
    return path, items


def get_topology_from_train_template(train_templates, template_id):
    item = train_templates.get(template_id, {}) or {}
    return item.get("topology_template", item)


def main():
    parser = argparse.ArgumentParser(description="Run RAG w/o Fusion baseline")
    parser.add_argument("--dataset", choices=sorted(DATASET_FILES), default="test")
    parser.add_argument("--dataset-file", type=str, default=None)
    parser.add_argument("--sample-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-id", type=str, default="rag_without_fusion_test")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    dataset_path, items = load_dataset(args)
    if not items:
        raise SystemExit("No samples selected.")

    run_dir = make_run_dir(args.run_id)
    log = setup_logger("rag_without_fusion", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info(f"Dataset: {dataset_path}")
    log.info(f"Samples: {len(items)}")

    train_templates = load_train_templates()
    records = []
    metric_rows = []
    failures = []
    started = time.time()

    for idx, item in enumerate(items, 1):
        sample_id = item["sample_id"]
        sample_dir = run_dir / "samples" / slug(sample_id)
        sample_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"[{idx}/{len(items)}] Stage1+Stage2: {sample_id}")
        try:
            stage1_template, stage1_meta = analyze_intent(
                item["synthetic_user_intent"],
                sample_id,
                logger_ref=log,
                return_metadata=True,
            )
            retrieved = retrieve_for_template(
                None,
                stage1_template,
                top_k=args.top_k,
                logger_ref=log,
            )
            top1 = retrieved[0] if retrieved else {}
            top1_template_id = top1.get("template_id", "")
            output_topology = get_topology_from_train_template(train_templates, top1_template_id)
            gt_topology = (item.get("ground_truth", {}) or {}).get("topology_template", {})
            metrics = template_metrics(output_topology, gt_topology)

            stage1_path = write_json(sample_dir / "stage1_output.json", {
                "template": stage1_template,
                "metadata": stage1_meta,
            })
            retrieval_path = write_json(sample_dir / "stage2_retrieval.json", {
                "sample_id": sample_id,
                "gt_template_id": item.get("template_id", ""),
                "retrieved_templates": retrieved,
            })
            output_path = write_json(sample_dir / "rag_without_fusion_output_topology.json", output_topology)

            rank = None
            for r_idx, r in enumerate(retrieved, 1):
                if r.get("template_id") == item.get("template_id", ""):
                    rank = r_idx
                    break

            records.append({
                "sample_id": sample_id,
                "gt_template_id": item.get("template_id", ""),
                "top1_template_id": top1_template_id,
                "hit_rank": rank,
                "stage1_output": str(stage1_path),
                "stage2_retrieval": str(retrieval_path),
                "output_topology": str(output_path),
            })
            metric_rows.append({
                "sample_id": sample_id,
                "gt_template_id": item.get("template_id", ""),
                "top1_template_id": top1_template_id,
                "hit_rank": rank,
                "scenario_correct": metrics["scenario_correct"],
                "zone_type_recall": metrics["zone_type_recall"],
                "node_type_recall": metrics["node_type_recall"],
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
            log.exception(f"Failed RAG w/o Fusion for {sample_id}: {exc}")

        write_json(run_dir / "records_partial.json", records)
        write_json(run_dir / "failures_partial.json", failures)
        write_csv(run_dir / "metrics_partial.csv", metric_rows)

    summary = {
        "variant": "RAG w/o Fusion",
        "definition": "Stage1 extracts structured intent; Stage2 retrieves Top-1 template; the Top-1 retrieved template is used directly as the final output topology. No Stage3 fusion, no Stage4 repair, no Stage5 feedback.",
        "run_dir": str(run_dir),
        "dataset": args.dataset,
        "dataset_file": str(dataset_path),
        "samples": len(items),
        "success_samples": len(records),
        "failed_samples": len(failures),
        "scenario_accuracy": average(r["scenario_correct"] for r in metric_rows),
        "zone_type_recall": average(r["zone_type_recall"] for r in metric_rows),
        "node_type_recall": average(r["node_type_recall"] for r in metric_rows),
        "top1_template_match_rate": average(1 if r["hit_rank"] == 1 else 0 for r in metric_rows),
        "top3_template_match_rate": average(1 if r["hit_rank"] and r["hit_rank"] <= 3 else 0 for r in metric_rows),
        "top5_template_match_rate": average(1 if r["hit_rank"] and r["hit_rank"] <= 5 else 0 for r in metric_rows),
        "cis_satisfaction_rate": None,
        "post_acl_pass_rate": None,
        "note": "CIS satisfaction and Post-ACL pass rate are not computed for this baseline because it intentionally skips Stage4 and Stage5.",
        "elapsed_sec": round(time.time() - started, 3),
    }

    write_json(run_dir / "rag_without_fusion_summary.json", summary)
    write_json(run_dir / "records.json", records)
    write_json(run_dir / "failures.json", failures)
    write_csv(run_dir / "rag_without_fusion_metrics.csv", metric_rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT_DIR / f"{run_dir.name}_rag_without_fusion_summary.json", summary)
    write_csv(OUTPUT_DIR / f"{run_dir.name}_rag_without_fusion_metrics.csv", metric_rows)

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
