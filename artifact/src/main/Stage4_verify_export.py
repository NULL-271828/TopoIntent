#!/usr/bin/env python3
"""
Stage 4: CIS verification, repair, test-case generation, and XML export.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_utils import create_run_directory, setup_logger, save_json, save_markdown
from Stage4_utils import (
    process_single_stage4_sample,
    process_stage5_diagnostic_repair,
    evaluate_stage4_results,
    generate_stage4_report,
)


def find_latest_stage3_results(logs_dir):
    """Find latest Stage 3 completion outputs for IG2 and IG3."""
    run_dirs = sorted(Path(logs_dir).glob("run_*"), key=lambda x: x.name, reverse=True)
    found = {}
    for rd in run_dirs:
        for ig in ("IG2", "IG3"):
            if ig in found:
                continue
            candidates = [
                rd / f"stage3_completion_results_{ig}.json",
                rd / f"stage3_results_{ig}.json",
                rd / f"stage3_completion_results_{ig}_partial.json",
                rd / f"stage3_results_{ig}_partial.json",
            ]
            for path in candidates:
                if path.exists():
                    found[ig] = path
                    break
        if "IG2" in found and "IG3" in found:
            break
    return found


def load_stage3_results(path):
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Stage 3 results must be a list: {path}")
    return data


def load_stage4_result(path, sample_id=None):
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        if sample_id:
            for item in data:
                if item.get("sample_id") == sample_id or item.get("final_topology", {}).get("template_id") == sample_id:
                    return item
            raise ValueError(f"Sample not found in Stage 4 results: {sample_id}")
        if len(data) != 1:
            raise ValueError("--sample-id is required when Stage 4 results contain multiple samples")
        return data[0]
    if isinstance(data, dict):
        return data
    raise ValueError(f"Stage 4 result must be a dict or list: {path}")


def load_json_file(path):
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Run Stage 4 CIS verification and XML export")
    parser.add_argument("--ig", choices=["IG2", "IG3", "all"], default="all")
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument(
        "--ignore-manual-review",
        action="store_true",
        help="Repair only unsatisfied findings, not manual_review findings.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional sample limit for smoke tests")
    parser.add_argument("--stage3-file", type=str, default=None, help="Explicit Stage 3 results file for one IG")
    parser.add_argument("--stage4-result-file", type=str, default=None, help="Prior Stage 4 result file for Stage5 diagnostic repair")
    parser.add_argument("--stage5-report", type=str, default=None, help="Stage 5 diagnostic report JSON to feed back into Stage 4 repair")
    parser.add_argument("--sample-id", type=str, default=None, help="Sample/template id when selecting from a multi-sample Stage 4 result file")
    args = parser.parse_args()

    run_dir = create_run_directory()
    log = setup_logger("stage4", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info("Starting Stage 4: CIS verification, repair, test-case generation, and XML export")

    if args.stage5_report:
        if not args.stage4_result_file:
            log.error("--stage5-report requires --stage4-result-file")
            sys.exit(1)
        if args.ig == "all":
            log.error("--stage5-report requires --ig IG2 or --ig IG3")
            sys.exit(1)
        log.info(f"Loading prior Stage 4 result from: {args.stage4_result_file}")
        prior_result = load_stage4_result(args.stage4_result_file, sample_id=args.sample_id)
        log.info(f"Loading Stage 5 diagnostic report from: {args.stage5_report}")
        stage5_report = load_json_file(args.stage5_report)
        repaired = process_stage5_diagnostic_repair(
            stage4_result=prior_result,
            stage5_report=stage5_report,
            ig_level=args.ig,
            run_dir=run_dir,
            max_repair_rounds=args.max_repair_rounds,
            logger_ref=log,
        )
        results = [repaired]
        metrics = evaluate_stage4_results(results, logger_ref=log)
        save_json(results, run_dir, f"stage4_stage5_repair_results_{args.ig}.json")
        save_json(metrics, run_dir, f"stage4_stage5_repair_evaluation_{args.ig}.json")
        save_markdown(generate_stage4_report(metrics), run_dir, f"stage4_stage5_repair_report_{args.ig}.md")
        save_json({
            "mode": "stage5_diagnostic_repair",
            "stage4_result_file": args.stage4_result_file,
            "stage5_report": args.stage5_report,
            "results_file": str(run_dir / f"stage4_stage5_repair_results_{args.ig}.json"),
            "xml_file": repaired.get("xml_file", ""),
            "metrics": metrics,
        }, run_dir, "stage4_manifest.json")
        log.info(f"Stage 5 diagnostic repair outputs saved to: {run_dir}")
        return

    base_dir = Path(__file__).resolve().parent.parent
    logs_dir = base_dir / "logs"
    stage3_files = find_latest_stage3_results(logs_dir)
    if args.stage3_file:
        if args.ig == "all":
            log.error("--stage3-file requires --ig IG2 or --ig IG3")
            sys.exit(1)
        stage3_files[args.ig] = Path(args.stage3_file)

    ig_levels = ["IG2", "IG3"] if args.ig == "all" else [args.ig]
    all_outputs = {}

    for ig in ig_levels:
        if ig not in stage3_files:
            log.error(f"No Stage 3 results found for {ig}. Run Stage 3 first.")
            continue

        stage3_file = stage3_files[ig]
        log.info(f"Loading {ig} Stage 3 results from: {stage3_file}")
        samples = load_stage3_results(stage3_file)
        if args.limit:
            samples = samples[:args.limit]
        log.info(f"Processing {len(samples)} samples for {ig}")

        results = []
        for idx, sample in enumerate(samples, 1):
            log.info(f"[{ig} {idx}/{len(samples)}] Processing: {sample.get('sample_id')}")
            result = process_single_stage4_sample(
                sample=sample,
                ig_level=ig,
                run_dir=run_dir,
                max_repair_rounds=args.max_repair_rounds,
                include_manual_review=not args.ignore_manual_review,
                logger_ref=log,
            )
            results.append(result)
            save_json(results, run_dir, f"stage4_results_{ig}_partial.json")

        metrics = evaluate_stage4_results(results, logger_ref=log)
        save_json(results, run_dir, f"stage4_results_{ig}.json")
        save_json(metrics, run_dir, f"stage4_evaluation_{ig}.json")
        save_markdown(generate_stage4_report(metrics), run_dir, f"stage4_report_{ig}.md")
        all_outputs[ig] = {
            "stage3_file": str(stage3_file),
            "results_file": str(run_dir / f"stage4_results_{ig}.json"),
            "metrics": metrics,
        }

    save_json(all_outputs, run_dir, "stage4_manifest.json")
    log.info(f"Stage 4 outputs saved to: {run_dir}")


if __name__ == "__main__":
    main()
