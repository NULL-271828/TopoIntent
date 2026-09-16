#!/usr/bin/env python3
"""
Stage 0: Build vector database for TopoIntent project.
- Reads training data from Stage4_synthetic_intent_train.json
- Deduplicates by template_id (one record per template)
- Builds 6 field texts per template: title, description, scenario, topology_summary, security, cis
- Generates embeddings via vllm-hosted BGE-M3
- Stores in Qdrant collection: topo_intent_train
"""

import json
import os
import requests
import time
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

# === Config ===
BASE_DIR = Path(__file__).resolve().parent
TRAIN_JSON = BASE_DIR / "../topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION_NAME = "topo_intent_train"
EMBEDDING_API = os.getenv("EMBEDDING_API", "http://127.0.0.1:8007/v1/embeddings")
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
BATCH_SIZE = 8  # embedding batch size
WEIGHTS = {
    "title": 0.20,
    "description": 0.15,
    "scenario": 0.10,
    "topology_summary": 0.20,
    "security": 0.20,
    "cis": 0.15,
}

FIELD_NAMES = list(WEIGHTS.keys())


def load_templates(path):
    """Load JSON and deduplicate by template_id."""
    with open(path) as f:
        data = json.load(f)
    templates = {}
    for item in data["items"]:
        tid = item["template_id"]
        if tid not in templates:
            templates[tid] = item["ground_truth"]["topology_template"]
    print(f"Loaded {len(data['items'])} samples, {len(templates)} unique templates")
    return templates


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


def build_cis_text(ground_truth):
    """Extract matched CIS rule titles."""
    cis_rules = ground_truth.get("cis_rules", {})
    matched = cis_rules.get("matched_cis_candidates", [])
    titles = [m.get("title", "") for m in matched]
    return "; ".join(titles)


def get_embeddings(texts):
    """Get embeddings from vllm BGE-M3."""
    payload = {"model": MODEL_NAME, "input": texts}
    resp = requests.post(EMBEDDING_API, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    embeddings = [item["embedding"] for item in data["data"]]
    return embeddings


def main():
    # 1. Load templates
    templates = load_templates(TRAIN_JSON)

    # Re-read to get ground_truth for CIS
    with open(TRAIN_JSON) as f:
        raw_data = json.load(f)
    gt_by_tid = {}
    for item in raw_data["items"]:
        tid = item["template_id"]
        if tid not in gt_by_tid:
            gt_by_tid[tid] = item["ground_truth"]

    # 2. Build field texts
    all_points = []
    all_texts = []  # for batch embedding
    text_meta = []  # (template_id, field_name) mapping

    for tid, tmpl in templates.items():
        fields = build_field_texts(tmpl)
        fields["cis"] = build_cis_text(gt_by_tid[tid])

        for fname in FIELD_NAMES:
            text = fields[fname]
            all_texts.append(text)
            text_meta.append((tid, fname))

    print(f"Total field texts to embed: {len(all_texts)}")

    # 3. Generate embeddings in batches
    all_embeddings = []
    for i in range(0, len(all_texts), BATCH_SIZE):
        batch = all_texts[i : i + BATCH_SIZE]
        embs = get_embeddings(batch)
        all_embeddings.extend(embs)
        print(f"  Embedded batch {i // BATCH_SIZE + 1}/{(len(all_texts) + BATCH_SIZE - 1) // BATCH_SIZE}")
        time.sleep(0.1)

    assert len(all_embeddings) == len(all_texts)

    # 4. Create Qdrant collection
    client = QdrantClient(url=QDRANT_URL)
    vector_size = len(all_embeddings[0])
    print(f"Vector size: {vector_size}")

    if client.collection_exists(COLLECTION_NAME):
        print(f"Deleting existing collection: {COLLECTION_NAME}")
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    print(f"Created collection: {COLLECTION_NAME}")

    # 5. Build points and upsert
    points = []
    for idx, (tid, fname) in enumerate(text_meta):
        point = PointStruct(
            id=idx,
            vector=all_embeddings[idx],
            payload={
                "template_id": tid,
                "field_name": fname,
                "field_text": all_texts[idx],
                "weight": WEIGHTS[fname],
            },
        )
        points.append(point)

    # Upsert in batches
    UPSERT_BATCH = 64
    for i in range(0, len(points), UPSERT_BATCH):
        batch = points[i : i + UPSERT_BATCH]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)
        print(f"  Upserted batch {i // UPSERT_BATCH + 1}/{(len(points) + UPSERT_BATCH - 1) // UPSERT_BATCH}")

    print(f"\nDone! Total points: {len(points)}")
    print(f"Templates: {len(templates)}, Fields per template: {len(FIELD_NAMES)}")

    # 6. Verify
    info = client.get_collection(COLLECTION_NAME)
    print(f"Collection points count: {info.points_count}")

    # 7. Test retrieval
    test_retrieval(client)


def test_retrieval(client):
    """Test weighted multi-field retrieval on the built database."""
    print("\n" + "=" * 50)
    print("Testing vector database retrieval...")
    print("=" * 50)

    # Test query: a sample user intent
    test_query = (
        "We need a cloud hub-and-spoke topology with production and non-production isolation. "
        "Central firewall for traffic inspection, VPN for remote access, "
        "and monitoring for all network activity."
    )

    # Embed each field of the query
    query_embeddings = get_embeddings([test_query] * len(FIELD_NAMES))

    # Search per field and aggregate scores by template_id
    template_scores = {}
    template_details = {}

    for i, fname in enumerate(FIELD_NAMES):
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
            score = hit.score * WEIGHTS[fname]
            template_scores[tid] = template_scores.get(tid, 0) + score
            if tid not in template_details:
                template_details[tid] = hit.payload["field_text"][:80] + "..."

    # Sort by weighted score
    sorted_templates = sorted(template_scores.items(), key=lambda x: x[1], reverse=True)

    print(f"\nQuery: {test_query}\n")
    print(f"{'Rank':<6}{'Template ID':<20}{'Weighted Score':<16}{'Preview'}")
    print("-" * 80)
    for rank, (tid, score) in enumerate(sorted_templates[:5], 1):
        preview = template_details.get(tid, "")
        print(f"{rank:<6}{tid:<20}{score:<16.4f}{preview}")

    print(f"\nTotal templates matched: {len(sorted_templates)}")
    print("Test PASSED!" if len(sorted_templates) > 0 else "Test FAILED!")


if __name__ == "__main__":
    main()
