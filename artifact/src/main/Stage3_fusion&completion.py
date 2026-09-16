#!/usr/bin/env python3
"""
Stage 3: Topology Fusion and IG-level Completion
Entry point for the fusion and completion pipeline.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_utils import create_run_directory, setup_logger, save_json, save_markdown
from Stage3_utils import (
    load_cis_requirements,
    load_train_templates,
    process_single_sample,
    evaluate_results,
    generate_evaluation_report,
)


def find_latest_stage_results(logs_dir):
    """Find the latest Stage 1 and Stage 2 results."""
    run_dirs = sorted(Path(logs_dir).glob("run_*"), key=lambda x: x.name, reverse=True)
    stage1_file = None
    stage2_file = None
    for rd in run_dirs:
        if not stage1_file and (rd / "stage1_extracted_intents.json").exists():
            stage1_file = rd / "stage1_extracted_intents.json"
        if not stage2_file and (rd / "stage2_retrieval_results.json").exists():
            stage2_file = rd / "stage2_retrieval_results.json"
        if stage1_file and stage2_file:
            break
    return stage1_file, stage2_file


def main():
    # Setup
    run_dir = create_run_directory()
    log = setup_logger("stage3", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info("Starting Stage 3: Topology Fusion and IG-level Completion")

    BASE_DIR = Path(__file__).resolve().parent.parent
    LOGS_DIR = BASE_DIR / "logs"

    # Load CIS requirements
    cis_map = load_cis_requirements()
    log.info(f"Loaded {len(cis_map)} CIS safeguards")

    # Load training templates
    train_templates = load_train_templates()
    log.info(f"Loaded {len(train_templates)} training templates")

    # Find Stage 1 and Stage 2 results
    stage1_file, stage2_file = find_latest_stage_results(LOGS_DIR)
    if not stage1_file or not stage2_file:
        log.error("Stage 1 or Stage 2 results not found. Run them first.")
        sys.exit(1)

    log.info(f"Loading Stage 1 from: {stage1_file}")
    log.info(f"Loading Stage 2 from: {stage2_file}")

    with open(stage1_file) as f:
        stage1_intents = json.load(f)
    with open(stage2_file) as f:
        stage2_results = json.load(f)

    # Build sample list
    samples = []
    for s1, s2 in zip(stage1_intents, stage2_results):
        samples.append({
            "sample_id": s1["sample_id"],
            "gt_template_id": s1["template_id"],
            "user_intent": s1["description"],
            "stage1_template": {
                "schema_version": "2.1",
                "template_id": f"generated_{s1['sample_id']}",
                "title": s1["title"],
                "description": s1["description"],
                "scenario": s1["scenario"],
                "topology_summary": s1["topology_summary"],
                "zones": [{"zone_id": f"z{i+1}", "name": zn, "zone_type": zt}
                          for i, (zn, zt) in enumerate(zip(s1["zone_names"], s1["zone_types"]))],
                "nodes": [{"node_id": f"n{i+1}", "name": nn, "normalized_type": nt,
                           "zone_id": f"z{(i // 3) + 1}"}
                          for i, (nn, nt) in enumerate(zip(s1["node_names"], s1["node_types"]))],
                "links": {"node_links": [], "zone_links": []},
            },
            "retrieved_templates": s2["retrieved_templates"],
        })

    log.info(f"Processing {len(samples)} samples...")

    # IG levels to generate
    ig_levels = ["IG2", "IG3"]
    all_results = {}

    for ig in ig_levels:
        log.info(f"\n{'='*50}")
        log.info(f"Processing with {ig}")
        log.info(f"{'='*50}")

        results = []
        for i, sample in enumerate(samples):
            log.info(f"[{i+1}/{len(samples)}] Processing: {sample['sample_id']}")
            result = process_single_sample(sample, cis_map, train_templates, ig_level=ig, logger_ref=log)
            results.append(result)
            save_json(results, run_dir, f"stage3_completion_results_{ig}_partial.json")

        all_results[ig] = results

        # Evaluate
        log.info(f"\nEvaluating {ig} results...")
        metrics = evaluate_results(results, train_templates, logger_ref=log)

        # Save
        save_json(results, run_dir, f"stage3_completion_results_{ig}.json")
        # Backward-compatible filename for older downstream scripts.
        save_json(results, run_dir, f"stage3_results_{ig}.json")
        save_json(metrics, run_dir, f"stage3_completion_evaluation_{ig}.json")
        save_markdown(generate_evaluation_report(metrics), run_dir, f"stage3_completion_report_{ig}.md")

        log.info(f"{ig} results saved to: {run_dir}")

    log.info(f"\nAll Stage 3 results saved to: {run_dir}")


if __name__ == "__main__":
    main()
