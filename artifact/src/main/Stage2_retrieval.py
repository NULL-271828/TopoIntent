#!/usr/bin/env python3
"""
Stage 2: Single-Vector Retrieval
Entry point for template retrieval pipeline.
Retrieves similar topology templates from vector database based on Stage 1 output.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_utils import create_run_directory, setup_logger
from Stage2_utils import retrieve_all_from_json


def find_latest_stage1_results(logs_dir):
    """Find the latest Stage 1 results."""
    run_dirs = sorted(Path(logs_dir).glob("run_*"), key=lambda x: x.name, reverse=True)
    for rd in run_dirs:
        full_stage1_file = rd / "stage1_results.json"
        if full_stage1_file.exists():
            return full_stage1_file, rd
        stage1_file = rd / "stage1_extracted_intents.json"
        if stage1_file.exists():
            return stage1_file, rd
    return None, None


def load_stage1_results(stage1_file):
    """Load either full Stage 1 results or extracted-intent compatibility files."""
    with open(stage1_file) as f:
        data = json.load(f)

    # New Stage 1 output: already contains generated_template.
    if data and "generated_template" in data[0]:
        return [
            {
                "sample_id": r["sample_id"],
                "template_id": r["template_id"],
                "generated_template": r["generated_template"],
            }
            for r in data
        ]

    # Compatibility output: stage1_extracted_intents.json.
    stage1_results = []
    for t in data:
        stage1_results.append({
            "sample_id": t["sample_id"],
            "template_id": t["template_id"],
            "generated_template": {
                "scenario": t.get("scenario", ""),
                "description": t.get("description", ""),
                "topology_summary": t.get("topology_summary", ""),
                "title": t.get("title", ""),
                "zones": [{"zone_type": zt, "name": zn}
                          for zn, zt in zip(t.get("zone_names", []), t.get("zone_types", []))],
                "nodes": [{"normalized_type": nt, "name": nn}
                          for nn, nt in zip(t.get("node_names", []), t.get("node_types", []))],
            },
        })
    return stage1_results


def main():
    # Setup run directory and logging
    run_dir = create_run_directory()
    log = setup_logger("stage2", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info("Starting Stage 2: Single-Vector Retrieval")

    # Find latest Stage 1 results
    BASE_DIR = Path(__file__).resolve().parent.parent
    LOGS_DIR = BASE_DIR / "logs"
    stage1_file, stage1_run_dir = find_latest_stage1_results(LOGS_DIR)

    if not stage1_file:
        log.error("No Stage 1 results found. Run Stage 1 first.")
        sys.exit(1)

    log.info(f"Loading Stage 1 results from: {stage1_file}")
    stage1_results = load_stage1_results(stage1_file)

    log.info(f"Loaded {len(stage1_results)} Stage 1 templates")

    # Run retrieval
    retrieval_results, metrics = retrieve_all_from_json(
        stage1_results, run_dir=run_dir, top_k=5, logger_ref=log
    )

    log.info(f"Completed.")
    for k, acc in metrics["top_k"].items():
        log.info(f"  {k} accuracy: {acc:.1%}")
    log.info(f"Results saved to: {run_dir}")


if __name__ == "__main__":
    main()
