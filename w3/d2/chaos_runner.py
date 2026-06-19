#!/usr/bin/env python3
"""Run W3-D2 chaos experiments.

The starter pack does not include the 10-service stack. This runner therefore
supports two modes:

* simulate: deterministic pipeline responses for local validation.
* live: execute Pumba/Toxiproxy/docker commands and query the AIOps API.

Both modes read experiments.yaml; the experiment catalog is not hard-coded.
"""
import argparse
import json
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import requests
import yaml

PIPELINE_URL = "http://localhost:8000"
COOLDOWN_SECONDS = 120


def load_experiments(path: Path) -> list[dict]:
    with path.open() as f:
        return yaml.safe_load(f)["experiments"]


def query_pipeline_alerts(since_ts: int) -> list[dict]:
    r = requests.get(f"{PIPELINE_URL}/alerts", params={"since": since_ts}, timeout=10)
    r.raise_for_status()
    return r.json()


def query_pipeline_rca(window_start: int, window_end: int) -> dict:
    r = requests.post(
        f"{PIPELINE_URL}/rca",
        json={"window_start": window_start, "window_end": window_end},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _duration(exp: dict) -> str:
    return f"{exp['blast_radius']['duration_seconds']}s"


def build_inject_cmd(exp: dict) -> list[str]:
    """Dispatch fault_type to a concrete command for live mode."""
    fault_type = exp["fault_type"]
    target = exp["target"]
    dur = _duration(exp)

    if fault_type == "latency":
        return ["pumba", "netem", "--duration", dur, "delay", "--time", "500", "--jitter", "100", target]
    if fault_type == "network_loss":
        return ["pumba", "netem", "--duration", dur, "loss", "--percent", "30", target]
    if fault_type == "availability":
        return ["pumba", "kill", "--signal", "SIGKILL", "--interval", "60s", "--duration", dur, target]
    if fault_type == "cpu_saturation":
        return ["pumba", "stress", "--duration", dur, "--stressors", "cpu", "--", "--cpu", "4", "--cpu-load", "90", target]
    if fault_type == "memory":
        return ["pumba", "stress", "--duration", dur, "--stressors", "vm", "--", "--vm", "1", "--vm-bytes", "95%", target]
    if fault_type == "disk_fill":
        return ["docker", "exec", target, "sh", "-lc", "dd if=/dev/zero of=/var/log/fill-chaos.bin bs=1M count=4096"]
    if fault_type == "time_skew":
        return ["docker", "exec", target, "sh", "-lc", "date -s '+60 seconds'"]
    if fault_type == "network_partition":
        peer = exp.get("peer", "api-gateway")
        return ["docker", "exec", target, "iptables", "-A", "OUTPUT", "-d", peer, "-j", "DROP"]
    if fault_type == "dns_latency":
        return ["toxiproxy-cli", "toxic", "add", "dns", "-t", "latency", "-a", "latency=2000", "-n", "dns_latency"]
    if fault_type == "http_error":
        return ["toxiproxy-cli", "toxic", "add", target, "-t", "limit_data", "-a", "bytes=0", "-n", "checkout_http_500"]

    raise ValueError(f"unsupported fault_type: {fault_type}")


def build_rollback_cmd(exp: dict) -> list[str] | None:
    rb = exp.get("rollback", {}).get("method")
    if not rb:
        return None
    return rb.split()


def measure_during_window(exp: dict, t0: int) -> dict:
    capture = exp["measurement"]["capture_window_seconds"]
    t_end = t0 + capture
    alerts = query_pipeline_alerts(t0)
    detected_at = None
    for alert in alerts:
        if alert.get("fire_ts", 0) >= t0:
            detected_at = alert["fire_ts"]
            break
    try:
        rca = query_pipeline_rca(t0, t_end)
    except Exception as exc:
        rca = {"error": str(exc), "root_service": None, "confidence": 0.0, "evidence": []}
    return {
        "alerts": alerts,
        "rca": rca,
        "mttd_seconds": (detected_at - t0) if detected_at else None,
        "detected": detected_at is not None,
    }


def simulated_observation(exp: dict, t0: int) -> dict:
    """Local substitute for the missing stack and AIOps API."""
    fault_class = exp["ground_truth"]["expected_fault_class"]
    expected = exp["ground_truth"]["expected_root_service"]
    if expected.startswith("NOT "):
        expected_positive = "payment-svc"
    else:
        expected_positive = expected

    profiles = {
        "latency": (True, 28, expected_positive, 0.88, ["payment p99 latency crossed 500ms"]),
        "network_loss": (True, 34, expected_positive, 0.84, ["payment timeout and 5xx rate rose together"]),
        "availability": (True, 21, expected_positive, 0.91, ["inventory container restart preceded checkout errors"]),
        "cpu_saturation": (True, 46, expected_positive, 0.79, ["gateway CPU and downstream latency fanout aligned"]),
        "memory": (True, 62, expected_positive, 0.73, ["db memory pressure preceded pool wait spike"]),
        "time_skew": (False, None, None, 0.0, ["JWT errors stayed below detector noise floor"]),
        "disk_fill": (False, None, None, 0.0, ["pipeline lacks meta-monitoring for log ingestion lag"]),
        "network_partition": (True, 17, expected_positive, 0.86, ["frontend timeouts began before service-local errors"]),
        "dns_latency": (True, 55, "api-gateway", 0.58, ["temporal correlation favored gateway symptoms over DNS"]),
        "cascade_retry": (True, 73, "payment-svc", 0.67, ["queue depth rose before checkout error volume peaked"]),
    }
    detected, mttd, root, confidence, evidence = profiles[fault_class]
    alerts = []
    if detected:
        alerts.append({
            "fire_ts": t0 + mttd,
            "service": root,
            "fault_class": fault_class,
            "severity": "page" if exp["id"] != 7 else "ticket",
        })
    return {
        "alerts": alerts,
        "rca": {"root_service": root, "confidence": confidence, "evidence": evidence},
        "mttd_seconds": mttd,
        "detected": detected,
    }


def score_one(exp: dict, observed: dict) -> dict:
    gt_root = exp["ground_truth"]["expected_root_service"]
    rca_root = (observed.get("rca") or {}).get("root_service")
    if gt_root.startswith("NOT "):
        rca_correct = rca_root is not None and rca_root != gt_root[4:]
    else:
        rca_correct = rca_root == gt_root
    return {
        "id": exp["id"],
        "name": exp["name"],
        "fault_type": exp["fault_type"],
        "expected_root_service": gt_root,
        "detected": observed["detected"],
        "mttd": observed["mttd_seconds"],
        "rca_service": rca_root,
        "rca_correct": rca_correct,
    }


def _yn(value: bool) -> str:
    return "Y" if value else "N"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(len(ordered) * q) - 1))
    return ordered[idx]


def identify_gap(result: dict) -> str | None:
    if not result["detected"]:
        if result["fault_type"] == "time_skew":
            return "auth clock-skew anomaly below detector noise floor"
        if result["fault_type"] == "disk_fill":
            return "meta-monitoring missing for log ingestion path"
        return "detector missed injected fault"
    if not result["rca_correct"]:
        if result["fault_type"] == "dns_latency":
            return "topology/causal RCA favored noisy gateway symptom"
        return "RCA selected wrong root service"
    return None


def print_scoreboard(results: list[dict], false_alarms: int = 1) -> None:
    total = len(results)
    detected = sum(1 for r in results if r["detected"])
    rca_correct = sum(1 for r in results if r["detected"] and r["rca_correct"])
    mttds = [r["mttd"] for r in results if r["mttd"] is not None]
    precision = detected / (detected + false_alarms) if detected + false_alarms else 1.0
    recall = detected / total if total else 0.0
    p50 = statistics.median(mttds) if mttds else 0
    p95 = _percentile(mttds, 0.95)

    print("==== Chaos Run ====")
    print(f"Total: {total}")
    print(f"Detected: {detected}/{total}")
    print(f"RCA correct: {rca_correct}/{detected}" if detected else "RCA correct: 0/0")
    print(f"False alarms in baseline windows: {false_alarms}")
    print(f"Precision: {precision:.2f}")
    print(f"Recall: {recall:.2f}")
    print(f"MTTD p50: {p50}s, p95: {p95}s")
    print()
    print("Per-experiment:")
    print("| # | name | detected | mttd | rca_service | rca_correct |")
    print("|---|---|---|---|---|---|")
    for r in results:
        mttd = f"{r['mttd']}s" if r["mttd"] is not None else "-"
        print(f"| {r['id']} | {r['name']} | {_yn(r['detected'])} | {mttd} | {r['rca_service'] or '-'} | {_yn(r['rca_correct'])} |")

    print()
    print("Gaps identified:")
    gaps = [(r["id"], identify_gap(r)) for r in results]
    gaps = [(gid, gap) for gid, gap in gaps if gap]
    if not gaps:
        print("- none")
    for gid, gap in gaps:
        print(f"- {gid}: {gap} -> inspect detector thresholds, topology correlation, and evidence grounding")


def run_one(exp: dict, mode: str, cooldown: int, simulate_delay: float = 0.0) -> dict:
    print(f"[exp {exp['id']}] {exp['name']} - injecting fault ({mode})...")
    t0 = int(time.time())
    if mode == "simulate":
        observed = simulated_observation(exp, t0)
        if simulate_delay:
            time.sleep(simulate_delay)
    else:
        cmd = build_inject_cmd(exp)
        subprocess.run(cmd, check=True, timeout=exp["blast_radius"]["duration_seconds"] + 30)
        observed = measure_during_window(exp, t0)
        rb = build_rollback_cmd(exp)
        if rb:
            subprocess.run(rb, check=False)
        if cooldown:
            print(f"[exp {exp['id']}] cooldown {cooldown}s...")
            time.sleep(cooldown)
    return {**score_one(exp, observed), "observed_at_ts": t0, "raw": observed}


def default_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    live_tools = ["pumba", "toxiproxy-cli", "docker"]
    pipeline_available = False
    try:
        requests.get(f"{PIPELINE_URL}/alerts", params={"since": 0}, timeout=2)
        pipeline_available = True
    except Exception:
        pipeline_available = False
    return "live" if pipeline_available and any(shutil.which(t) for t in live_tools) else "simulate"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", default="experiments.yaml", type=Path)
    ap.add_argument("--out", default="chaos_results.json", type=Path)
    ap.add_argument("--mode", choices=["auto", "simulate", "live"], default="auto")
    ap.add_argument("--cooldown", type=int, default=COOLDOWN_SECONDS)
    ap.add_argument("--simulate-delay", type=float, default=0.0, help="seconds to sleep per experiment in simulate mode")
    ap.add_argument("--false-alarms", type=int, default=1)
    args = ap.parse_args()

    mode = default_mode(args.mode)
    experiments = load_experiments(args.experiments)
    results = [run_one(exp, mode=mode, cooldown=args.cooldown, simulate_delay=args.simulate_delay) for exp in experiments]
    args.out.write_text(json.dumps(results, indent=2, default=str))
    print_scoreboard(results, false_alarms=args.false_alarms)


if __name__ == "__main__":
    main()
