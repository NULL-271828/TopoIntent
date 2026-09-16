#!/usr/bin/env python3
"""
Evaluation-set generation runner.

This script runs the server-side stages for every synthetic intent in the held-
out evaluation set and stops at the WSL/Mininet boundary. It does not compute
ground-truth accuracy or execute Mininet. Its main outputs are per-sample stage
artifacts plus a package of validation scripts to run in WSL.
"""

import argparse
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

from qdrant_client import QdrantClient

from logging_utils import setup_logger
from Stage1_utils import analyze_intent
from Stage2_utils import QDRANT_URL, retrieve_for_template
from Stage3_utils import load_cis_requirements, load_train_templates, process_single_sample
from Stage4_utils import process_single_stage4_sample
from Stage5_1_utils import generate_mininet_script_from_xml_file
from Stage5_2_visualize import build_variant_payload, write_html


DATASET_DIR = PROJECT_ROOT / "dataset/topology/metadata_process/Stage4_synthetic_intent"
DATASET_FILES = {
    "test": DATASET_DIR / "Stage4_synthetic_intent_test.json",
    "train": DATASET_DIR / "Stage4_synthetic_intent_train.json",
}
EVALUATION_RUNS_DIR = PROJECT_ROOT.parents[1] / "outputs" / "full_runs"


def slug(text):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_")
    return text[:90] or "sample"


def now():
    return datetime.now().isoformat(timespec="seconds")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_run_dir(run_id):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EVALUATION_RUNS_DIR / f"{slug(run_id)}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "samples").mkdir(exist_ok=True)
    (run_dir / "mininet_scripts_package").mkdir(exist_ok=True)
    (run_dir / "reports_from_wsl").mkdir(exist_ok=True)
    return run_dir


def ig_levels_from_arg(ig):
    return ["IG2", "IG3"] if ig == "all" else [ig]


def load_dataset(args):
    dataset_path = Path(args.dataset_file).expanduser().resolve() if args.dataset_file else DATASET_FILES[args.dataset]
    data = load_json(dataset_path)
    items = data.get("items", [])
    selected = []
    for item in items:
        if args.sample_id and item.get("sample_id") != args.sample_id:
            continue
        selected.append({
            "sample_id": item["sample_id"],
            "template_id": item.get("template_id", ""),
            "user_intent": item["synthetic_user_intent"],
            "source_dataset_file": str(dataset_path),
        })
    if args.limit:
        selected = selected[:args.limit]
    return dataset_path, selected


def copy_xml_to_stage_dir(xml_file, stage_dir, ig):
    xml_file = Path(xml_file)
    target_dir = stage_dir / "xml" / ig
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / xml_file.name
    if xml_file.resolve() != target.resolve():
        shutil.copy2(xml_file, target)
    return target


def run_one_sample(item, ig_levels, run_dir, shared, args):
    sample_id = item["sample_id"]
    sample_dir = run_dir / "samples" / slug(sample_id)
    sample_dir.mkdir(parents=True, exist_ok=True)
    write_json(sample_dir / "input.json", item)

    sample_record = {
        "sample_id": sample_id,
        "template_id": item.get("template_id", ""),
        "ig_levels": ig_levels,
        "started_at": now(),
        "status": "running",
        "outputs": {},
        "ig_results": {},
        "failures": [],
    }
    write_json(sample_dir / "sample_manifest.json", sample_record)

    started = time.time()
    stage1_dir = sample_dir / "stage1"
    stage1_dir.mkdir(exist_ok=True)
    stage1_log = setup_logger(f"eval_{slug(sample_id)}_stage1", stage1_dir)
    stage1_template, stage1_meta = analyze_intent(
        item["user_intent"],
        sample_id,
        logger_ref=stage1_log,
        return_metadata=True,
    )
    stage1_output = {"template": stage1_template, "metadata": stage1_meta}
    sample_record["outputs"]["stage1"] = str(write_json(stage1_dir / "output.json", stage1_output))

    stage2_dir = sample_dir / "stage2"
    stage2_dir.mkdir(exist_ok=True)
    stage2_log = setup_logger(f"eval_{slug(sample_id)}_stage2", stage2_dir)
    retrieved = retrieve_for_template(
        shared["qdrant_client"],
        stage1_template,
        top_k=args.top_k,
        logger_ref=stage2_log,
    )
    stage2_output = {
        "sample_id": sample_id,
        "template_id": item.get("template_id", ""),
        "retrieved_templates": retrieved,
    }
    sample_record["outputs"]["stage2"] = str(write_json(stage2_dir / "output.json", stage2_output))

    html_inputs = []
    stage4_topologies_by_ig = {}
    for ig in ig_levels:
        ig_record = {"status": "running", "outputs": {}}
        sample_record["ig_results"][ig] = ig_record
        write_json(sample_dir / "sample_manifest.json", sample_record)
        try:
            stage3_dir = sample_dir / "stage3"
            stage3_dir.mkdir(exist_ok=True)
            stage3_log = setup_logger(f"eval_{slug(sample_id)}_stage3_{ig}", stage3_dir)
            stage3_sample = {
                "sample_id": sample_id,
                "gt_template_id": item.get("template_id", ""),
                "user_intent": item["user_intent"],
                "stage1_template": stage1_template,
                "retrieved_templates": retrieved,
            }
            stage3_result = process_single_sample(
                stage3_sample,
                shared["cis_map"],
                shared["train_templates"],
                ig_level=ig,
                logger_ref=stage3_log,
            )
            ig_record["outputs"]["stage3"] = str(write_json(stage3_dir / f"{ig}_output.json", stage3_result))

            stage4_dir = sample_dir / "stage4"
            stage4_dir.mkdir(exist_ok=True)
            stage4_log = setup_logger(f"eval_{slug(sample_id)}_stage4_{ig}", stage4_dir)
            stage4_sample = {
                "sample_id": sample_id,
                "gt_template_id": item.get("template_id", ""),
                "completed_topology": stage3_result["completed_topology"],
            }
            stage4_result = process_single_stage4_sample(
                sample=stage4_sample,
                ig_level=ig,
                run_dir=stage4_dir,
                max_repair_rounds=args.max_repair_rounds,
                include_manual_review=not args.ignore_manual_review,
                logger_ref=stage4_log,
                reference_topology=stage4_topologies_by_ig.get("IG2") if ig == "IG3" else None,
            )
            stage4_topologies_by_ig[ig] = stage4_result.get("final_topology", {})
            copied_xml = copy_xml_to_stage_dir(stage4_result["xml_file"], stage4_dir, ig)
            stage4_result["xml_file"] = str(copied_xml)
            ig_record["outputs"]["stage4"] = str(write_json(stage4_dir / f"{ig}_output.json", stage4_result))
            ig_record["outputs"]["xml"] = str(copied_xml)
            html_inputs.append(copied_xml)

            stage5_dir = sample_dir / "stage5_1"
            mininet_dir = stage5_dir / "mininet_scripts" / ig
            mininet_manifest = generate_mininet_script_from_xml_file(copied_xml, mininet_dir)
            ig_record["outputs"]["stage5_1_manifest"] = str(write_json(stage5_dir / f"{ig}_manifest.json", mininet_manifest))
            ig_record["outputs"]["mininet_script"] = mininet_manifest["script_file"]
            ig_record["outputs"]["expected_stage5_report"] = mininet_manifest["expected_report_file"]
            ig_record["status"] = "generated"
        except Exception as exc:
            ig_record["status"] = "failed"
            failure = {"sample_id": sample_id, "ig_level": ig, "type": type(exc).__name__, "error": str(exc)}
            ig_record["failure"] = failure
            sample_record["failures"].append(failure)
        finally:
            sample_record["ig_results"][ig] = ig_record
            write_json(sample_dir / "sample_manifest.json", sample_record)

    if html_inputs and not args.no_visualize:
        stage5_2_dir = sample_dir / "stage5_2"
        variants = build_variant_payload(html_inputs)
        html_file = write_html(variants, stage5_2_dir)
        stage5_2_output = {"html_file": str(html_file), "ig_levels": [v["ig_level"] for v in variants]}
        sample_record["outputs"]["stage5_2"] = str(write_json(stage5_2_dir / "output.json", stage5_2_output))
        sample_record["outputs"]["html"] = str(html_file)

    sample_record["status"] = "generated" if not sample_record["failures"] else "partial"
    sample_record["elapsed_sec"] = round(time.time() - started, 3)
    sample_record["completed_at"] = now()
    write_json(sample_dir / "sample_manifest.json", sample_record)
    return sample_record


def package_mininet_scripts(run_dir, records):
    package_dir = run_dir / "mininet_scripts_package"
    package_dir.mkdir(parents=True, exist_ok=True)
    script_entries = []
    for record in records:
        sample_id = record["sample_id"]
        for ig, ig_record in record.get("ig_results", {}).items():
            script = ig_record.get("outputs", {}).get("mininet_script")
            if not script:
                continue
            src = Path(script)
            rel_dir = Path(slug(sample_id)) / "mininet_scripts" / ig
            dst_dir = package_dir / rel_dir
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name
            shutil.copy2(src, dst)
            script_entries.append({
                "sample_id": sample_id,
                "ig_level": ig,
                "script": str(dst),
                "relative_script": str(rel_dir / src.name),
            })
    write_json(package_dir / "script_manifest.json", script_entries)

    readme_lines = [
        "# Mininet Evaluation Scripts",
        "",
        "Run these scripts in WSL/Linux with root privileges. Generated reports should be copied back to:",
        "",
        f"```text\n{run_dir / 'reports_from_wsl'}\n```",
        "",
        "Example:",
        "",
        "```bash",
        "find . -name 'validation_*.py' -print | sort",
        "sudo python3 <path-to-validation-script>",
        "```",
        "",
    ]
    (package_dir / "README_RUN_IN_WSL.md").write_text("\n".join(readme_lines), encoding="utf-8")

    zip_path = run_dir / "mininet_scripts_package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir))
    return script_entries, zip_path


def build_manifest(run_dir, args, dataset_path, items):
    manifest = {
        "run_id": args.run_id,
        "mode": "evaluation_generation",
        "dataset": args.dataset,
        "dataset_file": str(dataset_path),
        "sample_count": len(items),
        "ig_levels": ig_levels_from_arg(args.ig),
        "status": "running",
        "created_at": now(),
        "updated_at": now(),
        "run_dir": str(run_dir),
        "outputs": {
            "reports_from_wsl": str(run_dir / "reports_from_wsl"),
        },
    }
    write_json(run_dir / "evaluation_manifest.json", manifest)
    return manifest


def update_manifest(run_dir, **updates):
    path = run_dir / "evaluation_manifest.json"
    manifest = load_json(path)
    manifest.update(updates)
    manifest["updated_at"] = now()
    write_json(path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate evaluation-set artifacts and Mininet scripts")
    parser.add_argument("--dataset", choices=sorted(DATASET_FILES), default="test")
    parser.add_argument("--dataset-file", type=str, default=None)
    parser.add_argument("--sample-id", type=str, default=None, help="Optional single sample id for smoke tests")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ig", choices=["IG2", "IG3", "all"], default="IG2")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--ignore-manual-review", action="store_true")
    parser.add_argument("--no-visualize", action="store_true")
    args = parser.parse_args()

    args.run_id = args.run_id or f"{args.dataset}_{args.ig}_evaluation"
    dataset_path, items = load_dataset(args)
    if not items:
        raise SystemExit("No evaluation samples selected.")

    run_dir = make_run_dir(args.run_id)
    log = setup_logger("evaluation", run_dir)
    log.info(f"Evaluation run directory: {run_dir}")
    log.info(f"Dataset: {dataset_path}")
    log.info(f"Samples: {len(items)}; IG={args.ig}")
    build_manifest(run_dir, args, dataset_path, items)

    shared = {
        "qdrant_client": QdrantClient(url=QDRANT_URL),
        "cis_map": load_cis_requirements(),
        "train_templates": load_train_templates(),
    }

    records = []
    failures = []
    started = time.time()
    for idx, item in enumerate(items, 1):
        log.info(f"[{idx}/{len(items)}] Processing {item['sample_id']}")
        try:
            record = run_one_sample(item, ig_levels_from_arg(args.ig), run_dir, shared, args)
            records.append(record)
        except Exception as exc:
            failure = {
                "sample_id": item.get("sample_id"),
                "type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            log.exception(f"Failed sample {item.get('sample_id')}: {exc}")
            write_json(run_dir / "failures_partial.json", failures)

    script_entries, zip_path = package_mininet_scripts(run_dir, records)
    write_json(run_dir / "sample_records.json", records)
    write_json(run_dir / "failures.json", failures)
    update_manifest(
        run_dir,
        status="waiting_for_wsl_reports",
        success_samples=len(records),
        failed_samples=len(failures),
        generated_scripts=len(script_entries),
        elapsed_sec=round(time.time() - started, 3),
        outputs={
            "reports_from_wsl": str(run_dir / "reports_from_wsl"),
            "mininet_scripts_package": str(run_dir / "mininet_scripts_package"),
            "mininet_scripts_zip": str(zip_path),
            "sample_records": str(run_dir / "sample_records.json"),
            "failures": str(run_dir / "failures.json"),
        },
    )
    log.info(f"Completed generation: success_samples={len(records)}, failed_samples={len(failures)}, scripts={len(script_entries)}")
    print(json.dumps({
        "run_dir": str(run_dir),
        "status": "waiting_for_wsl_reports",
        "success_samples": len(records),
        "failed_samples": len(failures),
        "generated_scripts": len(script_entries),
        "mininet_scripts_zip": str(zip_path),
        "reports_from_wsl": str(run_dir / "reports_from_wsl"),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
