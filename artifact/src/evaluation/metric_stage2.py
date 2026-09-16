#!/usr/bin/env python3
"""Compute Stage 2 retrieval metrics for multiple baselines on the reference set."""

import csv
import json
import math
from pathlib import Path

import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_DIR = PROJECT_ROOT / "main"
sys.path.insert(0, str(MAIN_DIR))

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from Stage2_utils import (
    COLLECTION_NAME,
    QDRANT_URL,
    RETRIEVAL_WEIGHTS,
    build_query_texts,
    call_embedding_api,
)


REFERENCE_DATASET = PROJECT_ROOT / "dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"
STAGE1_EXTRACTED = PROJECT_ROOT / "logs/stable_outputs/stage1_output/reference_train/stage1_extracted_intents.json"
STAGE1_GENERATED = PROJECT_ROOT / "logs/stable_outputs/stage1_output/reference_train/stage1_generated_templates.json"
STAGE2_WEIGHTED_ACCURACY = PROJECT_ROOT / "logs/stable_outputs/stage2_output/reference_train/stage2_accuracy.json"
OUTPUT_DIR = PROJECT_ROOT / "evaluation/metrics_outputs"


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def chunked(items, size=16):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def embedding_batch(texts):
    embeddings = []
    for batch in chunked(texts, 16):
        embeddings.extend(call_embedding_api(batch))
    return embeddings


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


def build_reference_index(reference_templates):
    texts = [flatten_template_text(t) for t in reference_templates]
    embeddings = embedding_batch(texts)
    return [
        {
            "template_id": template["template_id"],
            "text": text,
            "embedding": embedding,
        }
        for template, text, embedding in zip(reference_templates, texts, embeddings)
    ]


def unique_templates_by_id(templates):
    unique = {}
    for template in templates:
        tid = template.get("template_id")
        if tid and tid not in unique:
            unique[tid] = template
    return list(unique.values())


def retrieve_single_vector(query_template, reference_index, top_k=5):
    query_embedding = embedding_batch([flatten_template_text(query_template)])[0]
    scored = []
    for item in reference_index:
        score = cosine_similarity(query_embedding, item["embedding"])
        scored.append((item["template_id"], score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "template_id": tid,
            "score": round(score, 4),
        }
        for tid, score in scored[:top_k]
    ]


def search_multi_field(client, query_template, weights, top_k=5):
    query_texts = build_query_texts(query_template)
    if weights == "uniform":
        candidate_fields = list(RETRIEVAL_WEIGHTS.keys())
    else:
        candidate_fields = list(weights.keys())
    active_fields = [field for field in candidate_fields if query_texts.get(field)]
    if not active_fields:
        return []

    if weights == "uniform":
        field_weights = {field: 1.0 / len(active_fields) for field in active_fields}
    else:
        field_weights = {field: weights[field] for field in active_fields}

    query_embeddings = embedding_batch([query_texts[field] for field in active_fields])
    template_scores = {}
    template_field_scores = {}

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
            weighted_score = hit.score * field_weights[fname]
            template_scores[tid] = template_scores.get(tid, 0.0) + weighted_score
            template_field_scores.setdefault(tid, {})[fname] = hit.score

    sorted_templates = sorted(template_scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {
            "template_id": tid,
            "score": round(score, 4),
            "field_scores": {f: round(s, 4) for f, s in template_field_scores[tid].items()},
        }
        for tid, score in sorted_templates[:top_k]
    ]


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


def load_reference_samples():
    extracted = load_json(STAGE1_EXTRACTED, [])
    generated = load_json(STAGE1_GENERATED, [])
    dataset = load_json(REFERENCE_DATASET, {})
    if len(extracted) != len(generated):
        raise RuntimeError(
            f"Stage1 reference artifacts mismatch: extracted={len(extracted)} generated={len(generated)}"
        )
    samples = []
    for idx, (meta, template) in enumerate(zip(extracted, generated)):
        samples.append({
            "sample_id": meta["sample_id"],
            "template_id": meta["template_id"],
            "generated_template": template,
            "source_dataset_file": str(REFERENCE_DATASET),
        })
    return samples, dataset


def run_variant(variant_name, samples, reference_templates, client=None):
    retrieval_results = []
    if variant_name == "single_vector":
        reference_index = build_reference_index(reference_templates)
        for sample in samples:
            retrieved = retrieve_single_vector(sample["generated_template"], reference_index, top_k=5)
            retrieval_results.append({
                "sample_id": sample["sample_id"],
                "gt_template_id": sample["template_id"],
                "retrieved_templates": retrieved,
            })
    elif variant_name == "multi_field_uniform":
        if client is None:
            raise RuntimeError("Qdrant client is required for multi-field retrieval")
        for sample in samples:
            retrieved = search_multi_field(client, sample["generated_template"], "uniform", top_k=5)
            retrieval_results.append({
                "sample_id": sample["sample_id"],
                "gt_template_id": sample["template_id"],
                "retrieved_templates": retrieved,
            })
    elif variant_name == "multi_field_weighted":
        if client is None:
            raise RuntimeError("Qdrant client is required for multi-field retrieval")
        for sample in samples:
            retrieved = search_multi_field(client, sample["generated_template"], RETRIEVAL_WEIGHTS, top_k=5)
            retrieval_results.append({
                "sample_id": sample["sample_id"],
                "gt_template_id": sample["template_id"],
                "retrieved_templates": retrieved,
            })
    else:
        raise ValueError(f"Unknown variant: {variant_name}")

    return compute_metrics(retrieval_results), retrieval_results


def main():
    samples, dataset = load_reference_samples()
    reference_templates = unique_templates_by_id([
        item["ground_truth"]["topology_template"]
        for item in load_json(REFERENCE_DATASET, {}).get("items", [])
    ])
    if not reference_templates:
        raise RuntimeError(f"No reference templates found in {REFERENCE_DATASET}")

    client = QdrantClient(url=QDRANT_URL)
    try:
        collection_info = client.get_collection(COLLECTION_NAME)
    except Exception as exc:
        raise RuntimeError(f"Failed to access Qdrant collection {COLLECTION_NAME} at {QDRANT_URL}: {exc}") from exc
    _ = collection_info.points_count
    _ = call_embedding_api(["stage2 dependency check"])

    variants = {}
    details_rows = []

    for variant in ("single_vector", "multi_field_uniform", "multi_field_weighted"):
        metrics, retrieval_results = run_variant(
            variant,
            samples,
            reference_templates,
            client=client,
        )
        variants[variant] = {
            "top_k": metrics["top_k"],
            "mrr": metrics["mrr"],
            "avg_hit_rank": metrics["avg_hit_rank"],
            "miss_count": metrics["miss_count"],
        }
        for d in metrics["details"]:
            details_rows.append({
                "variant": variant,
                "sample_id": d["sample_id"],
                "gt_template_id": d["gt_template_id"],
                "hit_rank": d["hit_rank"],
                "top1": int(d["hit_rank"] is not None and d["hit_rank"] <= 1),
                "top3": int(d["hit_rank"] is not None and d["hit_rank"] <= 3),
                "top5": int(d["hit_rank"] is not None and d["hit_rank"] <= 5),
                "reciprocal_rank": round(1 / d["hit_rank"], 4) if d["hit_rank"] else 0.0,
                "retrieved_top5": ";".join(d["retrieved_top5"]),
            })
        write_json(OUTPUT_DIR / f"metric_stage2_{variant}_retrieval.json", retrieval_results)

    summary = {
        "metric": "stage2_retrieval_reference_set",
        "reference_dataset": str(REFERENCE_DATASET),
        "stage1_extracted_source": str(STAGE1_EXTRACTED),
        "stage1_generated_source": str(STAGE1_GENERATED),
        "stage2_weighted_stable_source": str(STAGE2_WEIGHTED_ACCURACY),
        "reference_samples": len(dataset.get("items", [])),
        "reference_templates": len(reference_templates),
        "variants": variants,
        "note": "single_vector embeds a single flattened text representation of the Stage 1 generated topology and retrieves against one flattened text per reference template; multi-field_uniform uses equal weights across active fields; multi-field_weighted uses the previous weighted retrieval scheme.",
    }

    write_json(OUTPUT_DIR / "metric_stage2_summary.json", summary)
    write_csv(OUTPUT_DIR / "metric_stage2_details.csv", details_rows)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
