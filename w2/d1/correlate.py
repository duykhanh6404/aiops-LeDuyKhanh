from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any


SEVERITY_RANK = {
    "info": 0,
    "warn": 1,
    "warning": 1,
    "crit": 2,
    "critical": 2,
}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fingerprint(alert: dict[str, Any]) -> str:
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"


def load_alerts(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_service_graph(path: str | Path) -> dict[str, set[str]]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    graph: dict[str, set[str]] = defaultdict(set)
    for node in data.get("services", []) + data.get("stores", []):
        graph[node["name"]]

    for edge in data.get("edges", []):
        src = edge["from"]
        dst = edge["to"]
        graph[src].add(dst)
        graph[dst].add(src)

    return dict(graph)


def session_groups(alerts: list[dict[str, Any]], gap_sec: int = 120) -> list[list[dict[str, Any]]]:
    if not alerts:
        return []

    sorted_alerts = sorted(alerts, key=lambda a: parse_ts(a["ts"]))
    groups = [[sorted_alerts[0]]]

    for alert in sorted_alerts[1:]:
        last_ts = parse_ts(groups[-1][-1]["ts"])
        if (parse_ts(alert["ts"]) - last_ts).total_seconds() <= gap_sec:
            groups[-1].append(alert)
        else:
            groups.append([alert])

    return groups


def shortest_hop(graph: dict[str, set[str]], start: str, target: str) -> int | None:
    if start == target:
        return 0
    if start not in graph or target not in graph:
        return None

    seen = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, depth = queue.popleft()
        for neighbor in graph.get(node, set()):
            if neighbor == target:
                return depth + 1
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, depth + 1))

    return None


def independent_note(alert: dict[str, Any]) -> bool:
    note = str(alert.get("labels", {}).get("note", "")).lower()
    return "unrelated" in note or "independent" in note or "noise" in note


def topology_group(
    alerts: list[dict[str, Any]],
    graph: dict[str, set[str]],
    max_hop: int = 2,
) -> list[list[dict[str, Any]]]:
    if not alerts:
        return []

    parent = {idx: idx for idx in range(len(alerts))}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for i, left in enumerate(alerts):
        for j in range(i + 1, len(alerts)):
            right = alerts[j]

            if independent_note(left) or independent_note(right):
                continue

            if left["service"] == right["service"]:
                union(i, j)
                continue

            distance = shortest_hop(graph, left["service"], right["service"])
            if distance is not None and distance <= max_hop:
                union(i, j)

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for idx, alert in enumerate(alerts):
        groups[find(idx)].append(alert)

    return sorted(groups.values(), key=lambda group: parse_ts(min(a["ts"] for a in group)))


def max_severity(alerts: list[dict[str, Any]]) -> str:
    return max(alerts, key=lambda a: SEVERITY_RANK.get(a["severity"], -1))["severity"]


def summarize_cluster(cluster_id: str, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(alerts, key=lambda a: parse_ts(a["ts"]))
    return {
        "cluster_id": cluster_id,
        "alert_count": len(ordered),
        "services": sorted({a["service"] for a in ordered}),
        "time_range": [ordered[0]["ts"], ordered[-1]["ts"]],
        "max_severity": max_severity(ordered),
        "fingerprints": sorted({fingerprint(a) for a in ordered}),
    }


def correlate(
    alerts: list[dict[str, Any]],
    graph: dict[str, set[str]],
    gap_sec: int = 120,
    max_hop: int = 2,
) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []

    for session_idx, session_alerts in enumerate(session_groups(alerts, gap_sec=gap_sec)):
        topology_groups = topology_group(session_alerts, graph, max_hop=max_hop)
        for group_idx, group in enumerate(topology_groups):
            cluster_id = f"c-{session_idx:03d}-{group_idx:03d}"
            clusters.append(summarize_cluster(cluster_id, group))

    return clusters


def build_summary(
    alerts: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    input_alerts = len(alerts)
    output_clusters = len(clusters)
    reduction_ratio = 1 - output_clusters / input_alerts if input_alerts else 0

    return {
        "input_alerts": input_alerts,
        "output_clusters": output_clusters,
        "reduction_ratio": round(reduction_ratio, 2),
        "clusters": clusters,
    }


def write_summary(
    alerts_path: str | Path = "dataset/alerts_sample.jsonl",
    services_path: str | Path = "dataset/services.json",
    output_path: str | Path = "results/cluster_summary.json",
    gap_sec: int = 120,
    max_hop: int = 2,
) -> dict[str, Any]:
    base_dir = Path(__file__).resolve().parent
    alerts_path = base_dir / alerts_path if not Path(alerts_path).is_absolute() else Path(alerts_path)
    services_path = base_dir / services_path if not Path(services_path).is_absolute() else Path(services_path)
    output_path = base_dir / output_path if not Path(output_path).is_absolute() else Path(output_path)

    alerts = load_alerts(alerts_path)
    graph = load_service_graph(services_path)
    clusters = correlate(alerts, graph, gap_sec=gap_sec, max_hop=max_hop)
    summary = build_summary(alerts, clusters)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_suffix(output.suffix + ".tmp")
    tmp_output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    os.replace(tmp_output, output)
    return summary


if __name__ == "__main__":
    result = write_summary()
    print(json.dumps(result, indent=2))
