#!/usr/bin/env python3
"""
Stage 4: CIS verification, repair, test-case generation, and XML export.
"""

import copy
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom
from collections import deque

import requests

os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

LLM_API = os.getenv("LLM_API", "http://127.0.0.1:8005/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from SYSTEM_PROMPT import (
    STAGE4_CIS_VERIFY_PROMPT,
    STAGE4_CIS_REPAIR_PROMPT,
    STAGE4_TESTCASE_PROMPT,
)
from Stage3_utils import (
    CIS_IG_RULES,
    load_cis_requirements,
    merge_topology_additions,
    parse_llm_json,
    validate_topology,
)
from logging_utils import save_json, save_markdown

logger = None


def call_llm(system_prompt, user_message, temperature=0.1, max_tokens=8192):
    """Call LLM via OpenAI-compatible API."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(LLM_API, json=payload, timeout=240, proxies={"http": "", "https": ""})
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    finish_reason = choice.get("finish_reason")
    content = choice["message"]["content"].strip()
    if logger and finish_reason == "length":
        logger.warning(f"LLM response was truncated by max_tokens={max_tokens}")
    return content


def parse_llm_json_or_none(raw, context, logger_ref=None):
    """Parse LLM JSON and convert malformed/truncated output into a logged warning."""
    try:
        return parse_llm_json(raw)
    except ValueError as exc:
        if logger_ref:
            logger_ref.warning(f"{context}: malformed LLM JSON ignored: {exc}")
        return None


def get_required_cis(ig_level, cis_map=None):
    """Return required CIS controls for an IG level."""
    cis_map = cis_map or load_cis_requirements()
    required = []
    for rule_id in CIS_IG_RULES.get(ig_level, []):
        if rule_id in cis_map:
            required.append({
                "cis_safeguard_id": rule_id,
                "title": cis_map[rule_id].get("title", ""),
                "description": cis_map[rule_id].get("description", ""),
            })
    return required


def verify_cis_compliance(topology, ig_level, cis_map=None, logger_ref=None):
    """Verify a topology against every required CIS rule for the IG level."""
    required = get_required_cis(ig_level, cis_map)
    user_msg = (
        f"IG level: {ig_level}\n\n"
        f"Required CIS safeguards:\n{json.dumps(required, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"Topology:\n{json.dumps(topology, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Return a verification result for every required safeguard."
    )
    raw = call_llm(STAGE4_CIS_VERIFY_PROMPT, user_msg, max_tokens=8192)
    result = parse_llm_json(raw)
    result = normalize_verification_result(result, topology, ig_level, required)
    if logger_ref:
        counts = count_control_statuses(result)
        logger_ref.info(
            f"Verification ({ig_level}): satisfied={counts['satisfied']}, "
            f"unsatisfied={counts['unsatisfied']}, manual_review={counts['manual_review']}"
        )
    return result


def normalize_verification_result(result, topology, ig_level, required):
    """Ensure verification output contains one normalized control per required rule."""
    result = result if isinstance(result, dict) else {}
    controls_by_id = {}
    for control in result.get("controls", []) or []:
        if not isinstance(control, dict):
            continue
        cid = str(control.get("cis_safeguard_id", "")).strip()
        if cid:
            controls_by_id[cid] = control

    normalized = []
    for req in required:
        cid = req["cis_safeguard_id"]
        control = dict(controls_by_id.get(cid, {}))
        status = str(control.get("status", "manual_review")).strip().lower()
        if status not in {"satisfied", "unsatisfied", "manual_review"}:
            status = "manual_review"
        try:
            confidence = float(control.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        normalized.append({
            "cis_safeguard_id": cid,
            "title": control.get("title") or req.get("title", ""),
            "status": status,
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "evidence": control.get("evidence", ""),
            "missing_reason": control.get("missing_reason", ""),
            "repair_suggestion": control.get("repair_suggestion", ""),
        })

    all_satisfied = all(c["status"] == "satisfied" for c in normalized)
    return {
        "template_id": topology.get("template_id", ""),
        "ig_level": ig_level,
        "all_required_satisfied": all_satisfied,
        "controls": normalized,
    }


def count_control_statuses(verification):
    counts = {"satisfied": 0, "unsatisfied": 0, "manual_review": 0}
    for control in verification.get("controls", []) or []:
        status = control.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def controls_needing_repair(verification, include_manual_review=True):
    statuses = {"unsatisfied", "manual_review"} if include_manual_review else {"unsatisfied"}
    return [c for c in verification.get("controls", []) or [] if c.get("status") in statuses]


def repair_topology_for_cis(topology, findings, ig_level, logger_ref=None):
    """Ask LLM for additive repair diff and merge it into topology."""
    if not findings:
        return topology, {"zones": [], "nodes": [], "links": {"node_links": [], "zone_links": []}}

    user_msg = (
        f"IG level: {ig_level}\n\n"
        f"Current topology:\n{json.dumps(topology, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"CIS findings needing repair:\n{json.dumps(findings, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Return only additive topology changes."
    )
    raw = call_llm(STAGE4_CIS_REPAIR_PROMPT, user_msg, max_tokens=8192)
    additions = parse_llm_json_or_none(raw, "CIS repair", logger_ref=logger_ref)
    if additions is None:
        retry_msg = (
            f"IG level: {ig_level}\n\n"
            f"Existing zone IDs: {[z.get('zone_id') for z in topology.get('zones', []) or []]}\n"
            f"Existing node IDs: {[n.get('node_id') for n in topology.get('nodes', []) or []]}\n"
            f"Findings needing repair:\n{json.dumps(findings[:8], ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Return ONLY compact additive changes. Limit to 3 zones, 8 nodes, 20 node_links, 10 zone_links. "
            "Use short labels and valid JSON only."
        )
        raw = call_llm(STAGE4_CIS_REPAIR_PROMPT, retry_msg, max_tokens=8192)
        additions = parse_llm_json_or_none(raw, "CIS repair retry", logger_ref=logger_ref)
    if additions is None:
        additions = {"zones": [], "nodes": [], "links": {"node_links": [], "zone_links": []}}
    additions = normalize_additions(additions)
    repaired = merge_topology_additions(topology, additions)
    if logger_ref:
        logger_ref.info(
            f"Repair additions: zones={len(additions.get('zones', []))}, "
            f"nodes={len(additions.get('nodes', []))}, "
            f"zone_links={len(additions.get('links', {}).get('zone_links', []))}, "
            f"node_links={len(additions.get('links', {}).get('node_links', []))}"
        )
    return repaired, additions


def repair_topology_from_stage5_diagnostics(topology, stage5_report, ig_level, logger_ref=None):
    """Repair topology using Stage 5 validation diagnostics, then re-run CIS verification upstream.

    Stage 5 must remain a validator only. This interface lets Stage 4 consume
    failed/skipped execution evidence and propose additive, CIS-aware topology
    changes before exporting a new XML.
    """
    findings = build_stage5_repair_findings(stage5_report)
    if not findings:
        return topology, {"zones": [], "nodes": [], "links": {"node_links": [], "zone_links": []}}
    if logger_ref:
        logger_ref.info(f"Stage5 diagnostic repair findings: {len(findings)}")
    return repair_topology_for_cis(topology, findings, ig_level, logger_ref=logger_ref)


def build_stage5_repair_findings(stage5_report):
    """Convert Stage 5 report failures into repair findings consumable by repair prompt."""
    report = stage5_report if isinstance(stage5_report, dict) else {}
    findings = []

    diagnosis = report.get("diagnosis", {}) if isinstance(report.get("diagnosis", {}), dict) else {}

    for item in diagnosis.get("topology_failures", []) or []:
        findings.append({
            "cis_safeguard_id": "stage5_validation",
            "title": "stage5_pre_acl_topology_failure",
            "status": "unsatisfied",
            "confidence": 1.0,
            "evidence": json.dumps(item, ensure_ascii=False),
            "missing_reason": "stage5_pre_acl_topology_failure",
            "repair_suggestion": (
                "The flow is not reachable before ACL enforcement. Add or repair concrete node_links "
                "between the resolved source and destination path; do not use zone_links as executable links."
            ),
        })

    for item in diagnosis.get("acl_failures", []) or []:
        findings.append({
            "cis_safeguard_id": "stage5_validation",
            "title": "stage5_post_acl_policy_failure",
            "status": "unsatisfied",
            "confidence": 1.0,
            "evidence": json.dumps(item, ensure_ascii=False),
            "missing_reason": "stage5_post_acl_policy_failure",
            "repair_suggestion": (
                "Pre-ACL connectivity exists but ACL-enforced behavior violates the expected policy. "
                "Repair the access-control intent, testcase endpoint selection, or security-control placement."
            ),
        })

    for item in diagnosis.get("acl_conflicts", []) or []:
        findings.append({
            "cis_safeguard_id": "stage5_validation",
            "title": "stage5_acl_policy_conflict",
            "status": "unsatisfied",
            "confidence": 1.0,
            "evidence": json.dumps(item, ensure_ascii=False),
            "missing_reason": "stage5_acl_policy_conflict",
            "repair_suggestion": (
                "The generated test cases contain conflicting allow/deny expectations for the same source, "
                "destination, protocol, and port. Regenerate or revise the conflicting policy tests so the "
                "ACL table has one unambiguous action per flow."
            ),
        })

    for item in diagnosis.get("node_link_failures", []) or []:
        findings.append({
            "cis_safeguard_id": "stage5_validation",
            "title": "stage5_node_link_unreachable",
            "status": "unsatisfied",
            "confidence": 1.0,
            "evidence": json.dumps(item, ensure_ascii=False),
            "missing_reason": "stage5_node_link_unreachable",
            "repair_suggestion": "Repair or replace the concrete node_link so the declared endpoint pair is reachable.",
        })

    for test in report.get("policy_tests", []) or report.get("results", []) or []:
        if test.get("passed") is True:
            continue
        expected = test.get("expected") or test.get("expect")
        actual = test.get("actual")
        reason = test.get("reason", "")
        has_node_path = test.get("has_node_path")
        finding_type = "stage5_policy_failure"
        suggestion = "Review topology node_links and policy-test endpoints; apply minimal additive changes only if they remain CIS-compliant."
        if actual == "skip" or "endpoint not resolvable" in str(reason):
            finding_type = "stage5_unexecutable_testcase"
            suggestion = "Regenerate this testcase with concrete existing source_node and target_node, or add a justified node endpoint in Stage 4 if required by the design."
        elif expected == "allow" and actual == "deny" and has_node_path is False:
            finding_type = "stage5_missing_node_path"
            suggestion = "Add or repair concrete node_links along the intended path; do not use zone_links as executable links."
        elif expected == "allow" and actual == "deny" and has_node_path is True:
            finding_type = "stage5_path_or_service_unreachable"
            suggestion = "Inspect node-link path, service endpoint selection, and security-control placement; repair topology or testcase endpoint selection as needed."
        elif expected in {"block", "deny"} and actual == "allow":
            finding_type = "stage5_policy_too_permissive"
            suggestion = "Add or reposition firewall/ACL/security-control nodes or adjust topology links to enforce the intended deny policy."

        findings.append({
            "cis_safeguard_id": "stage5_validation",
            "title": finding_type,
            "status": "unsatisfied",
            "confidence": 1.0,
            "evidence": json.dumps({
                "test_id": test.get("id") or test.get("test_id"),
                "src": test.get("src") or test.get("source_node"),
                "dst": test.get("dst") or test.get("target_node"),
                "resolved_src": test.get("resolved_src"),
                "resolved_dst": test.get("resolved_dst"),
                "expected": expected,
                "actual": actual,
                "has_node_path": has_node_path,
                "latency_ms": test.get("latency_ms"),
                "reason": reason,
            }, ensure_ascii=False),
            "missing_reason": finding_type,
            "repair_suggestion": suggestion,
        })
    return findings


def normalize_additions(additions):
    additions = additions if isinstance(additions, dict) else {}
    links = additions.get("links", {})
    if not isinstance(links, dict):
        links = {}
    return {
        "zones": additions.get("zones", []) if isinstance(additions.get("zones", []), list) else [],
        "nodes": additions.get("nodes", []) if isinstance(additions.get("nodes", []), list) else [],
        "links": {
            "node_links": links.get("node_links", []) if isinstance(links.get("node_links", []), list) else [],
            "zone_links": links.get("zone_links", []) if isinstance(links.get("zone_links", []), list) else [],
        },
    }


def merge_addition_records(primary, secondary):
    """Combine two addition records for audit output."""
    primary = normalize_additions(primary)
    secondary = normalize_additions(secondary)
    return {
        "zones": primary["zones"] + secondary["zones"],
        "nodes": primary["nodes"] + secondary["nodes"],
        "links": {
            "node_links": primary["links"]["node_links"] + secondary["links"]["node_links"],
            "zone_links": primary["links"]["zone_links"] + secondary["links"]["zone_links"],
        },
    }


def topology_id_sets(topology):
    zones = topology.get("zones", []) or []
    nodes = topology.get("nodes", []) or []
    links = topology.get("links", {}) or {}
    if not isinstance(links, dict):
        links = {"node_links": [], "zone_links": []}
    return {
        "zone_ids": {str(z.get("zone_id")) for z in zones if z.get("zone_id")},
        "node_ids": {str(n.get("node_id")) for n in nodes if n.get("node_id")},
        "link_ids": {
            str(l.get("link_id"))
            for l in (links.get("node_links", []) or []) + (links.get("zone_links", []) or [])
            if isinstance(l, dict) and l.get("link_id")
        },
    }


def next_prefixed_id(existing, prefix):
    idx = 1
    while f"{prefix}{idx}" in existing:
        idx += 1
    value = f"{prefix}{idx}"
    existing.add(value)
    return value


def zone_signature(zone):
    return (
        str(zone.get("name", "")).strip().lower(),
        str(zone.get("zone_type", "")).strip().lower(),
    )


def node_type_for_zone(zone):
    zone_type = str(zone.get("zone_type", "")).lower()
    name = str(zone.get("name", "")).lower()
    text = f"{zone_type} {name}"
    if "management" in text:
        return "server", "Management Service"
    if "security" in text or "dmz" in text:
        return "firewall", "Security Gateway"
    if "external" in text or "testing" in text or "pen" in text:
        return "endpoint", "External Endpoint"
    if "hub" in text:
        return "router", "Hub Router"
    if "identity" in text:
        return "identity_server", "Identity Service"
    if "data" in text:
        return "server", "Application Server"
    return "workload", "Representative Workload"


def choose_anchor_node(topology, excluded_zone_id=None):
    nodes = topology.get("nodes", []) or []
    if not nodes:
        return None
    priority = ("firewall", "router", "vpn_gateway", "load_balancer", "ids_ips", "siem", "server", "workload")
    for node_type in priority:
        for node in nodes:
            if excluded_zone_id and node.get("zone_id") == excluded_zone_id:
                continue
            if node.get("normalized_type") == node_type and node.get("node_id"):
                return node
    for node in nodes:
        if not excluded_zone_id or node.get("zone_id") != excluded_zone_id:
            return node
    return nodes[0]


def build_structural_findings(topology, ig_level, reference_topology=None):
    """Return Stage4 repair findings for topology structural integrity."""
    topology = validate_topology(topology)
    zones = topology.get("zones", []) or []
    nodes = topology.get("nodes", []) or []
    links = topology.get("links", {}) or {}
    zone_ids = {z.get("zone_id") for z in zones if z.get("zone_id")}
    node_ids = {n.get("node_id") for n in nodes if n.get("node_id")}
    zone_to_nodes = {z.get("zone_id"): [] for z in zones if z.get("zone_id")}
    findings = []

    for node in nodes:
        zone_id = node.get("zone_id")
        if zone_id in zone_to_nodes:
            zone_to_nodes[zone_id].append(node.get("node_id"))
        else:
            findings.append({
                "cis_safeguard_id": "stage4_structural_integrity",
                "title": "node_references_missing_zone",
                "status": "unsatisfied",
                "confidence": 1.0,
                "evidence": json.dumps(node, ensure_ascii=False),
                "missing_reason": f"Node {node.get('node_id')} references missing zone_id {zone_id}.",
                "repair_suggestion": "Add the missing zone or add an equivalent node in an existing semantically correct zone.",
            })

    for zone in zones:
        zone_id = zone.get("zone_id")
        if zone_id and not zone_to_nodes.get(zone_id):
            findings.append({
                "cis_safeguard_id": "stage4_structural_integrity",
                "title": "empty_zone_without_node",
                "status": "unsatisfied",
                "confidence": 1.0,
                "evidence": json.dumps(zone, ensure_ascii=False),
                "missing_reason": f"Zone {zone_id} has no concrete node assigned to it.",
                "repair_suggestion": (
                    "Add one representative concrete node in this zone and connect it with node_links "
                    "to the nearest hub, firewall, router, or management/security node."
                ),
            })

    for link in links.get("node_links", []) or []:
        if not isinstance(link, dict):
            continue
        src = link.get("source")
        dst = link.get("target")
        if src not in node_ids or dst not in node_ids:
            findings.append({
                "cis_safeguard_id": "stage4_structural_integrity",
                "title": "node_link_invalid_endpoint",
                "status": "unsatisfied",
                "confidence": 1.0,
                "evidence": json.dumps(link, ensure_ascii=False),
                "missing_reason": f"Node link {link.get('link_id')} references missing endpoint(s): {src}, {dst}.",
                "repair_suggestion": "Add the missing concrete endpoint node(s), or add a valid replacement node_link.",
            })

    for link in links.get("zone_links", []) or []:
        if not isinstance(link, dict):
            continue
        src = link.get("source")
        dst = link.get("target")
        if src not in zone_ids or dst not in zone_ids:
            findings.append({
                "cis_safeguard_id": "stage4_structural_integrity",
                "title": "zone_link_invalid_endpoint",
                "status": "unsatisfied",
                "confidence": 1.0,
                "evidence": json.dumps(link, ensure_ascii=False),
                "missing_reason": f"Zone link {link.get('link_id')} references missing endpoint(s): {src}, {dst}.",
                "repair_suggestion": "Add the missing zone(s), or add a valid replacement zone_link.",
            })

    if ig_level == "IG3" and reference_topology:
        current_signatures = {zone_signature(z) for z in zones}
        missing_ref_zones = [
            z for z in reference_topology.get("zones", []) or []
            if zone_signature(z) not in current_signatures
        ]
        if missing_ref_zones:
            findings.append({
                "cis_safeguard_id": "stage4_ig_consistency",
                "title": "ig3_missing_ig2_zone_semantics",
                "status": "unsatisfied",
                "confidence": 1.0,
                "evidence": json.dumps(missing_ref_zones, ensure_ascii=False),
                "missing_reason": "IG3 topology removed or failed to preserve IG2 zone semantics.",
                "repair_suggestion": (
                    "Add the missing IG2 zone semantics to IG3 as preserved or stricter zones, "
                    "with representative nodes and concrete node_links. IG3 may add controls but must not reduce segmentation."
                ),
            })

    return findings


def structural_additions(topology, ig_level, reference_topology=None):
    """Build deterministic minimal additions that make the topology structurally executable."""
    topology = validate_topology(topology)
    ids = topology_id_sets(topology)
    additions = {"zones": [], "nodes": [], "links": {"node_links": [], "zone_links": []}}
    zones = topology.get("zones", []) or []
    nodes = topology.get("nodes", []) or []
    zone_ids = {z.get("zone_id") for z in zones if z.get("zone_id")}
    zone_to_nodes = {z.get("zone_id"): [] for z in zones if z.get("zone_id")}
    for node in nodes:
        if node.get("zone_id") in zone_to_nodes:
            zone_to_nodes[node.get("zone_id")].append(node)

    if ig_level == "IG3" and reference_topology:
        current_signatures = {zone_signature(z) for z in zones}
        for ref_zone in reference_topology.get("zones", []) or []:
            if zone_signature(ref_zone) in current_signatures:
                continue
            new_zone = copy.deepcopy(ref_zone)
            if not new_zone.get("zone_id") or new_zone["zone_id"] in zone_ids:
                new_zone["zone_id"] = next_prefixed_id(ids["zone_ids"], "z_preserve")
            else:
                ids["zone_ids"].add(new_zone["zone_id"])
            additions["zones"].append(new_zone)
            zones.append(new_zone)
            zone_ids.add(new_zone["zone_id"])
            zone_to_nodes[new_zone["zone_id"]] = []
            current_signatures.add(zone_signature(new_zone))

    for node in nodes:
        zone_id = node.get("zone_id")
        if zone_id and zone_id not in zone_ids:
            additions["zones"].append({
                "zone_id": zone_id,
                "name": f"Recovered Zone {zone_id}",
                "zone_type": "other",
            })
            zone_ids.add(zone_id)
            zone_to_nodes.setdefault(zone_id, []).append(node)

    links = topology.get("links", {}) or {}
    if not isinstance(links, dict):
        links = {"node_links": [], "zone_links": []}

    anchor_zone = None
    anchor = choose_anchor_node(topology)
    if anchor:
        anchor_zone = anchor.get("zone_id")
    elif zones:
        anchor_zone = zones[0].get("zone_id")

    for link in links.get("node_links", []) or []:
        if not isinstance(link, dict):
            continue
        for endpoint_key in ("source", "target"):
            endpoint = link.get(endpoint_key)
            if not endpoint or endpoint in ids["node_ids"]:
                continue
            zone_id = anchor_zone
            if not zone_id:
                zone_id = next_prefixed_id(ids["zone_ids"], "z_recovered")
                additions["zones"].append({
                    "zone_id": zone_id,
                    "name": f"Recovered Zone {zone_id}",
                    "zone_type": "other",
                })
                zones.append(additions["zones"][-1])
                zone_ids.add(zone_id)
            ids["node_ids"].add(endpoint)
            additions["nodes"].append({
                "node_id": endpoint,
                "name": f"Recovered Endpoint {endpoint}",
                "normalized_type": "other",
                "zone_id": zone_id,
            })
            zone_to_nodes.setdefault(zone_id, []).append(additions["nodes"][-1])

    for link in links.get("zone_links", []) or []:
        if not isinstance(link, dict):
            continue
        for endpoint_key in ("source", "target"):
            endpoint = link.get(endpoint_key)
            if not endpoint or endpoint in zone_ids:
                continue
            additions["zones"].append({
                "zone_id": endpoint,
                "name": f"Recovered Zone {endpoint}",
                "zone_type": "other",
            })
            zones.append(additions["zones"][-1])
            zone_ids.add(endpoint)
            zone_to_nodes.setdefault(endpoint, [])

    for zone in list(zones):
        zone_id = zone.get("zone_id")
        if not zone_id or zone_to_nodes.get(zone_id):
            continue
        node_type, node_name = node_type_for_zone(zone)
        node_id = next_prefixed_id(ids["node_ids"], "n_struct")
        new_node = {
            "node_id": node_id,
            "name": f"{zone.get('name', zone_id)} {node_name}",
            "normalized_type": node_type,
            "zone_id": zone_id,
        }
        additions["nodes"].append(new_node)
        zone_to_nodes[zone_id] = [new_node]

        anchor = choose_anchor_node(topology, excluded_zone_id=zone_id)
        if anchor and anchor.get("node_id") != node_id:
            link_id = next_prefixed_id(ids["link_ids"], "nl_struct")
            additions["links"]["node_links"].append({
                "link_id": link_id,
                "source": anchor["node_id"],
                "target": node_id,
                "link_type": "routing",
                "label": "Structural zone attachment",
            })

    return additions


def ensure_structural_integrity(topology, ig_level, reference_topology=None, logger_ref=None):
    """Apply deterministic structural additions before final export."""
    findings = build_structural_findings(topology, ig_level, reference_topology=reference_topology)
    additions = structural_additions(topology, ig_level, reference_topology=reference_topology)
    if not additions["zones"] and not additions["nodes"] and not additions["links"]["node_links"] and not additions["links"]["zone_links"]:
        return topology, additions, findings
    repaired = merge_topology_additions(topology, additions)
    if logger_ref:
        logger_ref.info(
            f"Structural normalization: findings={len(findings)}, "
            f"zones={len(additions['zones'])}, nodes={len(additions['nodes'])}, "
            f"node_links={len(additions['links']['node_links'])}"
        )
    return repaired, additions, findings


def generate_test_cases(topology, logger_ref=None):
    """Generate connectivity test cases from final topology."""
    user_msg = (
        f"Final topology:\n{json.dumps(topology, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Generate at most 40 compact connectivity test cases for this final topology. "
        "Return valid JSON only."
    )
    raw = call_llm(STAGE4_TESTCASE_PROMPT, user_msg, max_tokens=8192)
    result = parse_llm_json_or_none(raw, "Test case generation", logger_ref=logger_ref)
    if result is None:
        retry_topology = compact_topology_for_testcase_prompt(topology)
        retry_msg = (
            f"Final topology summary:\n{json.dumps(retry_topology, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Generate 12 to 30 executable node-to-node test cases. "
            "Use existing node_id values only. Include both allow and block cases. "
            "Keep reasons under 12 words. Return valid JSON only."
        )
        raw = call_llm(STAGE4_TESTCASE_PROMPT, retry_msg, max_tokens=8192)
        result = parse_llm_json_or_none(raw, "Test case generation retry", logger_ref=logger_ref)
    if result is None:
        if logger_ref:
            logger_ref.warning("Falling back to deterministic test-case generation")
        result = deterministic_test_cases(topology)
    if not isinstance(result, dict):
        result = {}
    result.setdefault("template_id", topology.get("template_id", ""))
    result["test_cases"] = normalize_test_cases(topology, result.get("test_cases", []), logger_ref=logger_ref)
    result["test_cases"] = ensure_block_test_coverage(topology, result["test_cases"], logger_ref=logger_ref)
    if logger_ref:
        allow_count = sum(1 for t in result["test_cases"] if t.get("expected") == "allow")
        block_count = sum(1 for t in result["test_cases"] if t.get("expected") == "block")
        logger_ref.info(f"Test cases: {len(result['test_cases'])} total ({allow_count} allow, {block_count} block)")
    return result


def compact_topology_for_testcase_prompt(topology, max_links=80):
    """Keep only fields needed to generate executable policy tests."""
    node_links = (topology.get("links", {}) or {}).get("node_links", []) or []
    return {
        "template_id": topology.get("template_id", ""),
        "scenario": topology.get("scenario", ""),
        "zones": topology.get("zones", []) or [],
        "nodes": topology.get("nodes", []) or [],
        "node_links": node_links[:max_links],
    }


def deterministic_test_cases(topology, max_allow=20, max_block=10):
    """Generate a compact executable fallback when the LLM emits truncated JSON."""
    nodes = [n for n in topology.get("nodes", []) or [] if n.get("node_id")]
    node_by_id = {n["node_id"]: n for n in nodes}
    node_links = (topology.get("links", {}) or {}).get("node_links", []) or []
    adjacency = {}
    for link in node_links:
        src = link.get("source")
        dst = link.get("target")
        if src in node_by_id and dst in node_by_id:
            adjacency.setdefault(src, set()).add(dst)
            adjacency.setdefault(dst, set()).add(src)

    cases = []
    seen_pairs = set()
    for link in node_links:
        if len(cases) >= max_allow:
            break
        src = link.get("source")
        dst = link.get("target")
        if src not in node_by_id or dst not in node_by_id or src == dst:
            continue
        key = (src, dst, "allow")
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        cases.append(make_test_case(len(cases) + 1, node_by_id, src, dst, "allow", "Declared node link"))

    block_count = 0
    for src_node in nodes:
        if block_count >= max_block:
            break
        for dst_node in nodes:
            if block_count >= max_block:
                break
            src = src_node["node_id"]
            dst = dst_node["node_id"]
            if src == dst or src_node.get("zone_id") == dst_node.get("zone_id"):
                continue
            if has_path(adjacency, src, dst):
                continue
            key = (src, dst, "block")
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            cases.append(make_test_case(len(cases) + 1, node_by_id, src, dst, "block", "No executable node path"))
            block_count += 1

    return {"template_id": topology.get("template_id", ""), "test_cases": cases}


def make_test_case(idx, node_by_id, src, dst, expected, reason, port=443):
    return {
        "test_id": f"tc{idx}",
        "source_zone": node_by_id[src].get("zone_id", ""),
        "target_zone": node_by_id[dst].get("zone_id", ""),
        "source_node": src,
        "target_node": dst,
        "protocol": "tcp",
        "port": port,
        "expected": expected,
        "reason": reason,
    }


def has_path(adjacency, src, dst):
    if src == dst:
        return True
    queue = deque([src])
    seen = {src}
    while queue:
        cur = queue.popleft()
        for nxt in adjacency.get(cur, set()):
            if nxt == dst:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def normalize_test_cases(topology, test_cases, logger_ref=None):
    """Keep only executable node-to-node test cases and normalize fields."""
    cases = test_cases if isinstance(test_cases, list) else []
    nodes = {str(n.get("node_id", "")): n for n in topology.get("nodes", []) or [] if n.get("node_id")}
    normalized = []
    dropped = 0
    for idx, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            dropped += 1
            continue
        src_node = str(case.get("source_node", "")).strip()
        dst_node = str(case.get("target_node", "")).strip()
        if not src_node or not dst_node or src_node not in nodes or dst_node not in nodes or src_node == dst_node:
            dropped += 1
            continue
        src_zone = str(nodes[src_node].get("zone_id", "")).strip()
        dst_zone = str(nodes[dst_node].get("zone_id", "")).strip()
        expected = str(case.get("expected", "")).strip().lower()
        if expected in {"deny", "blocked"}:
            expected = "block"
        if expected not in {"allow", "block"}:
            dropped += 1
            continue
        normalized.append({
            "test_id": str(case.get("test_id") or f"tc{len(normalized) + 1}"),
            "source_zone": src_zone,
            "target_zone": dst_zone,
            "source_node": src_node,
            "target_node": dst_node,
            "protocol": str(case.get("protocol") or "tcp").strip().lower(),
            "port": case.get("port", 443),
            "expected": expected,
            "reason": str(case.get("reason", "")).strip(),
        })
    if logger_ref and dropped:
        logger_ref.warning(f"Dropped {dropped} non-executable zone-only or invalid test cases")
    return normalized


def ensure_block_test_coverage(topology, test_cases, logger_ref=None, min_block=4, max_total=48):
    """Add a few deterministic deny cases so Stage 5 validates ACL blocking."""
    cases = list(test_cases or [])
    block_count = sum(1 for t in cases if t.get("expected") == "block")
    if block_count >= min_block or len(cases) >= max_total:
        return cases

    nodes = [n for n in topology.get("nodes", []) or [] if n.get("node_id")]
    node_by_id = {n["node_id"]: n for n in nodes}
    adjacency = build_node_adjacency(topology, node_by_id)
    existing = {
        (
            str(t.get("source_node")),
            str(t.get("target_node")),
            str(t.get("protocol", "tcp")).lower(),
            str(t.get("port", 443)),
        )
        for t in cases
    }

    candidates = []
    for src_node in nodes:
        for dst_node in nodes:
            src = src_node["node_id"]
            dst = dst_node["node_id"]
            if src == dst or src_node.get("zone_id") == dst_node.get("zone_id"):
                continue
            if not has_path(adjacency, src, dst):
                continue
            score = block_candidate_score(src_node, dst_node)
            if score > 0:
                candidates.append((score, src, dst))

    added = 0
    for _, src, dst in sorted(candidates, reverse=True):
        if block_count + added >= min_block or len(cases) >= max_total:
            break
        flow = (src, dst, "tcp", "8443")
        if flow in existing:
            continue
        existing.add(flow)
        cases.append(make_test_case(
            len(cases) + 1,
            node_by_id,
            src,
            dst,
            "block",
            "Cross-boundary ACL deny",
            port=8443,
        ))
        added += 1

    if logger_ref and added:
        logger_ref.info(f"Added {added} deterministic block tests for ACL coverage")
    return cases


def build_node_adjacency(topology, node_by_id):
    adjacency = {}
    for link in (topology.get("links", {}) or {}).get("node_links", []) or []:
        src = link.get("source")
        dst = link.get("target")
        if src in node_by_id and dst in node_by_id:
            adjacency.setdefault(src, set()).add(dst)
            adjacency.setdefault(dst, set()).add(src)
    return adjacency


def block_candidate_score(src_node, dst_node):
    src_zone = str(src_node.get("zone_id", "")).lower()
    dst_zone = str(dst_node.get("zone_id", "")).lower()
    src_type = str(src_node.get("normalized_type", "")).lower()
    dst_type = str(dst_node.get("normalized_type", "")).lower()
    src_name = str(src_node.get("name", "")).lower()
    dst_name = str(dst_node.get("name", "")).lower()
    src_text = " ".join([src_zone, src_type, src_name])
    dst_text = " ".join([dst_zone, dst_type, dst_name])

    score = 0
    if any(token in src_text for token in ["external", "pen", "testing"]):
        score += 5
    if any(token in dst_text for token in ["management", "identity", "aaa", "server", "database", "production"]):
        score += 4
    if any(token in src_text for token in ["development", "testing", "nonprod", "non-production"]):
        score += 3
    if any(token in dst_text for token in ["production", "prod"]):
        score += 3
    if "management" in src_text and "management" not in dst_text:
        score += 2
    return score


def process_single_stage4_sample(
    sample,
    ig_level,
    run_dir,
    max_repair_rounds=2,
    include_manual_review=True,
    logger_ref=None,
    reference_topology=None,
):
    """Verify, repair, regenerate test cases, and export XML for one Stage3 sample."""
    cis_map = load_cis_requirements()
    topology = copy.deepcopy(sample.get("completed_topology") or sample.get("repaired_topology") or {})
    topology = validate_topology(topology)

    repair_history = []
    topology, structural_adds, structural_findings = ensure_structural_integrity(
        topology,
        ig_level,
        reference_topology=reference_topology,
        logger_ref=logger_ref,
    )
    if structural_adds.get("zones") or structural_adds.get("nodes") or structural_adds.get("links", {}).get("node_links") or structural_adds.get("links", {}).get("zone_links"):
        diff = build_repair_diff(0, structural_findings, structural_adds, sample.get("completed_topology") or sample.get("repaired_topology") or {}, topology)
        diff["source"] = "stage4_structural_normalization"
        repair_history.append(diff)
        save_repair_diff(run_dir, sample["sample_id"], ig_level, diff)

    verification = verify_cis_compliance(topology, ig_level, cis_map, logger_ref=logger_ref)

    for round_idx in range(1, max_repair_rounds + 1):
        findings = controls_needing_repair(verification, include_manual_review=include_manual_review)
        findings.extend(build_structural_findings(topology, ig_level, reference_topology=reference_topology))
        if not findings:
            break
        if logger_ref:
            logger_ref.info(f"Repair round {round_idx}: {len(findings)} findings need repair")
        topology_before = copy.deepcopy(topology)
        topology, additions = repair_topology_for_cis(topology, findings, ig_level, logger_ref=logger_ref)
        topology, structural_adds, structural_findings = ensure_structural_integrity(
            topology,
            ig_level,
            reference_topology=reference_topology,
            logger_ref=logger_ref,
        )
        additions = merge_addition_records(additions, structural_adds)
        diff = build_repair_diff(round_idx, findings, additions, topology_before, topology)
        repair_history.append(diff)
        save_repair_diff(run_dir, sample["sample_id"], ig_level, diff)
        verification = verify_cis_compliance(topology, ig_level, cis_map, logger_ref=logger_ref)

    test_cases = generate_test_cases(topology, logger_ref=logger_ref)
    xml_file = topology_to_xml(
        topology=topology,
        verification=verification,
        test_cases=test_cases,
        repair_history=repair_history,
        ig_level=ig_level,
        output_dir=Path(run_dir) / "xml" / ig_level,
    )

    return {
        "sample_id": sample["sample_id"],
        "gt_template_id": sample.get("gt_template_id", ""),
        "ig_level": ig_level,
        "final_topology": topology,
        "compliance_check": verification,
        "repair_history": repair_history,
        "test_cases": test_cases,
        "xml_file": str(xml_file),
    }


def process_stage5_diagnostic_repair(
    stage4_result,
    stage5_report,
    ig_level,
    run_dir,
    max_repair_rounds=1,
    logger_ref=None,
    reference_topology=None,
):
    """Repair a prior Stage 4 topology using a Stage 5 validation report."""
    cis_map = load_cis_requirements()
    topology = copy.deepcopy(stage4_result.get("final_topology") or {})
    topology = validate_topology(topology)
    sample_id = stage4_result.get("sample_id") or topology.get("template_id", "sample")
    repair_history = list(stage4_result.get("repair_history", []) or [])

    stage5_findings = build_stage5_repair_findings(stage5_report)
    stage5_findings.extend(build_structural_findings(topology, ig_level, reference_topology=reference_topology))
    if logger_ref:
        logger_ref.info(f"Loaded {len(stage5_findings)} Stage5 diagnostic findings for repair")

    if stage5_findings:
        topology_before = copy.deepcopy(topology)
        topology, additions = repair_topology_for_cis(topology, stage5_findings, ig_level, logger_ref=logger_ref)
        topology, structural_adds, structural_findings = ensure_structural_integrity(
            topology,
            ig_level,
            reference_topology=reference_topology,
            logger_ref=logger_ref,
        )
        additions = merge_addition_records(additions, structural_adds)
        diff = build_repair_diff(len(repair_history) + 1, stage5_findings, additions, topology_before, topology)
        diff["source"] = "stage5_diagnostic_report"
        repair_history.append(diff)
        save_repair_diff(run_dir, sample_id, ig_level, diff)

    verification = verify_cis_compliance(topology, ig_level, cis_map, logger_ref=logger_ref)

    for _ in range(max_repair_rounds):
        findings = controls_needing_repair(verification, include_manual_review=True)
        findings.extend(build_structural_findings(topology, ig_level, reference_topology=reference_topology))
        if not findings:
            break
        if logger_ref:
            logger_ref.info(f"Post-Stage5 CIS repair: {len(findings)} findings need repair")
        topology_before = copy.deepcopy(topology)
        topology, additions = repair_topology_for_cis(topology, findings, ig_level, logger_ref=logger_ref)
        topology, structural_adds, structural_findings = ensure_structural_integrity(
            topology,
            ig_level,
            reference_topology=reference_topology,
            logger_ref=logger_ref,
        )
        additions = merge_addition_records(additions, structural_adds)
        diff = build_repair_diff(len(repair_history) + 1, findings, additions, topology_before, topology)
        diff["source"] = "post_stage5_cis_verification"
        repair_history.append(diff)
        save_repair_diff(run_dir, sample_id, ig_level, diff)
        verification = verify_cis_compliance(topology, ig_level, cis_map, logger_ref=logger_ref)

    test_cases = generate_test_cases(topology, logger_ref=logger_ref)
    xml_file = topology_to_xml(
        topology=topology,
        verification=verification,
        test_cases=test_cases,
        repair_history=repair_history,
        ig_level=ig_level,
        output_dir=Path(run_dir) / "xml" / ig_level,
    )

    return {
        "sample_id": sample_id,
        "gt_template_id": stage4_result.get("gt_template_id", ""),
        "ig_level": ig_level,
        "final_topology": topology,
        "compliance_check": verification,
        "repair_history": repair_history,
        "test_cases": test_cases,
        "stage5_diagnostic_summary": stage5_report.get("summary", {}) if isinstance(stage5_report, dict) else {},
        "xml_file": str(xml_file),
    }


def build_repair_diff(round_idx, findings, additions, before, after):
    """Build an auditable repair diff record."""
    before_counts = topology_counts(before)
    after_counts = topology_counts(after)
    return {
        "round": round_idx,
        "controls_repaired": [f.get("cis_safeguard_id") for f in findings],
        "findings_before": findings,
        "additions": additions,
        "counts_before": before_counts,
        "counts_after": after_counts,
        "count_delta": {k: after_counts[k] - before_counts[k] for k in before_counts},
    }


def topology_counts(topology):
    links = topology.get("links", {}) or {}
    return {
        "zones": len(topology.get("zones", []) or []),
        "nodes": len(topology.get("nodes", []) or []),
        "zone_links": len(links.get("zone_links", []) or []),
        "node_links": len(links.get("node_links", []) or []),
    }


def save_repair_diff(run_dir, sample_id, ig_level, diff):
    diff_dir = Path(run_dir) / "repair_diffs" / ig_level
    diff_dir.mkdir(parents=True, exist_ok=True)
    save_json(diff, diff_dir, f"{sample_id}_round{diff['round']}_diff.json")


def topology_to_xml(topology, verification, test_cases, repair_history, ig_level, output_dir):
    """Convert final topology and Stage4 artifacts to XML."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    root = ET.Element("Topology")
    root.set("schema_version", str(topology.get("schema_version", "2.1")))
    root.set("template_id", str(topology.get("template_id", "")))
    root.set("title", str(topology.get("title", "")))
    root.set("scenario", str(topology.get("scenario", "")))
    root.set("ig_level", ig_level)

    ET.SubElement(root, "Description").text = str(topology.get("description", ""))
    ET.SubElement(root, "Summary").text = str(topology.get("topology_summary", ""))

    zones_by_id = {z.get("zone_id", ""): z for z in topology.get("zones", []) or []}
    domains_el = ET.SubElement(root, "Domains")
    for zone in topology.get("zones", []) or []:
        domain_el = ET.SubElement(domains_el, "Domain")
        domain_el.set("id", str(zone.get("zone_id", "")))
        domain_el.set("name", str(zone.get("name", "")))
        domain_el.set("type", str(zone.get("zone_type", "")))

    nodes_el = ET.SubElement(root, "Nodes")
    for node in topology.get("nodes", []) or []:
        zone_id = str(node.get("zone_id", ""))
        node_el = ET.SubElement(nodes_el, "Node")
        node_el.set("id", str(node.get("node_id", "")))
        node_el.set("name", str(node.get("name", "")))
        node_el.set("domain", zone_id)
        node_el.set("domain_name", str(zones_by_id.get(zone_id, {}).get("name", "")))
        node_el.set("type", str(node.get("normalized_type", "")))
        node_el.set("ip", str(node.get("ip", "")))

    links_el = ET.SubElement(root, "Links")
    zone_links_el = ET.SubElement(links_el, "ZoneLinks")
    node_links_el = ET.SubElement(links_el, "NodeLinks")
    links = topology.get("links", {}) or {}
    for link in links.get("zone_links", []) or []:
        link_el = ET.SubElement(zone_links_el, "Link")
        set_link_attrs(link_el, link)
    for link in links.get("node_links", []) or []:
        link_el = ET.SubElement(node_links_el, "Link")
        set_link_attrs(link_el, link)

    policies_el = ET.SubElement(root, "Policies")
    for idx, case in enumerate(test_cases.get("test_cases", []) or [], 1):
        tag = "Allow" if case.get("expected") == "allow" else "Deny"
        case_el = ET.SubElement(policies_el, tag)
        case_el.set("id", str(case.get("test_id") or f"tc{idx}"))
        case_el.set("src_zone", str(case.get("source_zone", "")))
        case_el.set("dst_zone", str(case.get("target_zone", "")))
        case_el.set("src_node", str(case.get("source_node", "")))
        case_el.set("dst_node", str(case.get("target_node", "")))
        case_el.set("protocol", str(case.get("protocol", "")))
        case_el.set("port", str(case.get("port", "")))
        ET.SubElement(case_el, "Reason").text = str(case.get("reason", ""))

    compliance_el = ET.SubElement(root, "Compliance")
    compliance_el.set("ig_level", ig_level)
    compliance_el.set("all_required_satisfied", str(verification.get("all_required_satisfied", False)).lower())
    for control in verification.get("controls", []) or []:
        control_el = ET.SubElement(compliance_el, "Control")
        control_el.set("id", str(control.get("cis_safeguard_id", "")))
        control_el.set("title", str(control.get("title", "")))
        control_el.set("status", str(control.get("status", "")))
        control_el.set("confidence", str(control.get("confidence", "")))
        ET.SubElement(control_el, "Evidence").text = str(control.get("evidence", ""))
        ET.SubElement(control_el, "MissingReason").text = str(control.get("missing_reason", ""))
        ET.SubElement(control_el, "Remediation").text = str(control.get("repair_suggestion", ""))

    repairs_el = ET.SubElement(root, "RepairHistory")
    for diff in repair_history:
        repair_el = ET.SubElement(repairs_el, "Repair")
        repair_el.set("round", str(diff.get("round", "")))
        repair_el.set("controls", ",".join(diff.get("controls_repaired", []) or []))
        repair_el.set("zones_added", str(len(diff.get("additions", {}).get("zones", []) or [])))
        repair_el.set("nodes_added", str(len(diff.get("additions", {}).get("nodes", []) or [])))
        links = diff.get("additions", {}).get("links", {}) or {}
        repair_el.set("zone_links_added", str(len(links.get("zone_links", []) or [])))
        repair_el.set("node_links_added", str(len(links.get("node_links", []) or [])))

    ET.SubElement(root, "DataFlows")
    ET.SubElement(root, "Risks")

    rough = ET.tostring(root, encoding="utf-8")
    xml_str = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    xml_str = "\n".join(line for line in xml_str.splitlines() if line.strip())
    xml_path = output_dir / f"{topology.get('template_id', 'topology')}_{ig_level}.xml"
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_str)
    return xml_path


def set_link_attrs(link_el, link):
    link_el.set("id", str(link.get("link_id", "")))
    link_el.set("source", str(link.get("source", "")))
    link_el.set("target", str(link.get("target", "")))
    link_el.set("type", str(link.get("link_type", "")))
    link_el.set("label", str(link.get("label", "")))


def evaluate_stage4_results(results, logger_ref=None):
    total = len(results)
    satisfied = sum(1 for r in results if r.get("compliance_check", {}).get("all_required_satisfied"))
    repair_rounds = [len(r.get("repair_history", [])) for r in results]
    test_counts = [len(r.get("test_cases", {}).get("test_cases", [])) for r in results]
    metrics = {
        "total_samples": total,
        "all_required_satisfied_count": satisfied,
        "all_required_satisfied_rate": round(satisfied / total, 4) if total else 0,
        "avg_repair_rounds": round(sum(repair_rounds) / total, 2) if total else 0,
        "avg_test_cases": round(sum(test_counts) / total, 2) if total else 0,
    }
    if logger_ref:
        logger_ref.info(
            f"Stage4 metrics: satisfied={metrics['all_required_satisfied_rate']:.1%}, "
            f"avg_repair_rounds={metrics['avg_repair_rounds']}, avg_test_cases={metrics['avg_test_cases']}"
        )
    return metrics


def generate_stage4_report(metrics):
    return "\n".join([
        "# Stage 4 Verification and XML Export Report",
        "",
        f"- Total samples: {metrics['total_samples']}",
        f"- All-required-satisfied count: {metrics['all_required_satisfied_count']}",
        f"- All-required-satisfied rate: {metrics['all_required_satisfied_rate']:.1%}",
        f"- Avg repair rounds: {metrics['avg_repair_rounds']}",
        f"- Avg test cases: {metrics['avg_test_cases']}",
        "",
    ])
