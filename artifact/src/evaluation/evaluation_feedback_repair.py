#!/usr/bin/env python3
"""Run Stage5 diagnostic feedback repair for an evaluation run."""

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

from logging_utils import setup_logger
from Stage4_utils import build_structural_findings, process_stage5_diagnostic_repair
from Stage5_1_utils import generate_mininet_script_from_xml_file
from Stage5_2_visualize import build_variant_payload, write_html


REPORT_RE = re.compile(r"policy_test_report_generated_(?P<sample>.+)_(?P<ig>IG[23])\.json$")


def slug(text):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_")
    return text[:90] or "sample"


def now():
    return datetime.now().isoformat(timespec="seconds")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def parse_report_identity(path):
    match = REPORT_RE.match(path.name)
    if not match:
        raise ValueError(f"Cannot parse sample/IG from report name: {path.name}")
    return match.group("sample"), match.group("ig")


def collect_reports(reports_dir):
    reports = sorted(Path(reports_dir).expanduser().resolve().rglob("policy_test_report_generated_*_IG*.json"))
    if not reports:
        raise SystemExit(f"No Stage5 reports found under {reports_dir}")
    return reports


def diagnosis_needs_repair(report):
    diagnosis = report.get("diagnosis", {}) if isinstance(report.get("diagnosis", {}), dict) else {}
    if diagnosis.get("repair_required"):
        return True
    for key in ("topology_failures", "acl_failures", "skipped_tests", "node_link_failures", "acl_conflicts"):
        if diagnosis.get(key):
            return True
    return False


def copy_xml_to_feedback_dir(xml_file, target_dir):
    xml_file = Path(xml_file)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / xml_file.name
    if xml_file.resolve() != target.resolve():
        shutil.copy2(xml_file, target)
    return target


def resolve_prior_xml(prior, original_run_dir, sample_id, ig):
    xml_file = Path(prior.get("xml_file", ""))
    if xml_file.exists():
        return xml_file

    candidates = [
        original_run_dir / "samples" / slug(sample_id) / "stage4" / "xml" / ig / xml_file.name,
        original_run_dir / "samples" / slug(sample_id) / "stage4" / "xml" / ig / f"generated_{sample_id}_{ig}.xml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    matches = sorted((original_run_dir / "samples" / slug(sample_id) / "stage4").glob(f"**/*_{ig}.xml"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Cannot resolve prior XML for {sample_id} {ig}; recorded path={xml_file}")


def package_mininet_scripts(feedback_dir, records):
    package_dir = feedback_dir / "mininet_scripts_package"
    package_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for record in records:
        script = record.get("mininet_script")
        if not script:
            continue
        src = Path(script)
        rel_dir = Path(slug(record["sample_id"])) / "mininet_scripts" / record["ig_level"]
        dst_dir = package_dir / rel_dir
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        entries.append({
            "sample_id": record["sample_id"],
            "ig_level": record["ig_level"],
            "script": str(dst),
            "relative_script": str(rel_dir / src.name),
        })

    write_json(package_dir / "script_manifest.json", entries)
    readme = "\n".join([
        "# Feedback-Repaired Mininet Evaluation Scripts",
        "",
        "Run these scripts in WSL/Linux with root privileges. Copy generated reports back to:",
        "",
        f"```text\n{feedback_dir / 'reports_from_wsl' / 'all_reports'}\n```",
        "",
    ])
    (package_dir / "README_RUN_IN_WSL.md").write_text(readme, encoding="utf-8")

    zip_path = feedback_dir / "mininet_scripts_package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir))
    return entries, zip_path


def run_one(report_file, original_run_dir, feedback_dir, logger, max_repair_rounds):
    sample_id, ig = parse_report_identity(report_file)
    sample_dir = feedback_dir / "samples" / slug(sample_id)
    stage4_dir = sample_dir / "stage4_feedback"
    stage5_dir = sample_dir / "stage5_1_feedback"
    stage4_dir.mkdir(parents=True, exist_ok=True)
    stage5_dir.mkdir(parents=True, exist_ok=True)

    prior_stage4_file = original_run_dir / "samples" / slug(sample_id) / "stage4" / f"{ig}_output.json"
    if not prior_stage4_file.exists():
        raise FileNotFoundError(f"Missing prior Stage4 output: {prior_stage4_file}")

    prior = load_json(prior_stage4_file)
    report = load_json(report_file)
    reference_topology = None
    if ig == "IG3":
        ig2_file = original_run_dir / "samples" / slug(sample_id) / "stage4" / "IG2_output.json"
        if ig2_file.exists():
            reference_topology = (load_json(ig2_file).get("final_topology") or {})
    structural_findings = build_structural_findings(
        prior.get("final_topology") or {},
        ig,
        reference_topology=reference_topology,
    )
    needs_repair = diagnosis_needs_repair(report) or bool(structural_findings)

    if needs_repair:
        logger.info(f"{sample_id} {ig}: applying Stage5 diagnostic repair")
        repaired = process_stage5_diagnostic_repair(
            stage4_result=prior,
            stage5_report=report,
            ig_level=ig,
            run_dir=stage4_dir,
            max_repair_rounds=max_repair_rounds,
            logger_ref=logger,
            reference_topology=reference_topology,
        )
    else:
        logger.info(f"{sample_id} {ig}: no repair required; reusing prior XML")
        prior_xml = resolve_prior_xml(prior, original_run_dir, sample_id, ig)
        xml_target = copy_xml_to_feedback_dir(prior_xml, stage4_dir / "xml" / ig)
        repaired = dict(prior)
        repaired["xml_file"] = str(xml_target)
        repaired["stage5_diagnostic_summary"] = report.get("summary", {})
        repaired["stage5_feedback_reused_prior_topology"] = True

    stage4_output = write_json(stage4_dir / f"{ig}_output.json", repaired)
    xml_file = Path(repaired["xml_file"])

    mininet_dir = stage5_dir / "mininet_scripts" / ig
    mininet_manifest = generate_mininet_script_from_xml_file(xml_file, mininet_dir)
    mininet_manifest_file = write_json(stage5_dir / f"{ig}_manifest.json", mininet_manifest)

    return {
        "sample_id": sample_id,
        "ig_level": ig,
        "repair_required": needs_repair,
        "stage5_report": str(report_file),
        "prior_stage4_output": str(prior_stage4_file),
        "feedback_stage4_output": str(stage4_output),
        "xml_file": str(xml_file),
        "stage5_1_manifest": str(mininet_manifest_file),
        "mininet_script": mininet_manifest["script_file"],
        "expected_stage5_report": mininet_manifest["expected_report_file"],
    }


def build_html_outputs(feedback_dir, records):
    by_sample = {}
    for record in records:
        by_sample.setdefault(record["sample_id"], []).append(Path(record["xml_file"]))
    html_records = []
    for sample_id, xml_files in by_sample.items():
        stage5_2_dir = feedback_dir / "samples" / slug(sample_id) / "stage5_2_feedback"
        variants = build_variant_payload(xml_files)
        html_file = write_html(variants, stage5_2_dir)
        html_records.append({
            "sample_id": sample_id,
            "html_file": str(html_file),
            "ig_levels": [v["ig_level"] for v in variants],
        })
    return html_records


def main():
    parser = argparse.ArgumentParser(description="Run Stage5 diagnostic feedback repair for an evaluation run")
    parser.add_argument("--run-dir", type=str, required=True, help="Original evaluation run directory")
    parser.add_argument("--reports-dir", type=str, required=True, help="Initial WSL all_reports directory")
    parser.add_argument("--output-name", type=str, default=None)
    parser.add_argument("--max-repair-rounds", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-visualize", action="store_true")
    args = parser.parse_args()

    original_run_dir = Path(args.run_dir).expanduser().resolve()
    reports = collect_reports(args.reports_dir)
    if args.limit:
        reports = reports[: args.limit]

    output_name = args.output_name or f"{original_run_dir.name}_feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    feedback_dir = original_run_dir.parent / output_name
    feedback_dir.mkdir(parents=True, exist_ok=False)
    (feedback_dir / "reports_from_wsl" / "all_reports").mkdir(parents=True, exist_ok=True)

    log = setup_logger("evaluation_feedback_repair", feedback_dir)
    log.info(f"Original run: {original_run_dir}")
    log.info(f"Reports dir: {Path(args.reports_dir).expanduser().resolve()}")
    log.info(f"Feedback run: {feedback_dir}")
    log.info(f"Reports: {len(reports)}")

    started = time.time()
    records = []
    failures = []
    for idx, report_file in enumerate(reports, 1):
        try:
            sample_id, ig = parse_report_identity(report_file)
            log.info(f"[{idx}/{len(reports)}] {sample_id} {ig}")
            records.append(run_one(report_file, original_run_dir, feedback_dir, log, args.max_repair_rounds))
            write_json(feedback_dir / "feedback_records_partial.json", records)
        except Exception as exc:
            failure = {
                "report_file": str(report_file),
                "type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            log.exception(f"Failed feedback repair for {report_file}: {exc}")
            write_json(feedback_dir / "failures_partial.json", failures)

    html_records = [] if args.no_visualize else build_html_outputs(feedback_dir, records)
    script_entries, zip_path = package_mininet_scripts(feedback_dir, records)

    manifest = {
        "mode": "stage5_feedback_repair",
        "original_run_dir": str(original_run_dir),
        "initial_reports_dir": str(Path(args.reports_dir).expanduser().resolve()),
        "feedback_run_dir": str(feedback_dir),
        "created_at": now(),
        "elapsed_sec": round(time.time() - started, 3),
        "records": len(records),
        "failures": len(failures),
        "repair_required_records": sum(1 for r in records if r["repair_required"]),
        "generated_scripts": len(script_entries),
        "outputs": {
            "records": str(feedback_dir / "feedback_records.json"),
            "failures": str(feedback_dir / "failures.json"),
            "html_records": str(feedback_dir / "html_records.json"),
            "mininet_scripts_package": str(feedback_dir / "mininet_scripts_package"),
            "mininet_scripts_zip": str(zip_path),
            "reports_from_wsl": str(feedback_dir / "reports_from_wsl" / "all_reports"),
        },
    }
    write_json(feedback_dir / "feedback_records.json", records)
    write_json(feedback_dir / "failures.json", failures)
    write_json(feedback_dir / "html_records.json", html_records)
    write_json(feedback_dir / "feedback_manifest.json", manifest)

    log.info(f"Completed feedback repair: records={len(records)}, failures={len(failures)}, scripts={len(script_entries)}")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
