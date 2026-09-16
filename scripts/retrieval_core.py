# Pure functions copied verbatim from the retained original implementation.

import math

# Origin: main/Stage2_utils.py

def build_query_texts(template):
    """Build query texts for each retrieval field from Stage 1 template.
    Only uses fields actually extracted by Stage 1."""
    zone_types = [z.get("zone_type", "") for z in template.get("zones", []) if z.get("zone_type")]
    node_types = sorted({n.get("normalized_type", "") for n in template.get("nodes", []) if n.get("normalized_type")})
    security_parts = []
    if zone_types:
        security_parts.append(f"zone types: {', '.join(zone_types)}")
    if node_types:
        security_parts.append(f"node types: {', '.join(node_types)}")

    return {
        "scenario": template.get("scenario", ""),
        "security": "; ".join(security_parts),
        "description": template.get("description", ""),
        "topology_summary": template.get("topology_summary", ""),
        "title": template.get("title", ""),
    }

# Origin: evaluation/metric_stage2.py

def flatten_template_text(template):
    zones = template.get("zones", []) or []
    nodes = template.get("nodes", []) or []
    links = template.get("links", {}) or {}
    node_links = links.get("node_links", []) or []
    zone_links = links.get("zone_links", []) or []

    parts = [
        f"template_id: {template.get('template_id', '')}",
        f"title: {template.get('title', '')}",
        f"scenario: {template.get('scenario', '')}",
        f"description: {template.get('description', '')}",
        f"topology_summary: {template.get('topology_summary', '')}",
        "zones:",
    ]
    for zone in zones:
        parts.append(
            f"- {zone.get('zone_id', '')} | {zone.get('name', '')} | {zone.get('zone_type', '')}"
        )
    parts.append("nodes:")
    for node in nodes:
        parts.append(
            f"- {node.get('node_id', '')} | {node.get('name', '')} | {node.get('normalized_type', '')} | zone={node.get('zone_id', '')}"
        )
    parts.append("node_links:")
    for link in node_links:
        parts.append(
            f"- {link.get('link_id', '')} | {link.get('source', '')}->{link.get('target', '')} | {link.get('link_type', '')} | {link.get('label', '')}"
        )
    parts.append("zone_links:")
    for link in zone_links:
        parts.append(
            f"- {link.get('link_id', '')} | {link.get('source', '')}->{link.get('target', '')} | {link.get('link_type', '')} | {link.get('label', '')}"
        )
    return "\n".join(parts)

# Origin: evaluation/metric_stage2.py

def unique_templates_by_id(templates):
    unique = {}
    for template in templates:
        tid = template.get("template_id")
        if tid and tid not in unique:
            unique[tid] = template
    return list(unique.values())

# Origin: evaluation/metric_stage2.py

def compute_metrics(results, top_k_values=(1, 3, 5)):
    metrics = {
        "top_k": {f"top_{k}": 0.0 for k in top_k_values},
        "mrr": 0.0,
        "avg_hit_rank": None,
        "miss_count": 0,
        "details": [],
    }

    if not results:
        return metrics

    reciprocal_ranks = []
    hit_ranks = []
    for item in results:
        gt_tid = item["gt_template_id"]
        retrieved_tids = [t["template_id"] for t in item["retrieved_templates"][:5]]
        rank = None
        for idx, tid in enumerate(retrieved_tids, 1):
            if tid == gt_tid:
                rank = idx
                break
        metrics["details"].append({
            "sample_id": item["sample_id"],
            "gt_template_id": gt_tid,
            "retrieved_top5": retrieved_tids,
            "hit_rank": rank,
        })
        if rank:
            reciprocal_ranks.append(1 / rank)
            hit_ranks.append(rank)

    for k in top_k_values:
        correct = 0
        for item in results:
            gt_tid = item["gt_template_id"]
            retrieved_tids = [t["template_id"] for t in item["retrieved_templates"][:k]]
            if gt_tid in retrieved_tids:
                correct += 1
        metrics["top_k"][f"top_{k}"] = round(correct / len(results), 4)

    metrics["mrr"] = round(sum(reciprocal_ranks) / len(results), 4)
    metrics["avg_hit_rank"] = round(sum(hit_ranks) / len(hit_ranks), 2) if hit_ranks else None
    metrics["miss_count"] = len(results) - len(hit_ranks)
    return metrics

# Origin: dataset/vector_dataset/build_vector_database.py

def build_field_texts(template):
    """Build 6 field texts for one template."""
    title = template.get("title", "")
    description = template.get("description", "")
    scenario = template.get("scenario", "")
    topology_summary = template.get("topology_summary", "")

    # security field: zones + node types + link types
    zone_types = [z.get("zone_type", "") for z in template.get("zones", [])]
    node_types = [n.get("normalized_type", "") for n in template.get("nodes", [])]
    links = template.get("links", {})
    node_link_types = [l.get("link_type", "") for l in links.get("node_links", [])]
    zone_link_types = [l.get("link_type", "") for l in links.get("zone_links", [])]
    security_parts = []
    if zone_types:
        security_parts.append(f"zone types: {', '.join(zone_types)}")
    if node_types:
        security_parts.append(f"node types: {', '.join(set(node_types))}")
    if node_link_types:
        security_parts.append(f"node link types: {', '.join(set(node_link_types))}")
    if zone_link_types:
        security_parts.append(f"zone link types: {', '.join(set(zone_link_types))}")
    security = "; ".join(security_parts)

    # cis field: matched CIS rule titles
    # Note: cis_rules is in ground_truth, not in topology_template
    # We need to pass it separately
    return {
        "title": title,
        "description": description,
        "scenario": scenario,
        "topology_summary": topology_summary,
        "security": security,
        "cis": "",  # filled later
    }
