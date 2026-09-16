# Table 2 provenance investigation

## 0.5909 versus printed 0.60

The archived uniform rankings contain 44 unique aligned query IDs and **26 Top-1 hits**: 26/44=.590909…; the original metric rounds this to .5909. Two-decimal display is **.59**, not .60. The Table 2 PDF prints .60. No retrieved formatting rule or historical complete uniform-ranking result explains that display.

The original `metric_stage2.py::compute_metrics` uses `round(correct / len(results), 4)`. The reference dataset contains 44 samples/22 unique templates, and each of the three archived ranking files contains 44 queries. No filtered/excluded-query list or alternate uniform denominator was found. There is no manuscript LaTeX/edit history establishing whether .60 was manually entered or arose via double rounding; neither explanation is asserted as fact.

The supplied EVALUATION_RECORD identifies the 44-reference-sample experiment and its outputs. Its paths are historical, not additional data. Archived original results remain unchanged. Proposed correction: .60→.59, subject to author confirmation of the final manuscript.

## Exact query differences

The phase-1 saved live results were compared directly to the archived query-level top-five rankings; no new embedding settings were tried. See [query difference table](../artifact/evidence/provenance/retrieval_query_diff.md) and `artifact/evidence/provenance/retrieval_query_diff.json` for all 44 queries per strategy, actual field texts, canonical query hashes, expected template, both ranked lists/scores, common-template score deltas, and per-query aggregate contributions.

| Strategy | Changed top-five ordering | Queries changing Top-k hit | Changed reciprocal rank |
|---|---:|---:|---:|
| Uniform | 8 | 4 | 4 |
| Weighted | 15 | 3 | 5 |

Thus “approximately two query differences” describes some **net metric changes**, not the number of changed rankings.

Uniform: Education-4_req2 falls 1→2; Healthcare-2_req2 rises 2→1, cancelling Top-1. Enterprise-5_req2 and Healthcare-2_req1 each fall 3→4, losing two Top-3 hits. MRR net change is -1/264≈-.003788.

Weighted: Enterprise-4_req2 and Healthcare-2_req2 rise 2→1 (two Top-1 gains); Healthcare-2_req1 falls 3→4 (one Top-3 loss); Enterprise-5_req1 and Enterprise-5_req2 each fall 2→3 (MRR only). MRR net change is 7/528≈.013258. Top-5 is unchanged in both strategies. All query IDs and correct-template labels align.

## Configuration provenance: facts versus uncertainty

| Candidate explanation | Available evidence / conclusion |
|---|---|
| Qdrant versus exact cosine | Phase-1 real runs produced identical rounded rankings and metrics; not the cause of this observed mismatch |
| BGE-M3 revision / tokenizer revision | Original code names BGE-M3 but no immutable revision/serving lock is preserved. Current cache hashes are in bge-model-fingerprints.json. Cannot establish identical historical weights/tokenizer |
| SentenceTransformers vs raw Transformers | Original calls a vLLM embeddings endpoint; current CPU path explicitly uses raw Transformers. Cached pooling config specifies CLS; source does not establish original vLLM launch/pooling override |
| Pooling / normalization / precision | Current CLS+L2 float32 is explicit. Original Qdrant cosine normalizes scoring; single-vector cosine uses norms explicitly. Original inference precision and pooling flags are not recorded; no causal attribution established |
| Maximum sequence length | Current runner rejects >8192 tokens, never truncates; original API request does not set max length. Historical serving limit unknown |
| Query prefix/instruction | `build_query_texts` and API requests add no retrieval instruction prefix; no evidence of a historical prefix change |
| Field preprocessing/order | Original `build_field_texts` serializes node/link-type sets without sorting. PYTHONHASHSEED for original indexing was not recorded. This is a concrete reproducibility hazard, not proof of the actual cause |
| Field weights | Metric uses .30 scenario/.25 security/.18 description/.17 topology_summary/.10 title; uniform divides by active field count. Index payload weights differ, but metric explicitly supplies query weights and ignores payload weight. Current wrapper preserves the metric weights |
| Query/reference code revision | AST comparisons show v1/v2 build_query_texts, call_embedding_api, build_field_texts, and get_embeddings are identical. Fingerprints in retrieval_code_provenance.json |
| Duplicate removal | Both deduplicate reference templates by ID, first occurrence; 22 unique templates; no differing dedup evidence |
| Template version | v1/v2 reference JSON are byte-identical; SHA-256 `d8d01d6510b473d61f4c9aaa2feff168757c2b54f096ad6cd0e625703728b6a6`. Old/new held-out GT does not enter this reference retrieval experiment |
| Query version | v2 reference logs missing; authentic v1 queries align by ID and reproduce v2 Stage 1 aggregates and single-vector metrics exactly. This is strong association evidence, not an original per-query run manifest proving identity |
| Serialized index/old embeddings | Phase-1 isolated-copy recovery exposed only 4/132 expected Qdrant points; no complete original strings/vectors recovered. It does not prove the historical running collection was incomplete. No recovery rerun or partial-index substitution in phase 2 |
| Model serving configuration | Original endpoint protocol and BGE-M3 name are preserved, exact launch/revision/dtype/hash seed are not. No alternative settings were tested to chase expected results |

Archived top-five results preserve field scores for their candidates. Current phase-1 outputs preserve total scores, not individual live field embeddings/scores. The comparison reports available total-score deltas and archived field scores; it does not invent missing live field scores or unseen candidate scores.

## Evaluator behavior and limits

`./claims/retrieval/run.sh` defaults to the paper's selected **single-vector** strategy, independently reproduced exactly: .7500/.9091/1.0000/.8409. Numerical implementation and tolerance are unchanged. `--all` runs all strategies and retains strict nonzero behavior for comparisons exceeding the declared tolerance; `--strict` is an explicit alias for strict retrieval auditing. Historical multi-field results remain released and verified; the README links this report prominently.

Small score-margin changes explain the observed rank crossings arithmetically. Their historical upstream cause remains unproven. The unordered set serialization and unpinned serving stack are evidence-backed risks, but the evidence does not isolate either as the cause. No encoder/settings sweep, expected-value edit, query edit, or tolerance tuning was performed.

C. unresolved historical provenance
