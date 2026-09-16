#!/usr/bin/env python3
"""Compute Full TopoIntent ablation-row metrics from feedback outputs."""

import argparse
import csv
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / "dataset/topology/metadata_process/Stage4_synthetic_intent"
DATASET_FILES = {
    "test": DATASET_DIR / "Stage4_synthetic_intent_test.json",
    "train": DATASET_DIR / "Stage4_synthetic_intent_train.json",
}
OUTPUT_DIR = PROJECT_ROOT.parents[1] / "outputs" / "original-metric-tools"


REPORT_RE = re.compile(r"policy_test_report_generated_(?P<sample>.+)_(?P<ig>IG[23])\.json$")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def pct(num, den):
    return num / den if den else None


def avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def slug(text):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_")
    return text[:90] or "sample"


def load_ground_truth(dataset_name, dataset_file=None):
    path = Path(dataset_file).expanduser().resolve() if dataset_file else DATASET_FILES[dataset_name]
    data = load_json(path)
    out = {}
    for item in data.get("items", []):
        out[item["sample_id"]] = (item.get("ground_truth", {}) or {}).get("topology_template", {})
    return path, out


def template_metrics(pred, gt):
    pred = pred or {}
    gt = gt or {}
    gt_zones = {z.get("zone_type", "") for z in gt.get("zones", []) or [] if z.get("zone_type")}
    pred_zones = {z.get("zone_type", "") for z in pred.get("zones", []) or [] if z.get("zone_type")}
    gt_nodes = {n.get("normalized_type", "") for n in gt.get("nodes", []) or [] if n.get("normalized_type")}
    pred_nodes = {n.get("normalized_type", "") for n in pred.get("nodes", []) or [] if n.get("normalized_type")}
    return {
        "zone_type_recall": pct(len(gt_zones & pred_zones), len(gt_zones)),
        "node_type_recall": pct(len(gt_nodes & pred_nodes), len(gt_nodes)),
    }


def parse_report_identity(path):
    match = REPORT_RE.match(path.name)
    if not match:
        return None, None
    return match.group("sample"), match.group("ig")


def collect_stage4_rows(feedback_run_dir, gt_by_sample):
    rows = []
    stage4_files = sorted((feedback_run_dir / "samples").glob("*/stage4_feedback/*_output.json"))
    if not stage4_files:
        raise SystemExit(f"No feedback Stage4 outputs found under {feedback_run_dir / 'samples'}")

    for path in stage4_files:
        data = load_json(path)
        sample_id = data.get("sample_id") or path.parents[1].name
        ig = data.get("ig_level") or path.stem.split("_")[0]
        gt = gt_by_sample.get(sample_id, {})
        metrics = template_metrics(data.get("final_topology", {}), gt)
        controls = (data.get("compliance_check", {}) or {}).get("controls", []) or []
        satisfied = sum(1 for c in controls if c.get("status") == "satisfied")
        rows.append({
            "sample_id": sample_id,
            "ig_level": ig,
            "stage4_output": str(path),
            "zone_type_recall": metrics["zone_type_recall"],
            "node_type_recall": metrics["node_type_recall"],
            "controls_satisfied": satisfied,
            "controls_total": len(controls),
            "cis_satisfaction_rate": pct(satisfied, len(controls)) if controls else None,
        })
    return rows


def collect_stage5_rows(feedback_reports_dir):
    rows = []
    reports = sorted(Path(feedback_reports_dir).expanduser().resolve().rglob("policy_test_report_generated_*_IG*.json"))
    if not reports:
        raise SystemExit(f"No feedback Stage5 reports found under {feedback_reports_dir}")
    for path in reports:
        sample_id, ig = parse_report_identity(path)
        if not sample_id:
            continue
        report = load_json(path)
        post = report.get("post_acl_policy_summary", {}) or report.get("summary", {}) or {}
        rows.append({
            "sample_id": sample_id,
            "ig_level": ig,
            "report_file": str(path),
            "post_acl_passed": int(post.get("passed", 0) or 0),
            "post_acl_executed": int(post.get("executed", post.get("total", 0)) or 0),
        })
    return rows


def summarize(stage4_rows, stage5_rows):
    controls_total = sum(r["controls_total"] for r in stage4_rows)
    controls_satisfied = sum(r["controls_satisfied"] for r in stage4_rows)
    post_executed = sum(r["post_acl_executed"] for r in stage5_rows)
    post_passed = sum(r["post_acl_passed"] for r in stage5_rows)
    return {
        "variant": "Full TopoIntent",
        "samples_stage4_records": len(stage4_rows),
        "samples_stage5_reports": len(stage5_rows),
        "zone_type_recall": avg(r["zone_type_recall"] for r in stage4_rows),
        "node_type_recall": avg(r["node_type_recall"] for r in stage4_rows),
        "cis_satisfaction_rate": round(controls_satisfied / controls_total, 4) if controls_total else None,
        "post_acl_pass_rate": round(post_passed / post_executed, 4) if post_executed else None,
        "post_acl_passed": post_passed,
        "post_acl_executed": post_executed,
    }


def main():
    parser = argparse.ArgumentParser(description="Compute Full TopoIntent ablation row")
    parser.add_argument("--feedback-run-dir", type=str, required=True)
    parser.add_argument("--feedback-reports-dir", type=str, required=True)
    parser.add_argument("--dataset", choices=sorted(DATASET_FILES), default="test")
    parser.add_argument("--dataset-file", type=str, default=None)
    parser.add_argument("--output-prefix", type=str, default=None)
    args = parser.parse_args()

    feedback_run_dir = Path(args.feedback_run_dir).expanduser().resolve()
    dataset_path, gt_by_sample = load_ground_truth(args.dataset, args.dataset_file)
    stage4_rows = collect_stage4_rows(feedback_run_dir, gt_by_sample)
    stage5_rows = collect_stage5_rows(args.feedback_reports_dir)
    summary = summarize(stage4_rows, stage5_rows)

    prefix = args.output_prefix or feedback_run_dir.name
    output = {
        "metric": "full_topointent_ablation_row",
        "feedback_run_dir": str(feedback_run_dir),
        "feedback_reports_dir": str(Path(args.feedback_reports_dir).expanduser().resolve()),
        "dataset_file": str(dataset_path),
        "summary": summary,
    }

    write_json(OUTPUT_DIR / f"{prefix}_full_topointent_summary.json", output)
    write_csv(OUTPUT_DIR / f"{prefix}_full_topointent_stage4_details.csv", stage4_rows)
    write_csv(OUTPUT_DIR / f"{prefix}_full_topointent_stage5_details.csv", stage5_rows)

    print(json.dumps(output, indent=2, ensure_ascii=False))
    print()
    print(
        "Full TopoIntent & "
        f"{summary['zone_type_recall'] * 100:.1f}\\% & "
        f"{summary['node_type_recall'] * 100:.1f}\\% & "
        f"{summary['cis_satisfaction_rate'] * 100:.1f}\\% & "
        f"{summary['post_acl_pass_rate'] * 100:.1f}\\% \\\\"
    )


if __name__ == "__main__":
    main()
