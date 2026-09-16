# Table 3: IG2 before-repair provenance investigation

## Finding

The selected original run yields **181/252 = 0.7182539682539683**, displayed as **0.7183** by the metric script and **0.72** at paper precision. No complete 14-sample IG2 experimental result supporting the paper's **0.78** was found in the available evidence. Genuine smaller historical runs do round to 0.78; they do not establish the paper's n=14 claim.

## Exact calculation and provenance

Source-relative run: `evaluation/evaluation_runs/test_all_single_vector_structfix_20260526_091223`. The artifact retains this under `artifact/src/`. For each `samples/<sample>/stage4/IG2_output.json`, `evaluation/metric_stage4.py::compute_one`:

1. Takes denominator `len(compliance_check.controls)` from final verification: 18 for every sample.
2. Excludes repair records marked `stage4_structural_normalization`.
3. Counts `findings_before` in the first remaining repair record as initially non-satisfied. If no CIS repair history exists, uses final satisfied count instead.
4. Computes `max(total_controls - findings_count, 0) / total_controls`.
5. `summarize` averages the 14 sample ratios (macro average), rounding to four decimals.

Thus this is the original script's **reconstruction from repair history**, not an independent first-pass verifier rerun. Equal denominators make the micro average identical. The source summary, detail CSV, and generated TeX agree: the TeX prints **IG2 71.8%**, IG3 77.9%. Nothing in that formatting converts 71.8% to 78%.

All 14 file paths/hashes and control/finding IDs are in `artifact/evidence/provenance/table3_aggregation_comparison.json`; a reviewable breakdown is `artifact/evidence/provenance/table3_ig2_before_per_sample.csv`. Contributions are ratio/14, not rounded before summing.

| Sample | Required | Satisfied before | Ratio | Contribution |
|---|---:|---:|---:|---:|
| Financial-2_req1 | 18 | 14 | .777778 | .055556 |
| Financial-2_req2 | 18 | 15 | .833333 | .059524 |
| Financial-3_req1 | 18 | 13 | .722222 | .051587 |
| Financial-3_req2 | 18 | 12 | .666667 | .047619 |
| Financial-4_req1 | 18 | 11 | .611111 | .043651 |
| Financial-4_req2 | 18 | 13 | .722222 | .051587 |
| Government-1_req1 | 18 | 15 | .833333 | .059524 |
| Government-1_req2 | 18 | 12 | .666667 | .047619 |
| Government-2_req1 | 18 | 12 | .666667 | .047619 |
| Government-2_req2 | 18 | 12 | .666667 | .047619 |
| Government-3_req1 | 18 | 14 | .777778 | .055556 |
| Government-3_req2 | 18 | 13 | .722222 | .051587 |
| Government-4_req1 | 18 | 10 | .555556 | .039683 |
| Government-4_req2 | 18 | 15 | .833333 | .059524 |

Macro = 0.7182539682539683. Micro = 181/252 = 0.7182539682539683.

## Historical evidence that rounds to 0.78

The historical scan grouped saved Stage 4 outputs by run, branch, and IG, preserving sample counts. Results and per-file SHA-256 values are in `table3_aggregation_comparison.json`. It found 28 run/branch/IG groups.

| Historical run | IG2 observations | Value | Why not support for Table 3 |
|---|---:|---:|---|
| `fixcheck_financial2_req2_20260525_154358` | 1 | 14/18=.777778 | Single-sample fix check; not 14-sample evaluation |
| `test_all_evaluation_20260525_150120` | 6 | 84/108=.777778 | Partial run; manifest intends 14 but remains `running`; only six saved IG2 outputs |
| `test_all_evaluation_20260525_155851` | 14 | .686508 | Complete earlier run, different predictions |
| `test_all_single_vector_evaluation_20260525_195001` | 14 | .678571 | Complete earlier single-vector run |
| `test_all_single_vector_evaluation_20260525_195550` | 14 | .710317 | Complete earlier single-vector run |
| `test_all_single_vector_structfix_20260526_091223` | 14 | .718254 | Selected paper run, other Table 3 cells and Table 4 correspond |
| `test_all_single_vector_structfix_feedback` | 14 | .718254 | Preserves initial repair history |

The fix-check manifest records creation `2026-05-25T15:43:58`, one sample, IG2, and completion status `waiting_for_wsl_reports`. Its SHA-256 is `b4d1f00ce3610453ad301e658f61c7a910f776c439a0257c99b43edf6b38b47a`.

The partial-run manifest records `2026-05-25T15:01:20`, intended sample_count 14, IG2/IG3, status `running`. SHA-256: `1ce8508d8f50afe5284bd867a5939f4edb9ef01563d848fe4ca395b58886306f`.

Both identify `dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_test.json` in historical provenance. They do not pin its then-current contents, serving configuration, or a GT hash. Current old/new GT filenames cannot retrospectively establish that version. The selected run manifest SHA-256 is `921b994767a3468a22007c89d67f949f42cd793c462df775994e0d60fb97a12b`; run date is May 26. Original and adapted-copy hashes are separately recorded in the retained `docs/artifact_integrity.json` manifest.

The **IG3** selected-run aggregate is .779220779… (.7792), which legitimately rounds to .78. A repeated IG3 value in the IG2 cell is plausible as an editorial error, but there is **no editing history proving that explanation**.

## Safeguards, GT, statuses, and denominator checks

- All current IG2 outputs contain the same 18 distinct safeguard IDs. The `CIS_IG_RULES` assignments in v1 and v2 are identical. IG2 includes IG1's four safeguards: 1.1, 4.4, 6.4, 9.2. No evidence of a changed mapping explains this value.
- Before-repair findings contain **49 manual_review and 22 unsatisfied** decisions, totaling 71; neither is treated as satisfied. The default repair path includes manual_review; source `controls_needing_repair` documents this. No alternative matched/rejected count is used by metric_stage4.
- Diagnostic calculations, not replacement metrics: excluding manual_review from the denominator gives 181/203=.891626; counting it satisfied gives 230/252=.912698; excluding IG1 safeguards gives 148/196=.755102. None explains .78. These alternatives were not applied to evaluation.
- Excluding any one current sample can raise the result only to .730769. No samples were excluded or alternate subsets selected to force agreement.
- Stage 4 aggregation does not load GT. Switching current old/new GT files cannot change this calculation over fixed outputs. GT differences matter to structural recall in Table 5, not this before-repair metric.
- An alternate historical safeguard version or full first-pass decision snapshot is not established by the available launch records. No evidence justifies changing denominator or status rules.

## Search scope and conclusion

Read-only search covered v2 metric JSON/CSV/TeX, all saved Stage 4 run outputs, EVALUATION_RECORD, supplementary results, logs, manuscript text, and v1/v2 safeguard definitions. No notebooks or manuscript LaTeX source were found in the searched v1/v2 hierarchy. Numeric search results for semantically named before-satisfaction keys are retained in `artifact/evidence/provenance/table3_historical_metric_search.json`; unrelated .78 retrieval scores/structural-recall values were not treated as CIS evidence.

A text inspection of the source reply DOCX found .78 only for structural-recall claims, not IG2 before-repair CIS. Correspondence was not copied. No BGE serving flags/revisions were found in source logs, shell scripts, or named config/manifest records.

No genuine **complete n=14 IG2** evidence supporting 0.78 was found. The smaller historical matches do not establish that the paper value came from them. Suggested author-approved corrections are in PAPER_CORRECTION_CANDIDATES.md. The evaluator runner validates .7183 against original evidence and emits a visible paper discrepancy warning; strict paper auditing remains nonzero.

C. PAPER VALUE APPEARS INCONSISTENT WITH AVAILABLE ORIGINAL EVIDENCE
