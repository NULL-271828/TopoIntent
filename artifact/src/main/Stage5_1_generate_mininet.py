#!/usr/bin/env python3
"""
Stage 5.1: Generate Mininet validation scripts from Stage 4 XML outputs.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_utils import setup_logger, save_json
from Stage5_1_utils import generate_mininet_script_from_xml_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_SOURCE_RUN_DIR = PROJECT_ROOT / "logs/test_all_ig_full_20260524_214454"
DEV_OUTPUT_DIR = PROJECT_ROOT / "logs/stage5_dev/acl_validation_current"


def find_latest_stage4_xml(logs_dir, ig=None):
    run_dirs = sorted(Path(logs_dir).glob("run_*"), key=lambda x: x.name, reverse=True)
    xml_files = []
    for rd in run_dirs:
        xml_root = rd / "xml"
        if not xml_root.exists():
            continue
        if ig:
            xml_files.extend(sorted((xml_root / ig).glob("*.xml")))
        else:
            xml_files.extend(sorted(xml_root.glob("*/*.xml")))
        if xml_files:
            return xml_files
    return []


def find_xml_from_run_dir(run_dir, ig=None):
    run_dir = Path(run_dir)
    candidates = []
    sample_root = run_dir / "samples"
    if sample_root.exists():
        if ig:
            candidates.extend(sorted(sample_root.glob(f"*/xml/{ig}/*.xml")))
        else:
            candidates.extend(sorted(sample_root.glob("*/xml/*/*.xml")))
    xml_root = run_dir / "xml"
    if xml_root.exists():
        if ig:
            candidates.extend(sorted((xml_root / ig).glob("*.xml")))
        else:
            candidates.extend(sorted(xml_root.glob("*/*.xml")))
    return candidates


def main():
    parser = argparse.ArgumentParser(description="Generate Mininet validation scripts from Stage 4 XML")
    parser.add_argument("--xml-file", type=str, default=None, help="Single XML file to convert")
    parser.add_argument("--ig", choices=["IG2", "IG3"], default=None, help="IG level when auto-discovering XML")
    parser.add_argument("--limit", type=int, default=None, help="Optional max XML files to process")
    parser.add_argument(
        "--source-run-dir",
        type=str,
        default=str(DEV_SOURCE_RUN_DIR),
        help="Stage4/run.py output directory containing sample XML files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(DEV_OUTPUT_DIR),
        help="Fixed Stage5.1 development output directory",
    )
    args = parser.parse_args()

    run_dir = Path(args.output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = setup_logger("stage5_1", run_dir)
    log.info(f"Run directory: {run_dir}")
    log.info("Starting Stage 5.1: ACL-driven Mininet script generation")

    if args.xml_file:
        xml_files = [Path(args.xml_file)]
    else:
        xml_files = find_xml_from_run_dir(args.source_run_dir, ig=args.ig)
        if not xml_files:
            xml_files = find_latest_stage4_xml(PROJECT_ROOT / "logs", ig=args.ig)

    if args.limit:
        xml_files = xml_files[:args.limit]
    if not xml_files:
        log.error("No Stage 4 XML files found. Run Stage 4 first or pass --xml-file.")
        sys.exit(1)

    output_dir = run_dir / "mininet_scripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifests = []
    for xml_file in xml_files:
        log.info(f"Generating Mininet script for: {xml_file}")
        manifest = generate_mininet_script_from_xml_file(xml_file, output_dir)
        manifests.append(manifest)
        log.info(f"Generated: {manifest['script_file']}")

    save_json(manifests, run_dir, "stage5_1_manifest.json")
    log.info(f"Generated {len(manifests)} Mininet scripts")
    log.info(f"Outputs saved to: {run_dir}")

    print(json.dumps(manifests, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
