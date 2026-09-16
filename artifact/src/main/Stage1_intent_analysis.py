#!/usr/bin/env python3
"""
Stage 1: Intent Analysis (LLM-driven)
Entry point for intent extraction pipeline.
Extracts structured fields from user natural language intent using Qwen2.5-72B.
Output: Initial topology templates following schema_contract format.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_utils import create_run_directory, setup_logger
from Stage1_utils import analyze_intents_from_json


def main():
    # Setup run directory and logging
    run_dir = create_run_directory()
    log = setup_logger("stage1", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info("Starting Stage 1: Intent Analysis")

    # Load training data
    BASE_DIR = Path(__file__).resolve().parent.parent
    TRAIN_JSON = BASE_DIR / "dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"

    if not TRAIN_JSON.exists():
        log.error(f"Training data not found: {TRAIN_JSON}")
        sys.exit(1)

    # Analyze all training intents
    results, metrics = analyze_intents_from_json(TRAIN_JSON, run_dir=run_dir, logger_ref=log)

    log.info(f"Completed. Scenario accuracy: {metrics['scenario_accuracy']:.1%}")
    log.info(f"Avg zone type recall: {metrics['avg_zone_type_recall']:.1%}")
    log.info(f"Avg node type recall: {metrics['avg_node_type_recall']:.1%}")
    log.info(f"Generated templates saved to: {run_dir / 'stage1_generated_templates.json'}")


if __name__ == "__main__":
    main()
