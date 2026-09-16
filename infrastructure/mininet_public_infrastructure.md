# Optional live Mininet reproduction

**Not tested on the preparation server.** Use a disposable privileged Linux environment such as [CloudLab](https://www.cloudlab.us/). Ubuntu 22.04/24.04 with Python 3 and Mininet 2.3.0 is a proposed target, not an identified original environment. Original Mininet/OS/iptables versions are missing. Recommend 4 cores and 8 GB RAM; allow 5–15 minutes/topology or 5–14 hours for all 56 initial/feedback topologies (estimates). One case is the recommended scaled AE task.

Provision an account/host through the selected public infrastructure. No author-specific image, SSH key, private endpoint, or allocation exists in this package. Validate infrastructure with its normal access procedure.

## Install only on that privileged host

Follow the [official Mininet VM/source instructions](https://mininet.org/download/). A prebuilt Mininet VM is an alternative. For a disposable Ubuntu host, install Python 3, Mininet, iproute2, iputils-ping, and iptables using the host administrator's package manager. Verify `mn --version`, `python3 -c 'import mininet'`, `iptables --version`, and `ping -V`; record the OS/kernel. No Python GPU packages are needed. The generated code uses Mininet plus Python standard libraries, ping/ip routing, and iptables.

The upstream source route is `git clone https://github.com/mininet/mininet`, select tag `2.3.0`, then follow its privileged installer with Python 3. These installation commands are deliberately separate from `install.sh`.

## Input and run

The default case uses initial `Financial-4_req1` IG2 XML, including topology, connectivity tests, and expected allow/deny policy. From artifact root:

```bash
./claims/stage5_validation/run_mininet_optional.sh
# Only on the privileged research host:
sudo ./claims/stage5_validation/run_mininet_optional.sh --execute
```

`--execute` is explicit and checks Linux/root before generation/execution. Without it, the command only generates a standalone script. It does not install or run Mininet automatically. Use system Python containing Mininet, not the CPU AE venv.

For another input, pass `--xml` and `--output-dir`. Initial XMLs are under `artifact/src/evaluation/evaluation_runs/test_all_single_vector_structfix_20260526_091223/samples/*/stage4/xml/*/`; post-feedback XMLs are discoverable under the analogous `test_all_single_vector_structfix_feedback/samples/` tree. Stage 4 JSON in each sample also retains test cases, repair history, and final topology. Original generated scripts are retained alongside the samples.

Generated scripts instantiate LinuxRouter hosts, assign synthetic loopback/link addresses, install routes, test node links, test pre-ACL reachability, apply kernel iptables rules, and test expected policy. They stop the emulation in a `finally` block. Use a disposable instance if interrupted cleanup is needed. These scripts configure networking and must not run on the no-sudo preparation host.

## Output and comparison

Default report: `outputs/mininet-optional/policy_test_report_generated_Financial-4_req1_IG2.json`. It includes `node_link_tests/summary`, `pre_acl_policy_tests/summary`, `post_acl_policy_tests/summary`, ACL table/conflicts, diagnosis, and topology sizes. Validate a new report with the AE report schema and `report_validate`:

```bash
.venv/bin/python -c 'import sys; from pathlib import Path; sys.path.insert(0,"scripts"); from verify_precomputed import report_validate; report_validate(Path("outputs/mininet-optional/policy_test_report_generated_Financial-4_req1_IG2.json")); print("PASS: report schema/counters")'
```

Original initial IG2 case: post-ACL 29/40; post-feedback IG2 40/40. IG3 goes 38/40→39/39. These are reference observations, not guarantees of timing/network scheduling parity. Compare per-test verdicts and diagnostics, record environment differences, and keep live reports separate from original evidence. Do not overwrite `artifact/` reports with a new run.

For a full fresh Table 4 experiment, run all 28 initial cases and all 28 released post-feedback cases into separate output directories, then invoke the retained `metric_stage5.py --initial-reports-dir ... --feedback-reports-dir ... --output-prefix live`. Running released post-feedback inputs exercises their behavior but does not independently rerun the LLM feedback repair; doing that requires the full GPU path.

Historical generated Python copies were pruned after byte-identical regeneration checks. The optional wrapper generates them from retained initial/post-feedback XML on demand. Additional ablation exports can be regenerated with `.venv/bin/python scripts/regenerate_exports.py`; output goes under `outputs/regenerated/`. Generated reports/manifests contain the actual new output paths.
