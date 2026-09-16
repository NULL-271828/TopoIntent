# Third-party and copyright audit

| Component | Source/purpose | License/status | Included? / obtain |
|---|---|---|---|
| Author code, prompts, synthetic intents, outputs | Supplied project; system implementation and evidence | Apache-2.0 for author-owned code/documentation; author has approved public distribution of derived artifact material | Included for public AE release; third-party components remain separately governed |
| 29 derived topology templates (22 reference / 7 held out) | Project dataset pipeline extracts/reconciles reference architecture diagrams | Author has approved public distribution; source attribution/provenance remains documented where available | Included for public AE release |
| Raw architecture diagrams | `dataset/topology/topology_imgs` and dataset-construction inputs | Third-party provenance/license uncertain | Excluded; author must supply source/download mapping and terms |
| CIS v8.1.2 safeguard definitions/titles | [CIS Controls](https://www.cisecurity.org/controls/v8-1); scoped verification | Public information; retain attribution and do not imply CIS endorsement | `cis_controls.json` and derivative annotations included; workbooks excluded |
| BGE-M3 | [BAAI model card](https://huggingface.co/BAAI/bge-m3); real dense retrieval | Model card identifies MIT | Weights excluded; evaluator downloads official model or uses own cache |
| Qwen2.5-72B-Instruct-AWQ | [Qwen model card](https://huggingface.co/Qwen/Qwen2.5-72B-Instruct-AWQ); source default for LLM | Qwen model-specific terms; authors must confirm exact checkpoint/revision | Weights excluded; optional full reproduction only |
| Qdrant / qdrant-client | [Qdrant](https://github.com/qdrant/qdrant), [client](https://github.com/qdrant/qdrant-client); vector search | Upstream Apache-2.0 | Dependency installed; original server code path retained; no storage engine binary bundled |
| PyTorch / Transformers / NumPy | Official pip distributions; encoder/runtime | Upstream BSD-style / Apache-2.0 / BSD respectively; dependency distributions retain notices | Installed, not vendored |
| Mininet | [Mininet upstream](https://github.com/mininet/mininet); optional emulation | Upstream BSD-style | Not bundled/installed by default; official VM or source installation |
| ACSAC paper candidate | Supplied anonymous manuscript PDF | Final version and distribution rights need confirmation | Local `submission/paper-candidate.pdf`; not represented as confirmed accepted copy |
| artmeta | [Official metadata workflow](https://github.com/jelenamirkovic/artmeta) | Upstream terms apply | Tool not vendored; author runs official workflow once public URLs/decisions exist |

The dataset JSON has no per-diagram source URLs. No license was inferred for derived data, and derivation is not treated as automatically clearing third-party rights. Authors must either establish redistribution permission or provide an authorized acquisition path that still supports evaluation. These are release blockers, not permission requests to run the local deterministic checks.

## Distribution scope

The authors have approved public distribution of the artifact, including the
derived topology data and CIS information. Apache-2.0 applies only to
author-owned code and documentation; third-party terms remain controlling.
Model weights, caches, environments, raw architecture images and credentials
are excluded.
