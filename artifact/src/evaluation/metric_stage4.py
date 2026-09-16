#!/usr/bin/env python3
"""Compute Stage 4 CIS compliance and repair metrics from evaluation outputs."""

import argparse
import csv
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVALUATION_RUNS_DIR = PROJECT_ROOT / "evaluation" / "evaluation_runs"
OUTPUT_DIR = PROJECT_ROOT.parents[1] / "outputs" / "original-metric-tools"


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


def latest_evaluation_run():
    manifests = sorted(EVALUATION_RUNS_DIR.glob("*/evaluation_manifest.json"), key=lambda p: p.parent.name)
    if not manifests:
        raise SystemExit(f"No evaluation runs found under {EVALUATION_RUNS_DIR}")
    return manifests[-1].parent


def status_is_satisfied(status):
    return str(status or "").strip().lower() == "satisfied"


def compute_one(path):
    data = load_json(path)
    sample_id = data.get("sample_id", path.parents[1].name)
    ig_level = data.get("ig_level", path.stem.split("_")[0])

    controls = data.get("compliance_check", {}).get("controls", []) or []
    total_controls = len(controls)
    after_satisfied = sum(1 for c in controls if status_is_satisfied(c.get("status")))
    after_sat_rate = after_satisfied / total_controls if total_controls else 0.0

    repair_history = data.get("repair_history", []) or []
    cis_repair_history = [
        item for item in repair_history
        if item.get("source") != "stage4_structural_normalization"
    ]
    structural_repair_history = [
        item for item in repair_history
        if item.get("source") == "stage4_structural_normalization"
    ]
    if cis_repair_history:
        first_findings = cis_repair_history[0].get("findings_before", []) or []
        before_unsatisfied = len(first_findings)
        before_satisfied = max(total_controls - before_unsatisfied, 0)
    else:
        before_satisfied = after_satisfied
        before_unsatisfied = total_controls - after_satisfied
    before_sat_rate = before_satisfied / total_controls if total_controls else 0.0

    test_cases = data.get("test_cases", {}).get("test_cases", []) or []
    allow_tests = sum(1 for tc in test_cases if str(tc.get("expected", "")).lower() == "allow")
    block_tests = sum(1 for tc in test_cases if str(tc.get("expected", "")).lower() in {"deny", "block"})

    final_topology = data.get("final_topology", {}) or {}
    links = final_topology.get("links", {}) or {}

    return {
        "sample_id": sample_id,
        "ig_level": ig_level,
        "stage4_output": str(path),
        "controls_total": total_controls,
        "cis_satisfied_before": before_satisfied,
        "cis_unsatisfied_before": before_unsatisfied,
        "cis_satisfaction_before": before_sat_rate,
        "cis_satisfied_after": after_satisfied,
        "cis_unsatisfied_after": total_controls - after_satisfied,
        "cis_satisfaction_after": after_sat_rate,
        "all_required_satisfied_after": bool(data.get("compliance_check", {}).get("all_required_satisfied")),
        "repair_rounds": len(cis_repair_history),
        "structural_normalization_rounds": len(structural_repair_history),
        "test_cases": len(test_cases),
        "allow_tests": allow_tests,
        "block_tests": block_tests,
        "zones": len(final_topology.get("zones", []) or []),
        "nodes": len(final_topology.get("nodes", []) or []),
        "zone_links": len(links.get("zone_links", []) or []),
        "node_links": len(links.get("node_links", []) or []),
    }


def avg(values):
    values = list(values)
    return round(sum(values) / len(values), 4) if values else 0.0


def summarize(rows):
    summary = {}
    for ig in sorted({row["ig_level"] for row in rows}):
        group = [row for row in rows if row["ig_level"] == ig]
        summary[ig] = {
            "samples": len(group),
            "cis_satisfaction_before_repair": avg(r["cis_satisfaction_before"] for r in group),
            "cis_satisfaction_after_repair": avg(r["cis_satisfaction_after"] for r in group),
            "all_required_satisfied_after_rate": avg(1.0 if r["all_required_satisfied_after"] else 0.0 for r in group),
            "avg_repair_rounds": avg(r["repair_rounds"] for r in group),
            "avg_test_cases": avg(r["test_cases"] for r in group),
            "avg_allow_tests": avg(r["allow_tests"] for r in group),
            "avg_block_tests": avg(r["block_tests"] for r in group),
            "avg_zones": avg(r["zones"] for r in group),
            "avg_nodes": avg(r["nodes"] for r in group),
            "avg_node_links": avg(r["node_links"] for r in group),
        }
    return summary


def collect_stage4_outputs(run_dir):
    samples_dir = run_dir / "samples"
    patterns = [
        "*/stage4/*_output.json",
        "*/stage4_feedback/*_output.json",
        "*/rag_without_repair/*/output.json",
        "*/rag_without_feedback/*/output.json",
    ]
    seen = set()
    files = []
    for pattern in patterns:
        for path in sorted(samples_dir.glob(pattern)):
            if path not in seen:
                seen.add(path)
                files.append(path)
    return files


def latex_percent(value):
    return f"{value * 100:.1f}\\%"


def build_latex_table(summary):
    lines = [
        "\\begin{table*}[t]",
        "\\caption{Stage~4 CIS Compliance Verification and Repair Results",
        "(Evaluation Set, $n=14$)}",
        "\\label{tab:stage4}",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lcccc}",
        "\\hline",
        "\\textbf{IG Level} & \\textbf{CIS Sat. (Before Repair)}",
        "                  & \\textbf{CIS Sat. (After Repair)}",
        "                  & \\textbf{Avg. Repair Rounds}",
        "                  & \\textbf{Avg. Test Cases} \\\\",
        "\\hline",
    ]
    for ig in ("IG2", "IG3"):
        if ig not in summary:
            continue
        row = summary[ig]
        lines.append(
            f"{ig} & {latex_percent(row['cis_satisfaction_before_repair'])} "
            f"& {latex_percent(row['cis_satisfaction_after_repair'])} "
            f"& {row['avg_repair_rounds']:.2f} "
            f"& {row['avg_test_cases']:.2f} \\\\"
        )
    lines.extend([
        "\\hline",
        "\\end{tabular}",
        "\\end{table*}",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Compute Stage 4 metrics from an evaluation run")
    parser.add_argument("--run-dir", type=str, default=None, help="Evaluation run directory; defaults to latest")
    parser.add_argument("--output-prefix", type=str, default=None)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else latest_evaluation_run()
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")

    stage4_files = collect_stage4_outputs(run_dir)
    if not stage4_files:
        raise SystemExit(f"No Stage4 output files found under {run_dir / 'samples'}")

    rows = [compute_one(path) for path in stage4_files]
    summary = summarize(rows)

    prefix = args.output_prefix or run_dir.name
    output = {
        "metric": "stage4_cis_compliance_and_repair",
        "run_dir": str(run_dir),
        "stage4_outputs": len(stage4_files),
        "summary": summary,
    }

    write_json(OUTPUT_DIR / f"{prefix}_metric_stage4_summary.json", output)
    write_csv(OUTPUT_DIR / f"{prefix}_metric_stage4_details.csv", rows)
    latex = build_latex_table(summary)
    (OUTPUT_DIR / f"{prefix}_metric_stage4_table.tex").write_text(latex, encoding="utf-8")

    print(json.dumps(output, indent=2, ensure_ascii=False))
    print()
    print(latex)


if __name__ == "__main__":
    main()
