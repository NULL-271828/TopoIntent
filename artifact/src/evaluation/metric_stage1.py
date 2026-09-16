#!/usr/bin/env python3
"""Compute Stage 1 intent-analysis metrics on the reference set."""

import csv
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DATASET = PROJECT_ROOT / "dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"
STAGE1_ACCURACY = PROJECT_ROOT / "logs/stable_outputs/stage1_output/reference_train/stage1_accuracy.json"
STAGE1_GENERATED = PROJECT_ROOT / "logs/stable_outputs/stage1_output/reference_train/stage1_generated_templates.json"
OUTPUT_DIR = PROJECT_ROOT / "evaluation/metrics_outputs"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    accuracy = load_json(STAGE1_ACCURACY)
    generated = load_json(STAGE1_GENERATED)
    dataset = load_json(REFERENCE_DATASET)

    details = accuracy.get("details", [])
    rows = []
    for item in details:
        rows.append({
            "sample_id": item.get("sample_id"),
            "scenario_match": int(bool(item.get("scenario_match"))),
            "gt_scenario": item.get("gt_scenario", ""),
            "pred_scenario": item.get("pred_scenario", ""),
            "zone_type_recall": item.get("zone_type_recall"),
            "node_type_recall": item.get("node_type_recall"),
            "gt_zone_types": ";".join(item.get("gt_zone_types", [])),
            "pred_zone_types": ";".join(item.get("pred_zone_types", [])),
            "gt_node_types": ";".join(item.get("gt_node_types", [])),
            "pred_node_types": ";".join(item.get("pred_node_types", [])),
        })

    summary = {
        "metric": "stage1_intent_analysis_reference_set",
        "reference_dataset": str(REFERENCE_DATASET),
        "stage1_accuracy_source": str(STAGE1_ACCURACY),
        "stage1_generated_source": str(STAGE1_GENERATED),
        "reference_samples": len(dataset.get("items", [])),
        "generated_templates": len(generated),
        "total_samples": accuracy.get("total_samples"),
        "scenario_accuracy": accuracy.get("scenario_accuracy"),
        "zone_type_recall": accuracy.get("avg_zone_type_recall"),
        "node_type_recall": accuracy.get("avg_node_type_recall"),
    }

    write_json(OUTPUT_DIR / "metric_stage1_summary.json", summary)
    write_csv(OUTPUT_DIR / "metric_stage1_details.csv", rows)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
