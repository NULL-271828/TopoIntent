#!/usr/bin/env python3
"""
Stage 2: Single-Vector Retrieval
Retrieve similar topology templates from vector database based on Stage 1 output.
The active pipeline flattens the Stage 1 topology into one text representation
and ranks reference templates by single-vector cosine similarity.
"""

import json
import os
import requests
import logging
import time
import math
from pathlib import Path

# Clear proxy env vars so local Qdrant/embedding services are reached directly.
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

# === Config ===
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION_NAME = "topo_intent_train"
EMBEDDING_API = os.getenv("EMBEDDING_API", "http://127.0.0.1:8007/v1/embeddings")
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
TRAIN_JSON = Path(__file__).resolve().parent.parent / "dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from logging_utils import setup_logger, save_json, save_markdown

# Legacy multi-field retrieval settings. These are kept for metric ablations and
# backward compatibility, but retrieve_for_template() uses single-vector search
# in the active pipeline.
RETRIEVAL_WEIGHTS = {
    "scenario": 0.30,
    "security": 0.25,
    "description": 0.18,
    "topology_summary": 0.17,
    "title": 0.10,
}
FIELD_NAMES = list(RETRIEVAL_WEIGHTS.keys())

logger = None
_SINGLE_VECTOR_INDEX = None


def call_embedding_api(texts):
    """Get embeddings from vllm BGE-M3."""
    if not texts:
        return []
    payload = {"model": MODEL_NAME, "input": texts}
    try:
        resp = requests.post(
            EMBEDDING_API,
            json=payload,
            timeout=60,
            proxies={"http": "", "https": ""},
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = resp.text[:500] if "resp" in locals() else ""
        raise RuntimeError(
            f"Embedding API request failed: url={EMBEDDING_API}, "
            f"model={MODEL_NAME}, status={getattr(resp, 'status_code', 'unknown')}, "
            f"body={body!r}"
        ) from e
    except requests.RequestException as e:
        raise RuntimeError(
            f"Embedding API request failed: url={EMBEDDING_API}, model={MODEL_NAME}, error={e}"
        ) from e

    data = resp.json()
    embeddings = [item["embedding"] for item in data.get("data", [])]
    if len(embeddings) != len(texts):
        raise RuntimeError(f"Embedding API returned {len(embeddings)} embeddings for {len(texts)} inputs")
    return embeddings


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


def cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def load_reference_templates():
    with open(TRAIN_JSON, encoding="utf-8") as f:
        data = json.load(f)
    templates = {}
    for item in data.get("items", []):
        tid = item.get("template_id")
        if tid and tid not in templates:
            templates[tid] = item["ground_truth"]["topology_template"]
    return list(templates.values())


def build_single_vector_index():
    templates = load_reference_templates()
    texts = [flatten_template_text(t) for t in templates]
    embeddings = call_embedding_api(texts)
    return [
        {
            "template_id": template["template_id"],
            "text": text,
            "embedding": embedding,
        }
        for template, text, embedding in zip(templates, texts, embeddings)
    ]


def get_single_vector_index():
    global _SINGLE_VECTOR_INDEX
    if _SINGLE_VECTOR_INDEX is None:
        _SINGLE_VECTOR_INDEX = build_single_vector_index()
    return _SINGLE_VECTOR_INDEX


def search_template_single_vector(stage1_template, top_k=5):
    reference_index = get_single_vector_index()
    query_embedding = call_embedding_api([flatten_template_text(stage1_template)])[0]
    scored = []
    for item in reference_index:
        scored.append((item["template_id"], cosine_similarity(query_embedding, item["embedding"])))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "template_id": tid,
            "score": round(score, 4),
            "retrieval_mode": "single_vector",
        }
        for tid, score in scored[:top_k]
    ]


def search_template_multi_field(client, query_texts, top_k=5):
    """
    Search vector database with weighted multi-field retrieval.

    Args:
        client: QdrantClient instance
        query_texts: dict of field_name -> text
        top_k: number of templates to return

    Returns:
        list of dicts: [{"template_id": str, "score": float, "field_scores": dict}, ...]
    """
    # Get embeddings only for populated query fields. This avoids spending calls
    # on unavailable Stage 1 fields and keeps field/embedding indices aligned.
    active_fields = [f for f in FIELD_NAMES if query_texts.get(f)]
    query_embeddings = call_embedding_api([query_texts[f] for f in active_fields])

    # Search per field and aggregate scores
    template_scores = {}
    template_field_scores = {}
    template_weighted_scores = {}

    for i, fname in enumerate(active_fields):
        results = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_embeddings[i],
            query_filter=Filter(
                must=[FieldCondition(key="field_name", match=MatchValue(value=fname))]
            ),
            limit=50,
        ).points

        for hit in results:
            tid = hit.payload["template_id"]
            weighted_score = hit.score * RETRIEVAL_WEIGHTS[fname]

            if tid not in template_scores:
                template_scores[tid] = 0.0
                template_field_scores[tid] = {}
                template_weighted_scores[tid] = {}
            template_scores[tid] += weighted_score
            template_field_scores[tid][fname] = hit.score
            template_weighted_scores[tid][fname] = weighted_score

    # Sort by total weighted score
    sorted_templates = sorted(template_scores.items(), key=lambda x: x[1], reverse=True)

    return [
        {
            "template_id": tid,
            "score": round(score, 4),
            "field_scores": {
                f: round(s, 4) for f, s in template_field_scores[tid].items()
            },
            "weighted_field_scores": {
                f: round(s, 4) for f, s in template_weighted_scores[tid].items()
            },
        }
        for tid, score in sorted_templates[:top_k]
    ]


def retrieve_for_template(client, stage1_template, top_k=5, logger_ref=None):
    """
    Retrieve top-k similar templates for a single Stage 1 output.

    Args:
        client: QdrantClient instance
        stage1_template: dict from Stage 1 output
        top_k: number of results
        logger_ref: optional logger

    Returns:
        list of retrieval results
    """
    if logger_ref:
        query_texts = build_query_texts(stage1_template)
        logger_ref.debug(f"Query fields: scenario={query_texts['scenario']}, "
                         f"zones={len(stage1_template.get('zones', []))}, "
                         f"nodes={len(stage1_template.get('nodes', []))}")
    return search_template_single_vector(stage1_template, top_k)


def compute_retrieval_accuracy(all_results, top_k_list=None, logger_ref=None):
    """
    Compute retrieval accuracy at different k values.

    Args:
        all_results: list of dicts with sample_id, gt_template_id, retrieved_templates
        top_k_list: list of k values to evaluate
        logger_ref: optional logger

    Returns:
        dict with accuracy metrics
    """
    if top_k_list is None:
        top_k_list = [1, 2, 3, 4, 5]

    metrics = {"top_k": {}, "mrr": 0.0, "avg_hit_rank": None, "miss_count": 0, "details": []}

    for k in top_k_list:
        correct = 0
        for r in all_results:
            gt_tid = r["gt_template_id"]
            retrieved_tids = [t["template_id"] for t in r["retrieved_templates"][:k]]
            if gt_tid in retrieved_tids:
                correct += 1
        metrics["top_k"][f"top_{k}"] = round(correct / len(all_results), 4) if all_results else 0

    # Per-sample details
    reciprocal_ranks = []
    hit_ranks = []
    for r in all_results:
        gt_tid = r["gt_template_id"]
        retrieved_tids = [t["template_id"] for t in r["retrieved_templates"][:5]]
        rank = None
        for i, tid in enumerate(retrieved_tids):
            if tid == gt_tid:
                rank = i + 1
                break
        metrics["details"].append({
            "sample_id": r["sample_id"],
            "gt_template_id": gt_tid,
            "retrieved_top5": retrieved_tids,
            "hit_rank": rank,
        })
        if rank:
            reciprocal_ranks.append(1 / rank)
            hit_ranks.append(rank)

    metrics["mrr"] = round(sum(reciprocal_ranks) / len(all_results), 4) if all_results else 0
    metrics["avg_hit_rank"] = round(sum(hit_ranks) / len(hit_ranks), 2) if hit_ranks else None
    metrics["miss_count"] = len(all_results) - len(hit_ranks)

    if logger_ref:
        for k in top_k_list:
            acc = metrics["top_k"][f"top_{k}"]
            logger_ref.info(f"Top-{k} accuracy: {acc:.1%}")
        logger_ref.info(f"MRR: {metrics['mrr']:.1%}; avg_hit_rank={metrics['avg_hit_rank']}; misses={metrics['miss_count']}")

    return metrics


def generate_retrieval_report(metrics):
    """Generate markdown retrieval accuracy report."""
    lines = [
        "# Stage 2 Retrieval Accuracy Report",
        "",
        "## Summary",
    ]
    for k, acc in metrics["top_k"].items():
        lines.append(f"- {k} accuracy: {acc:.1%}")
    lines.append(f"- MRR: {metrics['mrr']:.1%}")
    lines.append(f"- Avg hit rank: {metrics['avg_hit_rank']}")
    lines.append(f"- Miss count: {metrics['miss_count']}")

    lines.extend([
        "",
        "## Per-Sample Details",
        "",
        "| Sample | GT Template | Hit Rank | Top-5 Retrieved |",
        "|--------|-------------|----------|-----------------|",
    ])

    for d in metrics["details"]:
        rank_str = str(d["hit_rank"]) if d["hit_rank"] else "❌ Miss"
        top5 = ", ".join(d["retrieved_top5"])
        lines.append(f"| {d['sample_id']} | {d['gt_template_id']} | {rank_str} | {top5} |")

    lines.append("")
    return "\n".join(lines)


def retrieve_all_from_json(stage1_results, run_dir=None, top_k=5, logger_ref=None):
    """
    Run retrieval for all Stage 1 results and compute accuracy.

    Args:
        stage1_results: list from Stage 1 output
        run_dir: optional Path for saving results
        top_k: number of templates to retrieve per sample
        logger_ref: optional logger

    Returns:
        tuple: (retrieval_results, accuracy_metrics)
    """
    global logger
    logger = logger_ref

    try:
        call_embedding_api(["stage2 dependency check"])
        if logger:
            logger.info(f"Connected to embedding API: {EMBEDDING_API} model={MODEL_NAME}")
    except Exception as e:
        raise RuntimeError(f"Failed to access embedding API before retrieval: {e}") from e

    retrieval_results = []
    query_details = []
    failures = []
    for r in stage1_results:
        started = time.time()
        sample_id = r["sample_id"]
        gt_tid = r["template_id"]
        stage1_template = r["generated_template"]

        if logger:
            logger.info(f"Retrieving for: {sample_id}")

        query_texts = build_query_texts(stage1_template)
        try:
            retrieved = search_template_single_vector(stage1_template, top_k=top_k)
            status = "ok"
            error = None
        except Exception as e:
            retrieved = []
            status = "failed"
            error = str(e)
            failures.append({"sample_id": sample_id, "error": error})
            if logger:
                logger.error(f"Failed to retrieve for {sample_id}: {e}")

        retrieval_results.append({
            "sample_id": sample_id,
            "gt_template_id": gt_tid,
            "status": status,
            "retrieved_templates": retrieved,
        })
        query_details.append({
            "sample_id": sample_id,
            "gt_template_id": gt_tid,
            "status": status,
            "elapsed_sec": round(time.time() - started, 3),
            "error": error,
            "query_texts": query_texts,
        })

        if run_dir:
            save_json(retrieval_results, run_dir, "stage2_retrieval_results_partial.json")
            save_json(query_details, run_dir, "stage2_query_details_partial.json")

    # Compute accuracy
    if logger:
        logger.info("Computing retrieval accuracy...")
    metrics = compute_retrieval_accuracy(retrieval_results, logger_ref=logger)

    # Save results
    if run_dir:
        save_json(retrieval_results, run_dir, "stage2_retrieval_results.json")
        save_json(query_details, run_dir, "stage2_query_details.json")
        save_json(failures, run_dir, "stage2_failures.json")
        save_json(metrics, run_dir, "stage2_accuracy.json")
        save_markdown(generate_retrieval_report(metrics), run_dir, "stage2_accuracy_report.md")
        if logger:
            logger.info(f"Results saved to: {run_dir}")

    return retrieval_results, metrics


if __name__ == "__main__":
    from Stage2_retrieval import main

    main()
