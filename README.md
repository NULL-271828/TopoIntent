# TopoIntent Artifact

## 1. Overview

TopoIntent compiles natural-language security requirements into structured
network topologies, retrieves reference designs, applies CIS-oriented
structural checking and additive repair, and validates the resulting policy.

This artifact accompanies the ACSAC 2026 paper, “TopoIntent: Compiling
Security Intent into Executable, Compliance-Checked Network Topologies”.

## 2. Evaluation scope

| Paper result | Evaluation mode | Command |
|---|---|---|
| Stage 1 intent analysis (Table 1) | Released-output verification | `./claims/stage1/run.sh` |
| Stage 2 template retrieval (Table 2) | Live independent BGE-M3 reproduction | `./claims/retrieval/run.sh` |
| CIS-guided repair (Table 3) | Released-output verification | `./claims/cis_repair/run.sh` |
| Executable validation (Table 4) | Released-output verification | `./claims/stage5_validation/run_precomputed.sh` |
| End-to-end ablation (Table 5) | Released-output verification | `./claims/end_to_end/run.sh` |

The original LLM stages used Qwen2.5-72B-Instruct on a multi-GPU server, and
privileged Mininet/iptables execution was performed separately. Those expensive
or privileged stages are not rerun by this evaluator workflow. Their original
outputs are included and the scripts recompute the reported downstream metrics
from them. Retrieval is independently rerun with BGE-M3.

## 3. Requirements

The submitted workflow was tested on Linux with Python 3.13.13 and a CPU-only
environment. Python 3.11–3.13 with `venv` and `pip` should work. Retrieval uses
approximately 2.2–2.3 GiB peak RAM and about 8 GiB free disk is useful for the
environment and model cache. A GPU and sudo are not required. Internet access
is required on the first retrieval run if BAAI/bge-m3 is not already cached.

## 4. Environment setup

From the artifact root:

```bash
./install.sh
```

This creates `.venv` and installs the CPU dependencies used by the submitted
workflow. The claim scripts invoke that environment directly; activation is
optional. `install.sh` does not install system packages or download model
weights. The first retrieval run downloads the approximately 2.3 GB
BAAI/bge-m3 checkpoint through the standard Hugging Face model interface. Set
`BGE_MODEL` to an existing BGE-M3 directory to use a local cache. If Hugging
Face is unavailable or unusually slow, install the optional ModelScope
downloader and select it explicitly:

```bash
.venv/bin/pip install -r requirements-modelscope.txt
BGE_SOURCE=modelscope ./claims/retrieval/run.sh
```

The default and explicit Hugging Face forms are `./claims/retrieval/run.sh`
and `BGE_SOURCE=huggingface ./claims/retrieval/run.sh`. Both sources resolve
the same `BAAI/bge-m3` checkpoint and then use the same transformers tokenizer,
model, CLS pooling, normalization, and float32 code. The approximately
one-minute runtime reported below is computation after the model is local;
download time depends on network speed.

## 5. Claim-by-claim evaluation

Run the claims from the artifact root. Released-output checks finish in seconds;
retrieval takes about one minute on the tested CPU machine.

### Claim 1 — Stage 1 intent analysis

**Paper mapping:** Table 1.  
**Evaluation mode:** Released-output verification.

```bash
./claims/stage1/run.sh
```

The command recomputes the metrics from the released Stage 1 outputs and the
reference ground truth.

| Metric | Expected |
|---|---:|
| Scenario Accuracy | 0.98 |
| Zone Type Recall | 0.68 |
| Node Type Recall | 0.53 |

### Claim 2 — Template retrieval

**Paper mapping:** Section 5.4, Table 2 (single-vector strategy).  
**Evaluation mode:** Live independent reproduction.

```bash
./claims/retrieval/run.sh
```

The command loads the actual BAAI/bge-m3 model, embeds the released reference
templates and Stage 1 queries, recomputes cosine rankings, and calculates
Top-1, Top-3, Top-5 and MRR. It runs on CPU and does not invoke the LLM.

The paper's five-field representation corresponds to multi-field retrieval over
`title`, `scenario`, `description`, `topology_summary`, and `security`. The
primary reproducible single-vector strategy instead uses
`scripts/retrieval_core.py::flatten_template_text()` as its exact input
representation. It serializes `template_id`, `title`, `scenario`,
`description`, and `topology_summary`; each zone's `zone_id`, `name`, and
`zone_type`; each node's `node_id`, `name`, `normalized_type`, and `zone_id`;
and each `node_links` and `zone_links` entry's `link_id`, `source`, `target`,
`link_type`, and `label` into one text string.

| Metric | Expected |
|---|---:|
| Top-1 | 0.7500 |
| Top-3 | 0.9091 |
| Top-5 | 1.0000 |
| MRR | 0.8409 |

The run took approximately one minute and used approximately 2.2–2.3 GiB peak
RAM on the tested machine.

### Claim 3 — CIS-guided repair

**Paper mapping:** Table 3.  
**Evaluation mode:** Released-output verification.

```bash
./claims/cis_repair/run.sh
```

The command recomputes CIS satisfaction and repair-round statistics from the
released Stage 4 outputs.

| IG level | Before repair | After repair | Mean repair rounds | Mean test cases |
|---|---:|---:|---:|---:|
| IG2 | 0.7183 (181/252) | 0.9960 | 1.4286 | 41.1429 |
| IG3 | 0.7792 | 0.9968 | 1.3571 | 41.1429 |

The accepted-paper value for IG2 before repair is 0.78; the released
14-sample evidence yields 0.7183. See [Section 7](#7-known-paperevidence-discrepancies).

### Claim 4 — Stage 5 executable validation

**Paper mapping:** Table 4.  
**Evaluation mode:** Released-output verification.

```bash
./claims/stage5_validation/run_precomputed.sh
```

The command validates the released Stage 5 report schemas and counters, then
recomputes node-link reachability, pre-ACL path executability, post-ACL policy
pass rate, latency, and diagnostic failures.

| Setting | IG | Reachability | Pre-ACL pass | Post-ACL pass | Mean latency (ms) | Diagnostic failures |
|---|---|---:|---:|---:|---:|---:|
| Initial | IG2 | 1.0000 | 0.6354 | 0.8733 | 0.0566 | 214 |
| Initial | IG3 | 1.0000 | 0.6719 | 0.8663 | 0.0567 | 189 |
| After Feedback | IG2 | 1.0000 | 0.7359 | 0.8637 | 0.0664 | 165 |
| After Feedback | IG3 | 1.0000 | 0.7684 | 0.8885 | 0.0682 | 147 |

### Claim 5 — End-to-end ablation

**Paper mapping:** Table 5.  
**Evaluation mode:** Released-output verification.

```bash
./claims/end_to_end/run.sh
```

The command recomputes the ablation table from the original mixed-source runs
and the documented old ground truth.

| Variant | Zone recall | Node recall | CIS satisfaction | Post-ACL pass |
|---|---:|---:|---:|---:|
| Direct | 0.6333 | 0.6105 | — | — |
| RAG w/o Fusion | 0.5381 | 0.6278 | — | — |
| RAG w/o Repair | 0.7452 | 0.7281 | 0.7000 | — |
| RAG w/o Feedback | 0.7429 | 0.8184 | 0.9982 | 0.7836 |
| Full TopoIntent | 0.7571 | 0.7713 | 1.0000 | 0.8761 |

## 6. Runtime summary

| Evaluation | Typical runtime | GPU | sudo |
|---|---:|---|---|
| Stage 1 verification | < 1 s | No | No |
| BGE-M3 single-vector retrieval | ~1 min measured | No | No |
| CIS repair verification | < 1 s | No | No |
| Stage 5 verification | < 1 s | No | No |
| End-to-end verification | < 1 s | No | No |

## 7. Known paper/evidence discrepancies

The selected single-vector retrieval strategy reproduces exactly. Some
historical multi-field ablation rankings differ when rerun with the available
BGE-M3 environment because the historical encoder/index provenance is not fully
recoverable. The archived uniform Top-1 count is 26/44 = 0.5909 (normally 0.59),
while the paper displays 0.60. Query-level details are in the [Table 2
provenance report](docs/TABLE2_PROVENANCE_REPORT.md).

The complete released 14-sample IG2 evidence gives 181/252 = 0.7183 before
repair, while the accepted paper displays 0.78. No complete retained evidence
supporting 0.78 was recovered. The calculation and historical search are in the
[Table 3 discrepancy report](docs/TABLE3_DISCREPANCY_REPORT.md).

The default verification commands return success when released evidence is
internally valid and print these as paper-value warnings.

## 8. Artifact contents and release status

| Directory | Contents |
|---|---|
| `artifact/src/` | TopoIntent implementation, datasets, schemas, original runs and metric code |
| `artifact/evidence/provenance/` | Table 2/Table 3 investigation outputs and retained provenance |
| `claims/` | Claim-specific entry points and mappings |
| `scripts/` | Verification, retrieval, packaging and diagnostic utilities |
| `docs/` | Table 2 and Table 3 provenance and discrepancy reports |
| `infrastructure/` | Hardware and deployment notes retained for completeness |
| `submission/` | Candidate paper PDF |

The package contains no model weights, virtual environment, cache, credentials,
or private source-tree dependency. Author-owned code is released under
[Apache-2.0](LICENSE). The authors have approved public distribution of the
artifact, including derived topology data and CIS information; third-party
terms remain listed in [THIRD_PARTY.md](THIRD_PARTY.md). The official
`metadata.toml` is included; the permanent public repository/archive URL must
be confirmed after upload.

Evaluation update (Oct. 12, 2026): Fixed shell executable permissions, removed
stale references to unavailable optional helper scripts, ensured `outputs/` is
created automatically, enabled Python UTF-8 mode, and documented the exact
single-vector retrieval input representation. No experimental data, reported
metrics, or evaluation logic were changed.
