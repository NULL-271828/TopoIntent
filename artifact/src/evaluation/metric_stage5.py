#!/usr/bin/env python3
"""Compute Stage 5 executable validation metrics from WSL Mininet reports."""

import argparse
import csv
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
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


def parse_report_identity(path):
    match = REPORT_RE.match(path.name)
    if not match:
        return path.parent.parent.name, "UNKNOWN"
    return match.group("sample"), match.group("ig")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def sum_diagnosis_failures(diagnosis):
    diagnosis = diagnosis or {}
    keys = [
        "topology_failures",
        "acl_failures",
        "skipped_tests",
        "node_link_failures",
        "acl_conflicts",
    ]
    return sum(len(diagnosis.get(key, []) or []) for key in keys)


def compute_one(path, setting):
    data = load_json(path)
    sample_id, ig_level = parse_report_identity(path)

    node = data.get("node_link_summary", {}) or {}
    pre = data.get("pre_acl_policy_summary", {}) or {}
    post = data.get("post_acl_policy_summary", {}) or {}
    diagnosis = data.get("diagnosis", {}) or {}

    node_total = int(node.get("total", 0) or 0)
    node_reachable = int(node.get("reachable", 0) or 0)
    pre_executed = int(pre.get("executed", pre.get("total", 0)) or 0)
    pre_passed = int(pre.get("passed", 0) or 0)
    post_executed = int(post.get("executed", post.get("total", 0)) or 0)
    post_passed = int(post.get("passed", 0) or 0)

    diag_failures = sum_diagnosis_failures(diagnosis)
    repair_required = bool(diagnosis.get("repair_required")) or diag_failures > 0

    return {
        "setting": setting,
        "sample_id": sample_id,
        "ig_level": ig_level,
        "report_file": str(path),
        "node_link_total": node_total,
        "node_link_reachable": node_reachable,
        "node_link_reachability": ratio(node_reachable, node_total),
        "avg_latency_ms": float(node.get("avg_latency_ms", 0.0) or 0.0),
        "pre_acl_total": int(pre.get("total", 0) or 0),
        "pre_acl_executed": pre_executed,
        "pre_acl_passed": pre_passed,
        "pre_acl_failed": int(pre.get("failed", 0) or 0),
        "pre_acl_pass_rate": ratio(pre_passed, pre_executed),
        "post_acl_total": int(post.get("total", 0) or 0),
        "post_acl_executed": post_executed,
        "post_acl_passed": post_passed,
        "post_acl_failed": int(post.get("failed", 0) or 0),
        "post_acl_pass_rate": ratio(post_passed, post_executed),
        "diagnostic_failures": diag_failures,
        "topology_failures": len(diagnosis.get("topology_failures", []) or []),
        "acl_failures": len(diagnosis.get("acl_failures", []) or []),
        "skipped_tests": len(diagnosis.get("skipped_tests", []) or []),
        "node_link_failures": len(diagnosis.get("node_link_failures", []) or []),
        "acl_conflicts": len(diagnosis.get("acl_conflicts", []) or []),
        "repair_required": repair_required,
    }


def weighted_rate(rows, num_key, den_key):
    denominator = sum(r[den_key] for r in rows)
    numerator = sum(r[num_key] for r in rows)
    return ratio(numerator, denominator)


def weighted_latency(rows):
    total_links = sum(r["node_link_total"] for r in rows)
    if not total_links:
        return 0.0
    return sum(r["avg_latency_ms"] * r["node_link_total"] for r in rows) / total_links


def summarize(rows):
    summary = {}
    for setting in sorted({r["setting"] for r in rows}):
        for ig in ("IG2", "IG3"):
            group = [r for r in rows if r["setting"] == setting and r["ig_level"] == ig]
            if not group:
                continue
            key = f"{ig} {setting}"
            summary[key] = {
                "setting": setting,
                "ig_level": ig,
                "samples": len(group),
                "node_link_reachability": round(weighted_rate(group, "node_link_reachable", "node_link_total"), 4),
                "pre_acl_pass_rate": round(weighted_rate(group, "pre_acl_passed", "pre_acl_executed"), 4),
                "post_acl_pass_rate": round(weighted_rate(group, "post_acl_passed", "post_acl_executed"), 4),
                "avg_latency_ms": round(weighted_latency(group), 4),
                "diagnostic_failures": sum(r["diagnostic_failures"] for r in group),
                "repair_required_samples": sum(1 for r in group if r["repair_required"]),
            }
    return summary


def latex_percent(value):
    return f"{value * 100:.1f}\\%"


def build_latex_rows(summary):
    rows = []
    for key in ("IG2 Initial", "IG2 After Feedback", "IG3 Initial", "IG3 After Feedback"):
        if key not in summary:
            rows.append(f"{key} & TBD & TBD & TBD & TBD & TBD & TBD \\\\")
            continue
        item = summary[key]
        rows.append(
            f"{key} & {latex_percent(item['node_link_reachability'])} "
            f"& {latex_percent(item['pre_acl_pass_rate'])} "
            f"& {latex_percent(item['post_acl_pass_rate'])} "
            f"& {item['avg_latency_ms']:.3f} "
            f"& {item['diagnostic_failures']} "
            f"& {item['repair_required_samples']}/{item['samples']} \\\\"
        )
    return "\n".join(rows)


def build_latex_table(summary):
    return "\n".join([
        "\\begin{table*}[t]",
        "\\caption{Stage~5 Executable Validation and Diagnostic Feedback Results(Evaluation Set, $n=14$)}",
        "\\label{tab:stage5}",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lcccccc}",
        "\\hline",
        "\\textbf{Setting} & \\textbf{Node-Link Reach.}",
        "                 & \\textbf{Pre-ACL Pass} & \\textbf{Post-ACL Pass}",
        "                 & \\textbf{Avg. Latency (ms)} & \\textbf{Diag. Failures}",
        "                 & \\textbf{Repair Required} \\\\",
        "\\hline",
        build_latex_rows(summary),
        "\\hline",
        "\\end{tabular}",
        "\\end{table*}",
        "",
    ])


def collect_reports(reports_dir):
    reports_dir = Path(reports_dir).expanduser().resolve()
    reports = sorted(reports_dir.rglob("policy_test_report_generated_*_IG*.json"))
    if not reports:
        raise SystemExit(f"No Stage5 report JSON files found under {reports_dir}")
    return reports


def main():
    parser = argparse.ArgumentParser(description="Compute Stage5 metrics from WSL reports")
    parser.add_argument("--initial-reports-dir", type=str, required=True)
    parser.add_argument("--feedback-reports-dir", type=str, default=None)
    parser.add_argument("--output-prefix", type=str, default=None)
    args = parser.parse_args()

    rows = []
    initial_reports = collect_reports(args.initial_reports_dir)
    rows.extend(compute_one(path, "Initial") for path in initial_reports)

    feedback_reports = []
    if args.feedback_reports_dir:
        feedback_reports = collect_reports(args.feedback_reports_dir)
        rows.extend(compute_one(path, "After Feedback") for path in feedback_reports)

    summary = summarize(rows)
    prefix = args.output_prefix or Path(args.initial_reports_dir).resolve().parts[-3]
    output = {
        "metric": "stage5_executable_validation",
        "initial_reports_dir": str(Path(args.initial_reports_dir).expanduser().resolve()),
        "feedback_reports_dir": str(Path(args.feedback_reports_dir).expanduser().resolve()) if args.feedback_reports_dir else None,
        "initial_reports": len(initial_reports),
        "feedback_reports": len(feedback_reports),
        "summary": summary,
    }

    write_json(OUTPUT_DIR / f"{prefix}_metric_stage5_summary.json", output)
    write_csv(OUTPUT_DIR / f"{prefix}_metric_stage5_details.csv", rows)
    latex = build_latex_table(summary)
    (OUTPUT_DIR / f"{prefix}_metric_stage5_table.tex").write_text(latex, encoding="utf-8")

    print(json.dumps(output, indent=2, ensure_ascii=False))
    print()
    print(latex)


if __name__ == "__main__":
    main()
