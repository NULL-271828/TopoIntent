#!/usr/bin/env python3
"""Recompute the end-to-end ablation table from saved outputs and a GT JSON.

This script does not call the LLM, retrieval service, or Mininet. It only reads
previously generated artifacts and recomputes metrics that depend on the current
ground-truth topology file.
"""

import argparse
import csv
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_FILE = (
    PROJECT_ROOT
    / "dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_test.json"
)
EVALUATION_RUNS_DIR = PROJECT_ROOT / "evaluation" / "evaluation_runs"
OUTPUT_DIR = PROJECT_ROOT.parents[1] / "outputs" / "original-metric-tools"

DEFAULT_DIRECT_RUN = EVALUATION_RUNS_DIR / "direct_baseline_test_20260525_234326"
DEFAULT_RAG_ONLY_RUN = EVALUATION_RUNS_DIR / "rag_without_fusion_test_20260526_003349"
DEFAULT_RAG_ABLATION_RUN = EVALUATION_RUNS_DIR / "rag_ablation_structfix_20260526_130809"
DEFAULT_FULL_RUN = EVALUATION_RUNS_DIR / "test_all_single_vector_structfix_feedback"

REPORT_RE = re.compile(r"policy_test_report_generated_(?P<sample>.+)_(?P<ig>IG[23])\.json$")


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


def slug(text):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_")
    return text[:90] or "sample"


def pct(num, den):
    return round(num / den, 4) if den else None


def avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def load_ground_truth(dataset_file):
    data = load_json(dataset_file)
    items = data.get("items", []) if isinstance(data, dict) else data
    gt = {}
    order = []
    for item in items:
        sample_id = item["sample_id"]
        order.append(sample_id)
        gt[sample_id] = {
            "template_id": item.get("template_id", ""),
            "topology": (item.get("ground_truth", {}) or {}).get("topology_template", {}),
        }
    return order, gt


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
        "gt_zone_types": ";".join(sorted(gt_zones)),
        "pred_zone_types": ";".join(sorted(pred_zones)),
        "gt_node_types": ";".join(sorted(gt_nodes)),
        "pred_node_types": ";".join(sorted(pred_nodes)),
    }


def control_counts(stage4_result):
    controls = (stage4_result.get("compliance_check", {}) or {}).get("controls", []) or []
    satisfied = sum(1 for c in controls if str(c.get("status", "")).strip().lower() == "satisfied")
    return satisfied, len(controls)


def collect_direct_rows(run_dir, sample_order, gt_by_sample):
    rows = []
    for sample_id in sample_order:
        path = run_dir / "samples" / slug(sample_id) / "direct_topology.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing Direct prediction: {path}")
        pred = load_json(path)
        metrics = template_metrics(pred, gt_by_sample[sample_id]["topology"])
        rows.append({
            "variant": "Direct",
            "sample_id": sample_id,
            "ig_level": "N/A",
            "prediction_file": str(path),
            "controls_satisfied": "",
            "controls_total": "",
            **metrics,
        })
    return rows


def collect_rag_only_rows(run_dir, sample_order, gt_by_sample):
    rows = []
    for sample_id in sample_order:
        path = run_dir / "samples" / slug(sample_id) / "rag_without_fusion_output_topology.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing RAG w/o Fusion prediction: {path}")
        pred = load_json(path)
        metrics = template_metrics(pred, gt_by_sample[sample_id]["topology"])
        rows.append({
            "variant": "RAG w/o Fusion",
            "sample_id": sample_id,
            "ig_level": "N/A",
            "prediction_file": str(path),
            "controls_satisfied": "",
            "controls_total": "",
            **metrics,
        })
    return rows


def collect_stage4_variant_rows(run_dir, variant, sample_order, gt_by_sample):
    rows = []
    for sample_id in sample_order:
        for ig in ("IG2", "IG3"):
            path = run_dir / "samples" / slug(sample_id) / variant / ig / "output.json"
            if not path.exists():
                raise FileNotFoundError(f"Missing {variant} output: {path}")
            data = load_json(path)
            metrics = template_metrics(data.get("final_topology", {}), gt_by_sample[sample_id]["topology"])
            satisfied, total = control_counts(data)
            rows.append({
                "variant": variant,
                "sample_id": sample_id,
                "ig_level": ig,
                "prediction_file": str(path),
                "controls_satisfied": satisfied,
                "controls_total": total,
                **metrics,
            })
    return rows


def collect_full_rows(run_dir, sample_order, gt_by_sample):
    rows = []
    for sample_id in sample_order:
        for ig in ("IG2", "IG3"):
            path = run_dir / "samples" / slug(sample_id) / "stage4_feedback" / f"{ig}_output.json"
            if not path.exists():
                raise FileNotFoundError(f"Missing Full TopoIntent output: {path}")
            data = load_json(path)
            metrics = template_metrics(data.get("final_topology", {}), gt_by_sample[sample_id]["topology"])
            satisfied, total = control_counts(data)
            rows.append({
                "variant": "Full TopoIntent",
                "sample_id": sample_id,
                "ig_level": ig,
                "prediction_file": str(path),
                "controls_satisfied": satisfied,
                "controls_total": total,
                **metrics,
            })
    return rows


def post_acl_rate(reports_dir):
    if reports_dir is None:
        return None, 0, 0, 0
    reports = sorted(Path(reports_dir).expanduser().resolve().rglob("policy_test_report_generated_*_IG*.json"))
    if not reports:
        raise FileNotFoundError(f"No Stage5 report JSON files found under {reports_dir}")
    passed = 0
    executed = 0
    for path in reports:
        report = load_json(path)
        post = report.get("post_acl_policy_summary", {}) or report.get("summary", {}) or {}
        passed += int(post.get("passed", 0) or 0)
        executed += int(post.get("executed", post.get("total", 0)) or 0)
    return pct(passed, executed), passed, executed, len(reports)


def summarize_rows(label, rows, post_acl=None):
    total_controls = sum(int(r["controls_total"]) for r in rows if r["controls_total"] != "")
    satisfied = sum(int(r["controls_satisfied"]) for r in rows if r["controls_satisfied"] != "")
    return {
        "variant": label,
        "records": len(rows),
        "zone_type_recall": avg(r["zone_type_recall"] for r in rows),
        "node_type_recall": avg(r["node_type_recall"] for r in rows),
        "cis_satisfaction_rate": pct(satisfied, total_controls) if total_controls else None,
        "post_acl_pass_rate": post_acl,
    }


def fmt_metric(value):
    return "--" if value is None else f"{value:.2f}"


def latex_table(summaries):
    checks = {
        "Direct": ["", "", "", "", "", "", ""],
        "RAG w/o Fusion": ["\\checkmark", "\\checkmark", "", "", "", "", ""],
        "RAG w/o Repair": ["\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", "", "", ""],
        "RAG w/o Feedback": ["\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", ""],
        "Full TopoIntent": ["\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark", "\\checkmark"],
    }
    lines = [
        "\\begin{tabular}{l ccccccc cccc}",
        "\\toprule",
        " & \\multicolumn{7}{c}{\\textbf{Pipeline Components}}",
        " & \\multicolumn{4}{c}{\\textbf{Metrics}} \\\\",
        "\\cmidrule(lr){2-8} \\cmidrule(lr){9-12}",
        "\\textbf{Variant}",
        " & \\textbf{S1} & \\textbf{S2} & \\textbf{S3}",
        " & \\textbf{Verify} & \\textbf{Repair} & \\textbf{Emul} & \\textbf{FB}",
        " & \\textbf{Zone} & \\textbf{Node} & \\textbf{CIS} & \\textbf{Post-ACL} \\\\",
        "\\midrule",
    ]
    for i, s in enumerate(summaries):
        components = checks[s["variant"]]
        suffix = " \\\\"
        if i < len(summaries) - 1:
            suffix += " \\addlinespace[3pt]"
        lines.append(
            f"{s['variant']:<16} & "
            + " & ".join(f"{c:>10}" for c in components)
            + " & "
            + " & ".join([
                fmt_metric(s["zone_type_recall"]),
                fmt_metric(s["node_type_recall"]),
                fmt_metric(s["cis_satisfaction_rate"]),
                fmt_metric(s["post_acl_pass_rate"]),
            ])
            + suffix
        )
    lines.extend(["\\bottomrule", "\\end{tabular}"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Recompute ablation table from saved outputs and GT JSON")
    parser.add_argument("--dataset-file", type=Path, default=DATASET_FILE)
    parser.add_argument("--direct-run-dir", type=Path, default=DEFAULT_DIRECT_RUN)
    parser.add_argument("--rag-only-run-dir", type=Path, default=DEFAULT_RAG_ONLY_RUN)
    parser.add_argument("--rag-ablation-run-dir", type=Path, default=DEFAULT_RAG_ABLATION_RUN)
    parser.add_argument(
        "--rag-without-repair-run-dir",
        type=Path,
        default=None,
        help="Optional run directory for only the RAG w/o Repair row; defaults to --rag-ablation-run-dir.",
    )
    parser.add_argument(
        "--rag-without-feedback-run-dir",
        type=Path,
        default=None,
        help="Optional run directory for only the RAG w/o Feedback row; defaults to --rag-ablation-run-dir.",
    )
    parser.add_argument(
        "--rag-without-feedback-reports-dir",
        type=Path,
        default=DEFAULT_RAG_ABLATION_RUN / "reports_from_wsl" / "rag_without_feedback",
    )
    parser.add_argument("--full-feedback-run-dir", type=Path, default=DEFAULT_FULL_RUN)
    parser.add_argument(
        "--full-feedback-reports-dir",
        type=Path,
        default=DEFAULT_FULL_RUN / "reports_from_wsl" / "all_reports",
    )
    parser.add_argument("--output-prefix", default="ablation_table_current_gt")
    args = parser.parse_args()

    sample_order, gt_by_sample = load_ground_truth(args.dataset_file)
    if len(sample_order) != 14:
        raise RuntimeError(f"Expected 14 held-out samples, found {len(sample_order)}")

    no_repair_run_dir = args.rag_without_repair_run_dir or args.rag_ablation_run_dir
    no_feedback_run_dir = args.rag_without_feedback_run_dir or args.rag_ablation_run_dir

    detail_rows = []
    direct_rows = collect_direct_rows(args.direct_run_dir, sample_order, gt_by_sample)
    rag_only_rows = collect_rag_only_rows(args.rag_only_run_dir, sample_order, gt_by_sample)
    no_repair_rows = collect_stage4_variant_rows(
        no_repair_run_dir, "rag_without_repair", sample_order, gt_by_sample
    )
    no_feedback_rows = collect_stage4_variant_rows(
        no_feedback_run_dir, "rag_without_feedback", sample_order, gt_by_sample
    )
    full_rows = collect_full_rows(args.full_feedback_run_dir, sample_order, gt_by_sample)

    for rows in (direct_rows, rag_only_rows, no_repair_rows, no_feedback_rows, full_rows):
        detail_rows.extend(rows)

    no_feedback_post, no_feedback_passed, no_feedback_executed, no_feedback_reports = post_acl_rate(
        args.rag_without_feedback_reports_dir
    )
    full_post, full_passed, full_executed, full_reports = post_acl_rate(args.full_feedback_reports_dir)

    summaries = [
        summarize_rows("Direct", direct_rows),
        summarize_rows("RAG w/o Fusion", rag_only_rows),
        summarize_rows("RAG w/o Repair", no_repair_rows),
        summarize_rows("RAG w/o Feedback", no_feedback_rows, no_feedback_post),
        summarize_rows("Full TopoIntent", full_rows, full_post),
    ]

    output = {
        "metric": "end_to_end_ablation_table",
        "dataset_file": str(args.dataset_file.expanduser().resolve()),
        "sample_count": len(sample_order),
        "inputs": {
            "direct_run_dir": str(args.direct_run_dir.expanduser().resolve()),
            "rag_only_run_dir": str(args.rag_only_run_dir.expanduser().resolve()),
            "rag_ablation_run_dir": str(args.rag_ablation_run_dir.expanduser().resolve()),
            "rag_without_repair_run_dir": str(no_repair_run_dir.expanduser().resolve()),
            "rag_without_feedback_run_dir": str(no_feedback_run_dir.expanduser().resolve()),
            "rag_without_feedback_reports_dir": str(args.rag_without_feedback_reports_dir.expanduser().resolve()),
            "full_feedback_run_dir": str(args.full_feedback_run_dir.expanduser().resolve()),
            "full_feedback_reports_dir": str(args.full_feedback_reports_dir.expanduser().resolve()),
        },
        "post_acl_counts": {
            "rag_without_feedback": {
                "reports": no_feedback_reports,
                "passed": no_feedback_passed,
                "executed": no_feedback_executed,
            },
            "full_topointent": {
                "reports": full_reports,
                "passed": full_passed,
                "executed": full_executed,
            },
        },
        "summary": summaries,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    write_json(OUTPUT_DIR / f"{prefix}_summary.json", output)
    write_csv(OUTPUT_DIR / f"{prefix}_details.csv", detail_rows)
    tex = latex_table(summaries)
    (OUTPUT_DIR / f"{prefix}_table.tex").write_text(tex, encoding="utf-8")

    print(json.dumps(output, indent=2, ensure_ascii=False))
    print()
    print(tex)


if __name__ == "__main__":
    main()
