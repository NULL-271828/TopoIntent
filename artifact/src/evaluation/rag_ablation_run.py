#!/usr/bin/env python3
"""Run RAG ablations with shared Stage1/2/3 outputs.

Branches:
- RAG w/o Repair: Stage4 verifier/testcase/XML with max_repair_rounds=0.
- RAG w/o Feedback: Stage4 verifier + repair, then Stage5.1 script generation.
"""

import argparse
import csv
import json
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_DIR = PROJECT_ROOT / "main"
sys.path.insert(0, str(MAIN_DIR))

from logging_utils import setup_logger
from Stage1_utils import analyze_intent
from Stage2_utils import retrieve_for_template
from Stage3_utils import load_cis_requirements, load_train_templates, process_single_sample
from Stage4_utils import process_single_stage4_sample
from Stage5_1_utils import generate_mininet_script_from_xml_file


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
    (run_dir / "mininet_scripts_package").mkdir(exist_ok=True)
    (run_dir / "reports_from_wsl" / "rag_without_feedback").mkdir(parents=True, exist_ok=True)
    return run_dir


def ig_levels_from_arg(ig):
    return ["IG2", "IG3"] if ig == "all" else [ig]


def pct(num, den):
    return round(num / den, 4) if den else None


def average(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


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
        "gt_zone_types": sorted(gt_zones),
        "pred_zone_types": sorted(pred_zones),
        "gt_node_types": sorted(gt_nodes),
        "pred_node_types": sorted(pred_nodes),
    }


def cis_satisfaction(stage4_result):
    controls = (stage4_result.get("compliance_check", {}) or {}).get("controls", []) or []
    satisfied = sum(1 for c in controls if c.get("status") == "satisfied")
    return satisfied, len(controls), pct(satisfied, len(controls)) if controls else None


def load_dataset(args):
    path = Path(args.dataset_file).expanduser().resolve() if args.dataset_file else DATASET_FILES[args.dataset]
    data = load_json(path)
    items = data.get("items", [])
    if args.sample_id:
        items = [item for item in items if item.get("sample_id") == args.sample_id]
    if args.limit:
        items = items[: args.limit]
    return path, items


def aggregate(rows):
    total_controls = sum(r["controls_total"] for r in rows)
    total_satisfied = sum(r["controls_satisfied"] for r in rows)
    return {
        "samples": len(rows),
        "zone_type_recall": average(r["zone_type_recall"] for r in rows),
        "node_type_recall": average(r["node_type_recall"] for r in rows),
        "cis_satisfaction_rate": round(total_satisfied / total_controls, 4) if total_controls else None,
        "avg_test_cases": average(r["test_cases"] for r in rows),
    }


def summarize_variant(rows, ig_levels):
    return {
        "overall": aggregate(rows),
        "by_ig": {ig: aggregate([r for r in rows if r["ig_level"] == ig]) for ig in ig_levels},
    }


def package_mininet_scripts(run_dir, script_records):
    package_dir = run_dir / "mininet_scripts_package"
    entries = []
    for record in script_records:
        src = Path(record["mininet_script"])
        rel_dir = Path(slug(record["sample_id"])) / "mininet_scripts" / record["ig_level"]
        dst_dir = package_dir / rel_dir
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        entries.append({
            "variant": "rag_without_feedback",
            "sample_id": record["sample_id"],
            "ig_level": record["ig_level"],
            "script": str(dst),
            "relative_script": str(rel_dir / src.name),
        })
    write_json(package_dir / "script_manifest.json", entries)
    readme = "\n".join([
        "# RAG w/o Feedback Mininet Scripts",
        "",
        "Run these scripts in WSL/Linux with root privileges. Copy generated reports back to:",
        "",
        f"```text\n{run_dir / 'reports_from_wsl' / 'rag_without_feedback'}\n```",
        "",
    ])
    (package_dir / "README_RUN_IN_WSL.md").write_text(readme, encoding="utf-8")

    zip_path = run_dir / "mininet_scripts_package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir))
    return entries, zip_path


def append_metric_row(rows, variant, sample_id, ig, gt_template_id, stage4_result, gt_topology):
    metrics = template_metrics(stage4_result.get("final_topology", {}), gt_topology)
    satisfied, total, cis_rate = cis_satisfaction(stage4_result)
    tests = (stage4_result.get("test_cases", {}) or {}).get("test_cases", []) or []
    rows.append({
        "variant": variant,
        "sample_id": sample_id,
        "ig_level": ig,
        "gt_template_id": gt_template_id,
        "zone_type_recall": metrics["zone_type_recall"],
        "node_type_recall": metrics["node_type_recall"],
        "controls_satisfied": satisfied,
        "controls_total": total,
        "cis_satisfaction_rate": cis_rate,
        "test_cases": len(tests),
        "gt_zone_types": ";".join(metrics["gt_zone_types"]),
        "pred_zone_types": ";".join(metrics["pred_zone_types"]),
        "gt_node_types": ";".join(metrics["gt_node_types"]),
        "pred_node_types": ";".join(metrics["pred_node_types"]),
    })


def main():
    parser = argparse.ArgumentParser(description="Run shared RAG ablation branches")
    parser.add_argument("--dataset", choices=sorted(DATASET_FILES), default="test")
    parser.add_argument("--dataset-file", type=str, default=None)
    parser.add_argument("--sample-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ig", choices=["IG2", "IG3", "all"], default="all")
    parser.add_argument("--run-id", type=str, default="rag_ablation_test")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    args = parser.parse_args()

    dataset_path, items = load_dataset(args)
    if not items:
        raise SystemExit("No samples selected.")

    run_dir = make_run_dir(args.run_id)
    log = setup_logger("rag_ablation", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info(f"Dataset: {dataset_path}")
    log.info(f"Samples: {len(items)}; IG={args.ig}")

    cis_map = load_cis_requirements()
    train_templates = load_train_templates()
    ig_levels = ig_levels_from_arg(args.ig)

    records = []
    metric_rows = []
    failures = []
    mininet_script_records = []
    started = time.time()

    for idx, item in enumerate(items, 1):
        sample_id = item["sample_id"]
        gt_template_id = item.get("template_id", "")
        sample_dir = run_dir / "samples" / slug(sample_id)
        sample_dir.mkdir(parents=True, exist_ok=True)
        gt_topology = (item.get("ground_truth", {}) or {}).get("topology_template", {})
        log.info(f"[{idx}/{len(items)}] Processing {sample_id}")

        sample_record = {
            "sample_id": sample_id,
            "gt_template_id": gt_template_id,
            "status": "running",
            "shared": {},
            "variants": {
                "rag_without_repair": {},
                "rag_without_feedback": {},
            },
            "failures": [],
        }

        try:
            stage1_template, stage1_meta = analyze_intent(
                item["synthetic_user_intent"],
                sample_id,
                logger_ref=log,
                return_metadata=True,
            )
            retrieved = retrieve_for_template(None, stage1_template, top_k=args.top_k, logger_ref=log)
            stage1_path = write_json(sample_dir / "stage1_output.json", {
                "template": stage1_template,
                "metadata": stage1_meta,
            })
            stage2_path = write_json(sample_dir / "stage2_retrieval.json", {
                "sample_id": sample_id,
                "gt_template_id": gt_template_id,
                "retrieved_templates": retrieved,
            })
            sample_record["shared"]["stage1_output"] = str(stage1_path)
            sample_record["shared"]["stage2_retrieval"] = str(stage2_path)

            no_repair_topologies_by_ig = {}
            no_feedback_topologies_by_ig = {}
            for ig in ig_levels:
                try:
                    stage3_dir = sample_dir / "stage3"
                    stage3_dir.mkdir(exist_ok=True)
                    stage3_sample = {
                        "sample_id": sample_id,
                        "gt_template_id": gt_template_id,
                        "user_intent": item["synthetic_user_intent"],
                        "stage1_template": stage1_template,
                        "retrieved_templates": retrieved,
                    }
                    stage3_result = process_single_sample(
                        stage3_sample,
                        cis_map,
                        train_templates,
                        ig_level=ig,
                        logger_ref=log,
                    )
                    stage3_path = write_json(stage3_dir / f"{ig}_output.json", stage3_result)

                    base_stage4_sample = {
                        "sample_id": sample_id,
                        "gt_template_id": gt_template_id,
                        "completed_topology": stage3_result["completed_topology"],
                    }

                    no_repair_dir = sample_dir / "rag_without_repair" / ig
                    no_repair_result = process_single_stage4_sample(
                        sample=base_stage4_sample,
                        ig_level=ig,
                        run_dir=no_repair_dir,
                        max_repair_rounds=0,
                        include_manual_review=True,
                        logger_ref=log,
                        reference_topology=no_repair_topologies_by_ig.get("IG2") if ig == "IG3" else None,
                    )
                    no_repair_topologies_by_ig[ig] = no_repair_result.get("final_topology", {})
                    no_repair_path = write_json(no_repair_dir / "output.json", no_repair_result)
                    append_metric_row(
                        metric_rows,
                        "rag_without_repair",
                        sample_id,
                        ig,
                        gt_template_id,
                        no_repair_result,
                        gt_topology,
                    )

                    no_feedback_dir = sample_dir / "rag_without_feedback" / ig
                    no_feedback_result = process_single_stage4_sample(
                        sample=base_stage4_sample,
                        ig_level=ig,
                        run_dir=no_feedback_dir,
                        max_repair_rounds=args.max_repair_rounds,
                        include_manual_review=True,
                        logger_ref=log,
                        reference_topology=no_feedback_topologies_by_ig.get("IG2") if ig == "IG3" else None,
                    )
                    no_feedback_topologies_by_ig[ig] = no_feedback_result.get("final_topology", {})
                    no_feedback_path = write_json(no_feedback_dir / "output.json", no_feedback_result)
                    append_metric_row(
                        metric_rows,
                        "rag_without_feedback",
                        sample_id,
                        ig,
                        gt_template_id,
                        no_feedback_result,
                        gt_topology,
                    )

                    stage5_dir = sample_dir / "rag_without_feedback" / ig / "stage5_1"
                    mininet_manifest = generate_mininet_script_from_xml_file(
                        no_feedback_result["xml_file"],
                        stage5_dir / "mininet_scripts",
                    )
                    manifest_path = write_json(stage5_dir / "manifest.json", mininet_manifest)
                    mininet_script_records.append({
                        "sample_id": sample_id,
                        "ig_level": ig,
                        "mininet_script": mininet_manifest["script_file"],
                        "expected_stage5_report": mininet_manifest["expected_report_file"],
                    })

                    sample_record["variants"]["rag_without_repair"][ig] = {
                        "stage3_output": str(stage3_path),
                        "stage4_output": str(no_repair_path),
                        "xml_file": no_repair_result.get("xml_file", ""),
                    }
                    sample_record["variants"]["rag_without_feedback"][ig] = {
                        "stage3_output": str(stage3_path),
                        "stage4_output": str(no_feedback_path),
                        "xml_file": no_feedback_result.get("xml_file", ""),
                        "stage5_1_manifest": str(manifest_path),
                        "mininet_script": mininet_manifest["script_file"],
                        "expected_stage5_report": mininet_manifest["expected_report_file"],
                    }
                except Exception as exc:
                    failure = {
                        "sample_id": sample_id,
                        "ig_level": ig,
                        "type": type(exc).__name__,
                        "error": str(exc),
                    }
                    failures.append(failure)
                    sample_record["failures"].append(failure)
                    log.exception(f"Failed {sample_id} {ig}: {exc}")

            sample_record["status"] = "ok" if not sample_record["failures"] else "partial"
            records.append(sample_record)
        except Exception as exc:
            failure = {
                "sample_id": sample_id,
                "ig_level": "all",
                "type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            sample_record["status"] = "failed"
            sample_record["failures"].append(failure)
            records.append(sample_record)
            log.exception(f"Failed sample {sample_id}: {exc}")

        write_json(run_dir / "records_partial.json", records)
        write_json(run_dir / "failures_partial.json", failures)
        write_csv(run_dir / "metrics_partial.csv", metric_rows)

    script_entries, zip_path = package_mininet_scripts(run_dir, mininet_script_records)
    no_repair_rows = [r for r in metric_rows if r["variant"] == "rag_without_repair"]
    no_feedback_rows = [r for r in metric_rows if r["variant"] == "rag_without_feedback"]

    summary = {
        "variant_group": "RAG ablations",
        "run_dir": str(run_dir),
        "dataset": args.dataset,
        "dataset_file": str(dataset_path),
        "ig_levels": ig_levels,
        "samples": len(items),
        "records": len(metric_rows),
        "failures": len(failures),
        "rag_without_repair": {
            "definition": "Stage1 + Stage2 + Stage3 + Stage4 verification/testcase/XML export with Stage4 repair disabled. No Stage5.",
            **summarize_variant(no_repair_rows, ig_levels),
            "post_acl_pass_rate": None,
        },
        "rag_without_feedback": {
            "definition": "Stage1 + Stage2 + Stage3 + Stage4 verification, repair, testcase/XML export, and Stage5.1 script generation. No Stage5 diagnostic feedback repair.",
            **summarize_variant(no_feedback_rows, ig_levels),
            "post_acl_pass_rate": "pending_wsl_reports",
        },
        "generated_scripts": len(script_entries),
        "mininet_scripts_zip": str(zip_path),
        "reports_from_wsl": str(run_dir / "reports_from_wsl" / "rag_without_feedback"),
        "elapsed_sec": round(time.time() - started, 3),
    }

    write_json(run_dir / "rag_ablation_summary.json", summary)
    write_json(run_dir / "records.json", records)
    write_json(run_dir / "failures.json", failures)
    write_csv(run_dir / "rag_ablation_metrics.csv", metric_rows)
    write_json(run_dir / "mininet_script_records.json", mininet_script_records)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT_DIR / f"{run_dir.name}_rag_ablation_summary.json", summary)
    write_csv(OUTPUT_DIR / f"{run_dir.name}_rag_ablation_metrics.csv", metric_rows)

    log.info(f"Completed: records={len(metric_rows)}, failures={len(failures)}, scripts={len(script_entries)}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
