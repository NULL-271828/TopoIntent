#!/usr/bin/env python3
"""
Logging utilities for TopoIntent pipeline.
Each run creates a timestamped directory under logs/ for traceability.
"""

import logging
import os
import json
from datetime import datetime
from pathlib import Path

# === Config ===
LOGS_BASE = Path(__file__).resolve().parent.parent / "logs"


def create_run_directory():
    """Create a timestamped run directory under logs/."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = LOGS_BASE / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def setup_logger(name, run_dir, level=logging.INFO):
    """
    Setup a logger that writes to both file and console.

    Args:
        name: logger name (e.g., 'stage1', 'pipeline')
        run_dir: Path to the run directory
        level: logging level

    Returns:
        logging.Logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers = []  # Clear existing handlers

    # File handler
    log_file = run_dir / f"{name}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))
    logger.addHandler(ch)

    return logger


def save_json(data, run_dir, filename):
    """Save JSON data to run directory."""
    filepath = run_dir / filename
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return filepath


def save_markdown(content, run_dir, filename):
    """Save markdown content to run directory."""
    filepath = run_dir / filename
    with open(filepath, "w") as f:
        f.write(content)
    return filepath
