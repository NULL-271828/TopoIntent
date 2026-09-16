#!/usr/bin/env python3
"""
Single-intent TopoIntent runtime.

This is the user-facing path, not an experiment evaluator. It accepts one
natural-language intent, writes one auditable run directory, and stops at the
WSL/Mininet boundary after generating validation scripts. A later invocation can
feed a Stage 5 report back into Stage 4 for diagnostic repair.
"""

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qdrant_client import QdrantClient

from logging_utils import LOGS_BASE, save_json, setup_logger
from Stage1_utils import analyze_intent
from Stage2_utils import QDRANT_URL, retrieve_for_template
from Stage3_utils import load_cis_requirements, load_train_templates, process_single_sample
from Stage4_utils import build_structural_findings, process_single_stage4_sample, process_stage5_diagnostic_repair
from Stage5_1_utils import generate_mininet_script_from_xml_file
from Stage5_2_visualize import build_variant_payload, write_html


USER_RUNS_DIR = LOGS_BASE / "user_runs"


def slug(text):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_")
    return text[:80] or "run"


def now():
    return datetime.now().isoformat(timespec="seconds")


def make_user_run_dir(run_id):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = USER_RUNS_DIR / f"{slug(run_id)}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def ensure_dirs(run_dir):
    for name in ("stage1", "stage2", "stage3", "stage4", "stage5_1", "stage5_feedback", "stage5_2"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def update_manifest(run_dir, **updates):
    manifest_path = run_dir / "run_manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    manifest.update(updates)
    manifest["updated_at"] = now()
    write_json(manifest_path, manifest)
    return manifest


def ig_levels_from_arg(ig):
    return ["IG2", "IG3"] if ig == "all" else [ig]


def copy_xml_to_stage_dir(xml_file, stage_dir, ig):
    xml_file = Path(xml_file)
    target_dir = stage_dir / "xml" / ig
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / xml_file.name
    if xml_file.resolve() != target.resolve():
        shutil.copy2(xml_file, target)
    return target


def write_wsl_readme(stage5_dir, manifests):
    lines = [
        "# Run Mininet Validation in WSL",
        "",
        "Run the generated validation script(s) with root privileges in WSL/Linux.",
        "Copy the resulting `policy_test_report_*.json` back to this run directory,",
        "then invoke `run.py --run-dir <run_dir> --stage5-report <report.json>` for feedback repair if needed.",
        "",
        "Example:",
        "",
        "```bash",
    ]
    for item in manifests:
        script_path = Path(item["script_file"])
        try:
            script_dir = script_path.parent.relative_to(stage5_dir)
        except ValueError:
            script_dir = script_path.parent
        lines.append(f"cd {script_dir}")
        lines.append(f"sudo python3 {script_path.name}")
        lines.append("cd - >/dev/null")
    lines.extend(["```", ""])
    readme = stage5_dir / "README_RUN_IN_WSL.md"
    readme.write_text("\n".join(lines), encoding="utf-8")
    return readme


def build_initial_manifest(run_dir, args, ig_levels):
    sample_id = slug(args.run_id)
    input_data = {
        "run_id": args.run_id,
        "intent": args.intent,
        "sample_id": sample_id,
        "ig_levels": ig_levels,
        "created_at": now(),
    }
    write_json(run_dir / "input.json", input_data)
    manifest = {
        "run_id": args.run_id,
        "sample_id": sample_id,
        "intent": args.intent,
        "ig_levels": ig_levels,
        "status": "running",
        "created_at": input_data["created_at"],
        "updated_at": input_data["created_at"],
        "run_dir": str(run_dir),
        "outputs": {},
        "stage5_feedback": {
            "enabled": False,
            "report_file": None,
            "repair_required": None,
            "repaired_outputs": {},
        },
    }
    write_json(run_dir / "run_manifest.json", manifest)
    return manifest


def run_initial(args):
    run_dir = make_user_run_dir(args.run_id)
    ensure_dirs(run_dir)
    log = setup_logger("run", run_dir)
    ig_levels = ig_levels_from_arg(args.ig)
    sample_id = slug(args.run_id)
    build_initial_manifest(run_dir, args, ig_levels)
    log.info(f"Run directory: {run_dir}")
    log.info(f"IG levels: {ig_levels}")

    started = time.time()
    outputs = {}

    stage1_dir = run_dir / "stage1"
    stage1_log = setup_logger("stage1", stage1_dir)
    stage1_template, stage1_meta = analyze_intent(
        args.intent,
        sample_id,
        logger_ref=stage1_log,
        return_metadata=True,
    )
    stage1_output = {"template": stage1_template, "metadata": stage1_meta}
    outputs["stage1"] = str(write_json(stage1_dir / "output.json", stage1_output))

    stage2_dir = run_dir / "stage2"
    stage2_log = setup_logger("stage2", stage2_dir)
    qdrant_client = QdrantClient(url=QDRANT_URL)
    retrieved = retrieve_for_template(qdrant_client, stage1_template, top_k=args.top_k, logger_ref=stage2_log)
    stage2_output = {
        "sample_id": sample_id,
        "retrieved_templates": retrieved,
    }
    outputs["stage2"] = str(write_json(stage2_dir / "output.json", stage2_output))

    cis_map = load_cis_requirements()
    train_templates = load_train_templates()
    html_xml_inputs = []
    stage5_manifests = []
    stage3_outputs = {}
    stage4_outputs = {}
    stage4_topologies_by_ig = {}

    for ig in ig_levels:
        stage3_dir = run_dir / "stage3"
        stage3_log = setup_logger(f"stage3_{ig}", stage3_dir)
        stage3_sample = {
            "sample_id": sample_id,
            "gt_template_id": "",
            "user_intent": args.intent,
            "stage1_template": stage1_template,
            "retrieved_templates": retrieved,
        }
        stage3_result = process_single_sample(
            stage3_sample,
            cis_map,
            train_templates,
            ig_level=ig,
            logger_ref=stage3_log,
        )
        stage3_path = write_json(stage3_dir / f"{ig}_output.json", stage3_result)
        outputs[f"stage3_{ig}"] = str(stage3_path)
        stage3_outputs[ig] = str(stage3_path)

        stage4_dir = run_dir / "stage4"
        stage4_log = setup_logger(f"stage4_{ig}", stage4_dir)
        stage4_sample = {
            "sample_id": sample_id,
            "gt_template_id": "",
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
        stage4_path = write_json(stage4_dir / f"{ig}_output.json", stage4_result)
        outputs[f"stage4_{ig}"] = str(stage4_path)
        outputs[f"xml_{ig}"] = str(copied_xml)
        stage4_outputs[ig] = str(stage4_path)
        html_xml_inputs.append(copied_xml)

        stage5_dir = run_dir / "stage5_1"
        mininet_dir = stage5_dir / "mininet_scripts" / ig
        mininet_manifest = generate_mininet_script_from_xml_file(copied_xml, mininet_dir)
        stage5_manifests.append(mininet_manifest)
        write_json(stage5_dir / f"{ig}_manifest.json", mininet_manifest)
        outputs[f"mininet_script_{ig}"] = mininet_manifest["script_file"]
        outputs[f"expected_stage5_report_{ig}"] = mininet_manifest["expected_report_file"]

    stage5_manifest_path = write_json(run_dir / "stage5_1" / "manifest.json", stage5_manifests)
    outputs["stage5_1_manifest"] = str(stage5_manifest_path)
    outputs["wsl_readme"] = str(write_wsl_readme(run_dir / "stage5_1", stage5_manifests))

    if not args.no_visualize and html_xml_inputs:
        variants = build_variant_payload(html_xml_inputs)
        html_file = write_html(variants, run_dir / "stage5_2")
        stage5_2_output = {
            "html_file": str(html_file),
            "ig_levels": [v["ig_level"] for v in variants],
        }
        outputs["html"] = str(html_file)
        outputs["stage5_2"] = str(write_json(run_dir / "stage5_2" / "output.json", stage5_2_output))

    elapsed = round(time.time() - started, 3)
    update_manifest(
        run_dir,
        status="waiting_for_stage5_report",
        elapsed_sec=elapsed,
        outputs=outputs,
        stage3_outputs=stage3_outputs,
        stage4_outputs=stage4_outputs,
    )
    log.info("Initial run completed; waiting for optional Stage5 report.")
    print(json.dumps({
        "run_dir": str(run_dir),
        "status": "waiting_for_stage5_report",
        "html": outputs.get("html"),
        "stage5_1_manifest": outputs.get("stage5_1_manifest"),
        "wsl_readme": outputs.get("wsl_readme"),
    }, indent=2, ensure_ascii=False))


def select_ig_for_report(report, explicit_ig=None):
    if explicit_ig:
        return explicit_ig
    if isinstance(report, dict):
        topology = report.get("topology", {})
        ig = report.get("ig_level") or topology.get("ig_level")
        if ig in {"IG2", "IG3"}:
            return ig
    return None


def resolve_stage4_output(manifest, ig):
    outputs = manifest.get("outputs", {})
    stage4_path = outputs.get(f"stage4_{ig}")
    if not stage4_path:
        raise ValueError(f"No Stage4 output found for {ig} in manifest.")
    return Path(stage4_path)


def run_feedback(args):
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")
    ensure_dirs(run_dir)
    log = setup_logger("run_feedback", run_dir)
    manifest = load_json(run_dir / "run_manifest.json")
    report = load_json(args.stage5_report)
    ig = select_ig_for_report(report, args.ig if args.ig != "all" else None)
    if not ig:
        ig_levels = manifest.get("ig_levels") or []
        if len(ig_levels) == 1:
            ig = ig_levels[0]
        else:
            raise SystemExit("--ig IG2 or --ig IG3 is required when the report does not identify its IG level.")

    feedback_dir = run_dir / "stage5_feedback" / ig
    feedback_dir.mkdir(parents=True, exist_ok=True)
    report_copy = feedback_dir / Path(args.stage5_report).name
    shutil.copy2(args.stage5_report, report_copy)
    write_json(feedback_dir / "input_report.json", report)

    stage4_path = resolve_stage4_output(manifest, ig)
    prior_stage4 = load_json(stage4_path)
    reference_topology = None
    if ig == "IG3":
        try:
            reference_topology = load_json(resolve_stage4_output(manifest, "IG2")).get("final_topology") or {}
        except Exception:
            reference_topology = None
    diagnosis = report.get("diagnosis", {}) if isinstance(report, dict) else {}
    structural_findings = build_structural_findings(
        prior_stage4.get("final_topology") or {},
        ig,
        reference_topology=reference_topology,
    )
    repair_required = bool(diagnosis.get("repair_required", report.get("summary", {}).get("failed", 0))) or bool(structural_findings)
    if not repair_required:
        update_manifest(
            run_dir,
            status="completed",
            stage5_feedback={
                "enabled": True,
                "report_file": str(report_copy),
                "repair_required": False,
                "repaired_outputs": {},
            },
        )
        print(json.dumps({
            "run_dir": str(run_dir),
            "status": "completed",
            "repair_required": False,
        }, indent=2, ensure_ascii=False))
        return

    stage4_log = setup_logger(f"stage5_feedback_stage4_{ig}", feedback_dir)
    repaired = process_stage5_diagnostic_repair(
        stage4_result=prior_stage4,
        stage5_report=report,
        ig_level=ig,
        run_dir=feedback_dir,
        max_repair_rounds=args.max_repair_rounds,
        logger_ref=stage4_log,
        reference_topology=reference_topology,
    )
    repaired_stage4_path = write_json(feedback_dir / "repaired_stage4_output.json", repaired)
    repaired_xml = Path(repaired["xml_file"])
    mininet_dir = feedback_dir / "repaired_mininet_scripts"
    mininet_manifest = generate_mininet_script_from_xml_file(repaired_xml, mininet_dir)
    mininet_manifest_path = write_json(feedback_dir / "repaired_stage5_1_manifest.json", mininet_manifest)

    repaired_html = None
    if not args.no_visualize:
        variants = build_variant_payload([repaired_xml])
        repaired_html = write_html(variants, run_dir / "stage5_2")

    repaired_outputs = {
        "repaired_stage4": str(repaired_stage4_path),
        "repaired_xml": str(repaired_xml),
        "repaired_mininet_script": mininet_manifest["script_file"],
        "repaired_stage5_1_manifest": str(mininet_manifest_path),
    }
    if repaired_html:
        repaired_outputs["repaired_html"] = str(repaired_html)

    update_manifest(
        run_dir,
        status="waiting_for_retest",
        stage5_feedback={
            "enabled": True,
            "report_file": str(report_copy),
            "repair_required": True,
            "ig_level": ig,
            "repaired_outputs": repaired_outputs,
        },
    )
    log.info("Feedback repair completed; waiting for retest.")
    print(json.dumps({
        "run_dir": str(run_dir),
        "status": "waiting_for_retest",
        "repair_required": True,
        "ig_level": ig,
        "repaired_outputs": repaired_outputs,
    }, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Run one TopoIntent user request")
    parser.add_argument("--intent", type=str, default=None, help="Single natural-language user intent")
    parser.add_argument("--run-id", type=str, default="user_case", help="Human-readable run identifier")
    parser.add_argument("--ig", choices=["IG2", "IG3", "all"], default="IG2")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--ignore-manual-review", action="store_true")
    parser.add_argument("--no-visualize", action="store_true")
    parser.add_argument("--run-dir", type=str, default=None, help="Existing user run directory for Stage5 feedback")
    parser.add_argument("--stage5-report", type=str, default=None, help="Stage5 report JSON to feed back into Stage4")
    args = parser.parse_args()

    if args.stage5_report:
        if not args.run_dir:
            parser.error("--stage5-report requires --run-dir")
        run_feedback(args)
        return

    if not args.intent:
        parser.error("Provide --intent for a new run, or --run-dir with --stage5-report for feedback repair.")
    run_initial(args)


if __name__ == "__main__":
    main()
