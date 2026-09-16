#!/usr/bin/env python3
"""
Stage 5.1: Generate Mininet validation scripts from Stage 4 XML.
"""

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path


IFNAMSIZ = 15
ETH_SUFFIX_MAX = len("-eth10")
NAME_MAX = IFNAMSIZ - ETH_SUFFIX_MAX


def _short_hash(text, n=4):
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:n]


def _safe_name(text):
    text = re.sub(r"[^a-zA-Z0-9]+", "", str(text or "").lower())
    return text or "x"


def _make_short_unique(text, used):
    base = _safe_name(text)
    if len(base) > NAME_MAX:
        base = base[: max(1, NAME_MAX - 5)] + _short_hash(text, 4)
    if len(base) < 2:
        base = f"{base}{_short_hash(text, 3)}"
    name = base[:NAME_MAX]
    i = 1
    while name in used:
        suffix = str(i)
        name = (base[: max(1, NAME_MAX - len(suffix))] + suffix)[:NAME_MAX]
        i += 1
    used.add(name)
    return name


def parse_stage4_xml(xml_path):
    """Parse Stage 4 XML and normalize it into generator-friendly data."""
    root = ET.parse(xml_path).getroot()
    used = set()

    domain_map = {}
    domains = []
    for domain in root.findall("./Domains/Domain"):
        domain_id = domain.get("id") or domain.get("name") or "domain"
        short = _make_short_unique(f"d_{domain_id}", used)
        domain_map[domain_id] = short
        domains.append({
            "id": short,
            "orig_id": domain_id,
            "name": domain.get("name") or domain_id,
            "type": domain.get("type") or "",
        })

    node_map = {}
    nodes = []
    for node in root.findall("./Nodes/Node"):
        node_id = node.get("id") or node.get("name") or "node"
        domain_id = node.get("domain") or ""
        if domain_id not in domain_map:
            short_domain = _make_short_unique(f"d_{domain_id or 'domain'}", used)
            domain_map[domain_id] = short_domain
            domains.append({"id": short_domain, "orig_id": domain_id, "name": domain_id, "type": ""})
        short = _make_short_unique(f"n_{domain_id}_{node_id}", used)
        node_map[node_id] = short
        nodes.append({
            "id": short,
            "orig_id": node_id,
            "name": node.get("name") or node_id,
            "domain": domain_map[domain_id],
            "type": node.get("type") or "other",
            "ip": node.get("ip") or "",
        })

    zone_links = []
    for link in root.findall("./Links/ZoneLinks/Link"):
        src = domain_map.get(link.get("source") or link.get("a") or "")
        dst = domain_map.get(link.get("target") or link.get("b") or "")
        if src and dst and src != dst:
            zone_links.append((src, dst))

    node_links = []
    for link in root.findall("./Links/NodeLinks/Link"):
        src = node_map.get(link.get("source") or link.get("a") or "")
        dst = node_map.get(link.get("target") or link.get("b") or "")
        if src and dst and src != dst:
            node_links.append({
                "id": link.get("id") or f"nl{len(node_links) + 1}",
                "source": src,
                "target": dst,
                "type": link.get("type") or "",
                "label": link.get("label") or "",
            })

    policy_tests = []
    for policy in root.findall("./Policies/*"):
        expect = "allow" if policy.tag.lower() == "allow" else "deny"
        src = node_map.get(policy.get("src_node") or "") or domain_map.get(policy.get("src_zone") or "")
        dst = node_map.get(policy.get("dst_node") or "") or domain_map.get(policy.get("dst_zone") or "")
        if src and dst and src != dst:
            policy_tests.append({
                "id": policy.get("id") or f"tc{len(policy_tests) + 1}",
                "src": src,
                "dst": dst,
                "expect": expect,
                "protocol": policy.get("protocol") or "",
                "port": policy.get("port") or "",
            })

    return {
        "template_id": root.get("template_id") or Path(xml_path).stem,
        "ig_level": root.get("ig_level") or "",
        "domains": domains,
        "nodes": nodes,
        "zone_links": dedupe_pairs(zone_links),
        "node_links": dedupe_links(node_links),
        "policy_tests": policy_tests,
        "name_maps": {"domains": domain_map, "nodes": node_map},
    }


def dedupe_pairs(pairs):
    seen = set()
    out = []
    for a, b in pairs:
        key = tuple(sorted((a, b)))
        if key not in seen:
            seen.add(key)
            out.append((a, b))
    return out


def dedupe_links(links):
    seen = set()
    out = []
    for link in links:
        a = link["source"]
        b = link["target"]
        key = tuple(sorted((a, b)))
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


def assign_ips(parsed):
    """Assign loopback and point-to-point addresses for node-link emulation."""
    for idx, node in enumerate(parsed["nodes"], 1):
        node["loopback"] = node.get("ip") or f"10.255.0.{idx}"

    for idx, link in enumerate(parsed["node_links"], 1):
        second = 100 + ((idx - 1) // 255)
        third = ((idx - 1) % 255) + 1
        link["source_ip"] = f"10.{second}.{third}.1/30"
        link["target_ip"] = f"10.{second}.{third}.2/30"
        link["source_peer"] = f"10.{second}.{third}.2"
        link["target_peer"] = f"10.{second}.{third}.1"

    return parsed


def ensure_domain_probe_nodes(parsed):
    """Add one synthetic probe host for domains without any node."""
    existing_domains = {node["domain"] for node in parsed["nodes"]}
    used = {node["id"] for node in parsed["nodes"]}
    for domain in parsed["domains"]:
        domain_id = domain["id"]
        if domain_id in existing_domains:
            continue
        probe_id = _make_short_unique(f"probe_{domain_id}", used)
        parsed["nodes"].append({
            "id": probe_id,
            "orig_id": f"probe_{domain_id}",
            "name": f"Probe {domain.get('name') or domain_id}",
            "domain": domain_id,
            "type": "endpoint",
            "ip": "",
            "synthetic": True,
        })


def generate_mininet_script_from_xml_file(xml_path, output_dir):
    """Generate a standalone Mininet validation script from one Stage 4 XML file."""
    parsed = assign_ips(parse_stage4_xml(xml_path))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    script_name = f"validation_{parsed['template_id']}_{parsed['ig_level']}.py"
    script_path = output_dir / script_name
    report_name = f"policy_test_report_{parsed['template_id']}_{parsed['ig_level']}.json"

    script = render_mininet_script(parsed, report_name)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(script_path, 0o755)

    manifest = {
        "xml_file": str(xml_path),
        "script_file": str(script_path),
        "expected_report_file": str(output_dir / report_name),
        "template_id": parsed["template_id"],
        "ig_level": parsed["ig_level"],
        "domains": len(parsed["domains"]),
        "nodes": len(parsed["nodes"]),
        "node_links": len(parsed["node_links"]),
        "policy_tests": len(parsed["policy_tests"]),
    }
    manifest_path = output_dir / f"manifest_{parsed['template_id']}_{parsed['ig_level']}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest


def render_mininet_script(parsed, report_name):
    domains_json = json.dumps(parsed["domains"], ensure_ascii=False, indent=2)
    nodes_json = json.dumps(parsed["nodes"], ensure_ascii=False, indent=2)
    zone_links_json = json.dumps(parsed["zone_links"], ensure_ascii=False)
    node_links_json = json.dumps(parsed["node_links"], ensure_ascii=False, indent=2)
    policy_tests_json = json.dumps(parsed["policy_tests"], ensure_ascii=False, indent=2)
    domains_literal = repr(domains_json)
    nodes_literal = repr(nodes_json)
    zone_links_literal = repr(zone_links_json)
    node_links_literal = repr(node_links_json)
    policy_tests_literal = repr(policy_tests_json)

    return f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generated Mininet validation script.

Run in WSL or a Linux host with Mininet installed:
    sudo python3 {report_name.replace("policy_test_report_", "validation_").replace(".json", ".py")}
"""

import json
import os
from collections import deque
import time
from mininet.net import Mininet
from mininet.node import Node
from mininet.cli import CLI
from mininet.log import setLogLevel, info

DOMAINS = json.loads({domains_literal})
NODES = json.loads({nodes_literal})
ZONE_LINKS = json.loads({zone_links_literal})
NODE_LINKS = json.loads({node_links_literal})
POLICY_TESTS = json.loads({policy_tests_literal})

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "{report_name}")
NODES_BY_ID = {{node["id"]: node for node in NODES}}
DOMAIN_NODE_IDS = {{}}
for node in NODES:
    DOMAIN_NODE_IDS.setdefault(node["domain"], []).append(node["id"])


class LinuxRouter(Node):
    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1")

    def terminate(self):
        self.cmd("sysctl -w net.ipv4.ip_forward=0")
        super().terminate()


def parse_ping(output):
    loss = None
    avg_ms = None
    for line in output.splitlines():
        if "packet loss" in line:
            try:
                loss = float(line.split("%")[0].split()[-1])
            except Exception:
                pass
        if "rtt min/avg/max" in line or "round-trip min/avg/max" in line:
            try:
                avg_ms = float(line.split("=")[1].strip().split()[0].split("/")[1])
            except Exception:
                pass
    return loss, avg_ms


def run_ping(src, dst_ip, count=3):
    output = src.cmd("ping -c %s -W 1 %s" % (count, dst_ip))
    loss, avg_ms = parse_ping(output)
    reachable = loss == 0.0
    return reachable, avg_ms


def build_graph():
    graph = {{node["id"]: set() for node in NODES}}
    peer_ip = {{}}
    for link in NODE_LINKS:
        a = link["source"]
        b = link["target"]
        if a not in graph or b not in graph:
            continue
        graph[a].add(b)
        graph[b].add(a)
        peer_ip[(a, b)] = link["target_ip"].split("/")[0]
        peer_ip[(b, a)] = link["source_ip"].split("/")[0]
    return graph, peer_ip


def shortest_next_hop(graph, src, dst):
    if src == dst:
        return None
    queue = deque([(src, [])])
    seen = {{src}}
    while queue:
        cur, path = queue.popleft()
        for nxt in sorted(graph.get(cur, [])):
            if nxt in seen:
                continue
            next_path = path + [nxt]
            if nxt == dst:
                return next_path[0]
            seen.add(nxt)
            queue.append((nxt, next_path))
    return None


def install_routes(hosts):
    graph, peer_ip = build_graph()
    for src_id, src in hosts.items():
        src.cmd("ip route del default 2>/dev/null || true")
        for dst_id, dst_meta in NODES_BY_ID.items():
            if src_id == dst_id:
                continue
            next_hop = shortest_next_hop(graph, src_id, dst_id)
            if not next_hop:
                continue
            via = peer_ip.get((src_id, next_hop))
            if via:
                src.cmd("ip route replace %s/32 via %s" % (dst_meta["loopback"], via))


def path_exists(src_id, dst_id):
    graph, _ = build_graph()
    if src_id not in graph or dst_id not in graph:
        return False
    if src_id == dst_id:
        return True
    queue = deque([src_id])
    seen = {{src_id}}
    while queue:
        cur = queue.popleft()
        for nxt in graph.get(cur, []):
            if nxt == dst_id:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def resolve_endpoint_id(endpoint):
    if endpoint in NODES_BY_ID:
        return endpoint
    candidates = DOMAIN_NODE_IDS.get(endpoint, [])
    return candidates[0] if candidates else None


def normalize_proto_port(case):
    proto = (case.get("protocol") or "").lower().strip()
    port = str(case.get("port") or "").strip()
    return proto, port


def acl_match_key(case, src_id, dst_id):
    proto, port = normalize_proto_port(case)
    return (src_id, dst_id, proto, port)


def build_acl_table(hosts):
    """Compile policy tests into explicit source-side/destination-side ACL rules.

    Deny has precedence for duplicate/conflicting match keys. For each tested
    flow, we also install an exact fallback DROP after the explicit rule. An
    allow test therefore succeeds because an ACCEPT rule exists, not because the
    namespace default policy is permissive.
    """
    table = []
    by_key = {{}}
    conflicts = []
    for case in POLICY_TESTS:
        src_id = resolve_endpoint_id(case["src"])
        dst_id = resolve_endpoint_id(case["dst"])
        src = hosts.get(src_id) if src_id else None
        dst = hosts.get(dst_id) if dst_id else None
        proto, port = normalize_proto_port(case)
        if not src or not dst:
            table.append({{
                "rule_id": "acl_%s" % case["id"],
                "case_id": case["id"],
                "action": "skip",
                "src": case["src"],
                "dst": case["dst"],
                "resolved_src": src_id,
                "resolved_dst": dst_id,
                "protocol": proto,
                "port": port,
                "reason": "endpoint not resolvable from node/domain",
            }})
            continue
        key = acl_match_key(case, src_id, dst_id)
        existing = by_key.get(key)
        action = "accept" if case["expect"] == "allow" else "drop"
        if existing:
            if existing["action"] != action:
                conflicts.append({{
                    "match": {{"src": src_id, "dst": dst_id, "protocol": proto, "port": port}},
                    "previous_action": existing["action"],
                    "new_action": action,
                    "new_case_id": case["id"],
                    "resolution": "drop_precedence",
                }})
                existing["action"] = "drop"
                existing.setdefault("case_ids", []).append(case["id"])
            else:
                existing.setdefault("case_ids", []).append(case["id"])
            continue
        rule = {{
            "rule_id": "acl_%s" % (len(by_key) + 1),
            "case_ids": [case["id"]],
            "action": action,
            "src": case["src"],
            "dst": case["dst"],
            "resolved_src": src_id,
            "resolved_dst": dst_id,
            "src_ip": NODES_BY_ID[src_id]["loopback"],
            "dst_ip": NODES_BY_ID[dst_id]["loopback"],
            "protocol": proto,
            "port": port,
        }}
        by_key[key] = rule
        table.append(rule)
    return table, conflicts


def iptables_match(rule, direction):
    proto = rule.get("protocol") or ""
    port = str(rule.get("port") or "").strip()
    src_ip = rule["src_ip"]
    dst_ip = rule["dst_ip"]
    parts = []
    if proto == "tcp" and port:
        parts.extend(["-p", "tcp"])
    if direction == "output":
        parts.extend(["-d", dst_ip])
        if proto == "tcp" and port:
            parts.extend(["--dport", port])
    else:
        parts.extend(["-s", src_ip])
        if proto == "tcp" and port:
            parts.extend(["--dport", port])
    return " ".join(parts)


def reset_acl_chains(hosts):
    for host in hosts.values():
        host.cmd("iptables -D OUTPUT -j TOPSEC_OUT 2>/dev/null || true")
        host.cmd("iptables -D INPUT -j TOPSEC_IN 2>/dev/null || true")
        host.cmd("iptables -F TOPSEC_OUT 2>/dev/null || true")
        host.cmd("iptables -F TOPSEC_IN 2>/dev/null || true")
        host.cmd("iptables -X TOPSEC_OUT 2>/dev/null || true")
        host.cmd("iptables -X TOPSEC_IN 2>/dev/null || true")
        host.cmd("iptables -N TOPSEC_OUT")
        host.cmd("iptables -N TOPSEC_IN")
        host.cmd("iptables -I OUTPUT 1 -j TOPSEC_OUT")
        host.cmd("iptables -I INPUT 1 -j TOPSEC_IN")


def install_rule_pair(hosts, rule, action):
    src = hosts.get(rule["resolved_src"])
    dst = hosts.get(rule["resolved_dst"])
    if not src or not dst:
        return
    target = "ACCEPT" if action == "accept" else "DROP"
    out_match = iptables_match(rule, "output")
    in_match = iptables_match(rule, "input")
    src.cmd("iptables -A TOPSEC_OUT %s -m comment --comment %s -j %s" % (out_match, rule["rule_id"], target))
    dst.cmd("iptables -A TOPSEC_IN %s -m comment --comment %s -j %s" % (in_match, rule["rule_id"], target))


def apply_acl_table(hosts, acl_table):
    reset_acl_chains(hosts)
    installed = []
    for rule in acl_table:
        if rule.get("action") not in ("accept", "drop"):
            continue
        install_rule_pair(hosts, rule, rule["action"])
        installed.append({{**rule, "installed": True, "fallback_drop_installed": True}})
    for rule in acl_table:
        if rule.get("action") not in ("accept", "drop"):
            continue
        install_rule_pair(hosts, rule, "drop")
    return installed


def find_matching_acl(case, src_id, dst_id, acl_table):
    proto, port = normalize_proto_port(case)
    for rule in acl_table:
        if rule.get("resolved_src") == src_id and rule.get("resolved_dst") == dst_id and rule.get("protocol") == proto and str(rule.get("port") or "") == port:
            return rule
    return None


def run_policy_tests(hosts, acl_table=None, phase="post_acl"):
    results = []
    summary = {{"total": len(POLICY_TESTS), "executed": 0, "skipped": 0, "passed": 0, "failed": 0}}
    for case in POLICY_TESTS:
        src_id = resolve_endpoint_id(case["src"])
        dst_id = resolve_endpoint_id(case["dst"])
        src = hosts.get(src_id) if src_id else None
        dst = hosts.get(dst_id) if dst_id else None
        if not src or not dst:
            summary["skipped"] += 1
            results.append({{**case, "phase": phase, "actual": "skip", "passed": False, "reason": "endpoint not resolvable from node/domain"}})
            continue
        summary["executed"] += 1
        dst_ip = NODES_BY_ID[dst_id]["loopback"]
        proto, port = normalize_proto_port(case)
        if proto == "tcp" and port:
            actual, latency_ms = run_tcp_test(src, dst, dst_ip, port)
        else:
            reachable, latency_ms = run_ping(src, dst_ip)
            actual = "allow" if reachable else "deny"
        expected = "allow" if phase == "pre_acl" else case["expect"]
        passed = actual == expected
        summary["passed" if passed else "failed"] += 1
        matched_acl = find_matching_acl(case, src_id, dst_id, acl_table or [])
        results.append({{
            "id": case["id"],
            "phase": phase,
            "src": case["src"],
            "dst": case["dst"],
            "resolved_src": src_id,
            "resolved_dst": dst_id,
            "expected": expected,
            "declared_policy": case["expect"],
            "actual": actual,
            "passed": passed,
            "latency_ms": latency_ms,
            "has_node_path": path_exists(src_id, dst_id),
            "protocol": proto,
            "port": port,
            "matched_acl_rule": matched_acl.get("rule_id") if matched_acl else None,
            "matched_acl_action": matched_acl.get("action") if matched_acl else None,
        }})
        info("[%s] %s %s -> %s expect=%s actual=%s\\n" % ("PASS" if passed else "FAIL", phase, case["src"], case["dst"], expected, actual))
    return {{"summary": summary, "results": results}}


def build_diagnosis(pre_report, post_report, node_link_tests, acl_conflicts):
    topology_failures = []
    acl_failures = []
    skipped = []
    pre_by_id = {{item["id"]: item for item in pre_report["results"]}}
    for item in post_report["results"]:
        pre = pre_by_id.get(item["id"], {{}})
        if item["actual"] == "skip":
            skipped.append(item)
            continue
        if pre.get("actual") != "allow":
            topology_failures.append({{
                "test_id": item["id"],
                "src": item["src"],
                "dst": item["dst"],
                "pre_acl_actual": pre.get("actual"),
                "post_acl_actual": item["actual"],
                "has_node_path": item.get("has_node_path"),
                "reason": "flow is not reachable before ACL enforcement",
            }})
        elif not item["passed"]:
            acl_failures.append({{
                "test_id": item["id"],
                "src": item["src"],
                "dst": item["dst"],
                "expected": item["expected"],
                "actual": item["actual"],
                "matched_acl_rule": item.get("matched_acl_rule"),
                "matched_acl_action": item.get("matched_acl_action"),
                "reason": "pre-ACL connectivity exists but post-ACL result violates policy expectation",
            }})
    link_failures = [item for item in node_link_tests if not item.get("reachable")]
    return {{
        "repair_required": bool(topology_failures or acl_failures or skipped or link_failures or acl_conflicts),
        "topology_failures": topology_failures,
        "acl_failures": acl_failures,
        "skipped_tests": skipped,
        "node_link_failures": link_failures,
        "acl_conflicts": acl_conflicts,
        "recommended_feedback_target": "stage4" if (topology_failures or acl_failures or acl_conflicts) else None,
    }}


def run_tcp_test(src, dst, dst_ip, port):
    """Verify TCP reachability with a short-lived Python socket server."""
    try:
        port_num = int(port)
    except Exception:
        return "deny", None
    server_log = "/tmp/tcp_probe_%s_%s.log" % (dst.name, port_num)
    server_cmd = (
        "python3 -c \\"import socket,sys;"
        "p=int(sys.argv[1]);"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        "s.bind(('0.0.0.0',p));"
        "s.listen(1);"
        "c,a=s.accept();"
        "c.recv(1);"
        "c.close();"
        "s.close()\\" "
        "%s > %s 2>&1 &" % (port_num, server_log)
    )
    dst.cmd("pkill -f 'tcp_probe|socket.*%s' || true" % port_num)
    dst.cmd(server_cmd)
    src.cmd("sleep 0.5")
    client_cmd = (
        "timeout 4 python3 -c \\"import socket,sys;"
        "s=socket.create_connection((sys.argv[1], int(sys.argv[2])), 2);"
        "s.send(b'x');"
        "s.close()\\" "
        "%s %s 2>&1" % (dst_ip, port_num)
    )
    start = time.time()
    output = src.cmd(client_cmd)
    latency_ms = round((time.time() - start) * 1000, 3)
    dst.cmd("pkill -f 'tcp_probe|socket.*%s' || true" % port_num)
    failed_markers = ("traceback", "refused", "timed out", "unreachable", "no route", "not found")
    ok = not any(marker in output.lower() for marker in failed_markers)
    return ("allow" if ok else "deny"), latency_ms if ok else None


def run_node_link_tests(hosts, runtime_links):
    results = []
    for link in runtime_links:
        src = hosts.get(link["source"])
        dst = hosts.get(link["target"])
        if not src or not dst:
            results.append({{
                "id": link["id"],
                "source": link["source"],
                "target": link["target"],
                "type": link.get("type", ""),
                "label": link.get("label", ""),
                "reachable": False,
                "latency_ms": None,
            }})
            continue
        target_ip = link["target_ip"].split("/")[0]
        reachable, latency_ms = run_ping(src, target_ip)
        results.append({{
            "id": link["id"],
            "source": link["source"],
            "target": link["target"],
            "type": link.get("type", ""),
            "label": link.get("label", ""),
            "reachable": reachable,
            "latency_ms": latency_ms,
        }})
    return results


def summarize_link_tests(link_tests):
    total = len(link_tests)
    reachable = sum(1 for item in link_tests if item["reachable"])
    latencies = [item["latency_ms"] for item in link_tests if item["latency_ms"] is not None]
    avg_latency = round(sum(latencies) / len(latencies), 3) if latencies else None
    return {{
        "total": total,
        "reachable": reachable,
        "unreachable": total - reachable,
        "avg_latency_ms": avg_latency,
    }}


def run():
    net = Mininet(controller=None, build=False)
    try:
        hosts = {{}}
        for node in NODES:
            hosts[node["id"]] = net.addHost(node["id"], cls=LinuxRouter, ip=None)

        runtime_links = []
        for link in NODE_LINKS:
            src = hosts.get(link["source"])
            dst = hosts.get(link["target"])
            if not src or not dst:
                continue
            mn_link = net.addLink(src, dst, params1={{"ip": link["source_ip"]}}, params2={{"ip": link["target_ip"]}})
            runtime_links.append({{
                **link,
                "source_intf": mn_link.intf1.name,
                "target_intf": mn_link.intf2.name,
            }})

        net.build()
        net.start()
        for node in NODES:
            host = hosts.get(node["id"])
            if not host:
                continue
            host.cmd("ip addr add %s/32 dev lo 2>/dev/null || true" % node["loopback"])
            host.cmd("ip link set lo up")
            host.cmd("sysctl -w net.ipv4.ip_forward=1")
            host.cmd("sysctl -w net.ipv4.conf.all.rp_filter=0")
            host.cmd("sysctl -w net.ipv4.conf.default.rp_filter=0")
            for intf in host.intfList():
                host.cmd("sysctl -w net.ipv4.conf.%s.rp_filter=0" % intf.name)
        install_routes(hosts)

        node_link_tests = run_node_link_tests(hosts, runtime_links)
        pre_acl_report = run_policy_tests(hosts, phase="pre_acl")
        acl_table, acl_conflicts = build_acl_table(hosts)
        installed_acl_table = apply_acl_table(hosts, acl_table)
        post_acl_report = run_policy_tests(hosts, acl_table=acl_table, phase="post_acl")
        diagnosis = build_diagnosis(pre_acl_report, post_acl_report, node_link_tests, acl_conflicts)
        report = {{
            "summary": post_acl_report["summary"],
            "node_link_summary": summarize_link_tests(node_link_tests),
            "node_link_tests": node_link_tests,
            "pre_acl_policy_summary": pre_acl_report["summary"],
            "pre_acl_policy_tests": pre_acl_report["results"],
            "acl_table": installed_acl_table,
            "acl_conflicts": acl_conflicts,
            "post_acl_policy_summary": post_acl_report["summary"],
            "post_acl_policy_tests": post_acl_report["results"],
            "policy_tests": post_acl_report["results"],
            "diagnosis": diagnosis,
            "topology": {{
                "node_count": len(NODES),
                "node_link_count": len(NODE_LINKS),
                "zone_link_count_metadata_only": len(ZONE_LINKS),
            }},
        }}
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        info("Report written to: %s\\n" % REPORT_PATH)
        info("Summary: %s\\n" % report["summary"])
        info("Repair required: %s\\n" % diagnosis["repair_required"])

        if os.environ.get("MININET_INTERACTIVE") == "1":
            CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    run()
'''
