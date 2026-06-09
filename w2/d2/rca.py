from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


SEVERITY_MAP = {
    "crit": "critical",
    "critical": "critical",
    "warn": "medium",
    "warning": "medium",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_alerts(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_graph(path: str | Path) -> dict[str, Any]:
    data = load_json(path)
    graph: dict[str, set[str]] = defaultdict(set)
    reverse_graph: dict[str, set[str]] = defaultdict(set)
    node_meta: dict[str, dict[str, Any]] = {}

    for node in data.get("services", []):
        node_meta[node["name"]] = {**node, "node_type": "service"}
        graph[node["name"]]
        reverse_graph[node["name"]]

    for node in data.get("stores", []):
        node_meta[node["name"]] = {**node, "node_type": "store"}
        graph[node["name"]]
        reverse_graph[node["name"]]

    for edge in data.get("edges", []):
        src = edge["from"]
        dst = edge["to"]
        graph[src].add(dst)
        reverse_graph[dst].add(src)

    return {
        "graph": dict(graph),
        "reverse_graph": dict(reverse_graph),
        "node_meta": node_meta,
        "edges": data.get("edges", []),
    }


def subgraph_edges(graph: dict[str, set[str]], services: set[str]) -> dict[str, set[str]]:
    return {svc: {dst for dst in graph.get(svc, set()) if dst in services} for svc in services}


def pagerank_scores(subgraph: dict[str, set[str]], iterations: int = 30, damping: float = 0.85) -> dict[str, float]:
    nodes = sorted(subgraph)
    if not nodes:
        return {}

    score = {node: 1.0 / len(nodes) for node in nodes}
    incoming: dict[str, set[str]] = {node: set() for node in nodes}
    for src, targets in subgraph.items():
        for dst in targets:
            incoming.setdefault(dst, set()).add(src)

    for _ in range(iterations):
        next_score = {node: (1 - damping) / len(nodes) for node in nodes}
        sink_score = sum(score[node] for node in nodes if not subgraph.get(node))
        for node in nodes:
            next_score[node] += damping * sink_score / len(nodes)
            for src in incoming.get(node, set()):
                out_degree = max(len(subgraph.get(src, set())), 1)
                next_score[node] += damping * score[src] / out_degree
        score = next_score

    max_score = max(score.values()) or 1.0
    return {node: value / max_score for node, value in score.items()}


def service_first_seen(alerts: list[dict[str, Any]], services: set[str]) -> dict[str, datetime]:
    first_seen: dict[str, datetime] = {}
    for alert in alerts:
        service = alert["service"]
        if service not in services:
            continue
        ts = parse_ts(alert["ts"])
        if service not in first_seen or ts < first_seen[service]:
            first_seen[service] = ts
    return first_seen


def temporal_scores(first_seen: dict[str, datetime]) -> dict[str, float]:
    if not first_seen:
        return {}
    ordered = sorted(first_seen.items(), key=lambda item: item[1])
    if len(ordered) == 1:
        return {ordered[0][0]: 1.0}

    start = ordered[0][1]
    end = ordered[-1][1]
    span = max((end - start).total_seconds(), 1.0)
    return {
        service: 1.0 - ((seen_at - start).total_seconds() / span)
        for service, seen_at in first_seen.items()
    }


def metric_boost(cluster: dict[str, Any], service: str) -> float:
    fingerprints = cluster.get("fingerprints", [])
    service_metrics = [fp.split("|")[1] for fp in fingerprints if fp.startswith(service + "|")]
    text = " ".join(service_metrics).lower()
    boost = 0.0
    if "connection_pool" in text or "db_connection" in text:
        boost += 0.16
    if "error_rate" in text:
        boost += 0.06
    if "cpu" in text or "query_time" in text:
        boost += 0.04
    return boost


def graph_temporal_top_k(
    cluster: dict[str, Any],
    alerts: list[dict[str, Any]],
    graph_bundle: dict[str, Any],
    top_k: int = 3,
) -> list[list[Any]]:
    services = set(cluster.get("services", []))
    graph = graph_bundle["graph"]
    subgraph = subgraph_edges(graph, services)
    pagerank = pagerank_scores(subgraph)
    first_seen = service_first_seen(alerts, services)
    temporal = temporal_scores(first_seen)

    in_degree = Counter()
    out_degree = Counter()
    for src, targets in subgraph.items():
        out_degree[src] = len(targets)
        for dst in targets:
            in_degree[dst] += 1

    scores: dict[str, float] = {}
    max_in_degree = max(in_degree.values() or [1])
    for service in services:
        terminal_score = 1.0 / (1 + out_degree[service])
        dependency_score = in_degree[service] / max_in_degree if max_in_degree else 0.0
        raw_score = (
            0.42 * pagerank.get(service, 0.0)
            + 0.28 * temporal.get(service, 0.0)
            + 0.18 * terminal_score
            + 0.12 * dependency_score
            + metric_boost(cluster, service)
        )
        scores[service] = min(raw_score, 1.0)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [[service, round(score, 2)] for service, score in ranked[:top_k]]


def tokenize(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-zA-Z0-9]+", value.replace("_", " ").lower()) if token}


def cluster_text(cluster: dict[str, Any]) -> str:
    return " ".join(
        list(cluster.get("services", []))
        + list(cluster.get("fingerprints", []))
        + [cluster.get("max_severity", "")]
    )


def keyword_similarity(cluster: dict[str, Any], incident: dict[str, Any], graph_top1: str | None) -> float:
    cluster_services = set(cluster.get("services", []))
    incident_services = set(incident.get("services_involved", []))
    service_overlap = len(cluster_services & incident_services)
    overlap_score = min(service_overlap * 0.12, 0.36)

    root_score = 0.10 if incident.get("root_cause_service") in cluster_services else 0.0
    top1_score = 0.24 if graph_top1 and incident.get("root_cause_service") == graph_top1 else 0.0

    cluster_severity = SEVERITY_MAP.get(cluster.get("max_severity", ""), cluster.get("max_severity", ""))
    incident_severity = SEVERITY_MAP.get(incident.get("severity", ""), incident.get("severity", ""))
    severity_score = 0.08 if cluster_severity == incident_severity else 0.0

    query_tokens = tokenize(cluster_text(cluster))
    doc_tokens = tokenize(
        " ".join(
            [
                incident.get("summary", ""),
                incident.get("root_cause_class", ""),
                incident.get("root_cause_service", ""),
                " ".join(incident.get("services_involved", [])),
            ]
        )
    )
    jaccard = len(query_tokens & doc_tokens) / len(query_tokens | doc_tokens) if query_tokens and doc_tokens else 0.0
    token_score = min(jaccard * 1.8, 0.22)

    fingerprints = " ".join(cluster.get("fingerprints", [])).lower()
    root_class = incident.get("root_cause_class", "")
    summary = incident.get("summary", "").lower()
    metric_class_score = 0.0
    if "connection_pool" in fingerprints and root_class == "connection_pool_exhaustion":
        metric_class_score += 0.20
    if "db_connection" in fingerprints and "pool" in summary:
        metric_class_score += 0.10
    if "cpu" in fingerprints and ("memory" in root_class or "batch" in root_class):
        metric_class_score += 0.06
    if "query_time" in fingerprints and ("query" in root_class or "cache" in root_class):
        metric_class_score += 0.06

    return round(min(root_score + top1_score + overlap_score + severity_score + token_score + metric_class_score, 1.0), 3)


def retrieve_similar_incidents(
    cluster: dict[str, Any],
    incidents: list[dict[str, Any]],
    graph_top3: list[list[Any]],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    graph_top1 = graph_top3[0][0] if graph_top3 else None
    scored = []
    for incident in incidents:
        similarity = keyword_similarity(cluster, incident, graph_top1)
        if similarity >= 0.2:
            overlap_count = len(set(cluster.get("services", [])) & set(incident.get("services_involved", [])))
            scored.append({**incident, "_similarity": similarity, "_overlap_count": overlap_count})
    return sorted(scored, key=lambda item: (-item["_similarity"], -item["_overlap_count"], item["id"]))[:top_k]


def split_actions(remediation: str) -> list[str]:
    actions = [part.strip().rstrip(".") for part in re.split(r"(?<=[.!?])\s+", remediation) if part.strip()]
    return actions or ["Investigate manually"]


def classify_knn(
    cluster: dict[str, Any],
    graph_top3: list[list[Any]],
    similar_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    if not graph_top3:
        graph_top3 = [[cluster.get("services", ["unknown"])[0], 0.1]]

    graph_root = graph_top3[0][0]
    graph_confidence = graph_top3[0][1]

    if similar_incidents:
        top = similar_incidents[0]
        retrieval_confidence = top.get("_similarity", 0.0)
        confidence = min(0.99, 0.55 * graph_confidence + 0.45 * retrieval_confidence)
        root_cause = top.get("root_cause_service") if top.get("root_cause_service") in cluster.get("services", []) else graph_root
        root_class = top.get("root_cause_class", "other")
        actions = split_actions(top.get("remediation", ""))
        method = "graph+retrieval-knn"
        reasoning = (
            f"Graph scorer ranks {graph_root} highest. Retrieval top-1 is {top['id']} "
            f"with similarity {retrieval_confidence:.2f}, class {root_class}, "
            f"and overlapping services {sorted(set(cluster.get('services', [])) & set(top.get('services_involved', [])))}."
        )
    else:
        confidence = graph_confidence * 0.7
        root_cause = graph_root
        root_class = "other"
        actions = ["Investigate manually"]
        method = "graph-only-fallback"
        reasoning = "No similar incident cleared the retrieval threshold, so the output falls back to graph top-1."

    return {
        "root_cause": root_cause,
        "class": root_class,
        "confidence": round(confidence, 2),
        "actions": actions,
        "reasoning": reasoning,
        "similar_incidents": [incident["id"] for incident in similar_incidents],
        "method": method,
    }


def validate_result(result: dict[str, Any], cluster: dict[str, Any]) -> dict[str, Any]:
    services = set(cluster.get("services", []))
    if result.get("root_cause") not in services:
        result["root_cause"] = result["graph_top3"][0][0] if result.get("graph_top3") else next(iter(services), "unknown")
        result["method"] = "validated-fallback"
    if not isinstance(result.get("confidence"), (int, float)) or not 0 <= result["confidence"] <= 1:
        result["confidence"] = 0.5
    if not result.get("class"):
        result["class"] = "other"
    if not isinstance(result.get("actions"), list) or not result["actions"]:
        result["actions"] = ["Investigate manually"]
    return result


def analyze_cluster(
    cluster: dict[str, Any],
    alerts: list[dict[str, Any]],
    graph_bundle: dict[str, Any],
    incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    graph_top3 = graph_temporal_top_k(cluster, alerts, graph_bundle, top_k=3)
    similar_incidents = retrieve_similar_incidents(cluster, incidents, graph_top3, top_k=3)
    classified = classify_knn(cluster, graph_top3, similar_incidents)
    result = {
        "cluster_id": cluster["cluster_id"],
        "graph_top3": graph_top3,
        **classified,
    }
    return validate_result(result, cluster)


def build_rca_output(
    cluster_summary: dict[str, Any],
    alerts: list[dict[str, Any]],
    graph_bundle: dict[str, Any],
    incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    results = [
        analyze_cluster(cluster, alerts, graph_bundle, incidents)
        for cluster in cluster_summary.get("clusters", [])
    ]
    return {
        "clusters_analyzed": len(results),
        "results": results,
    }


def write_rca_output(
    cluster_path: str | Path = "../d1/results/cluster_summary.json",
    alerts_path: str | Path = "dataset/alerts_sample.jsonl",
    services_path: str | Path = "dataset/services.json",
    incidents_path: str | Path = "dataset/incidents_history.json",
    output_path: str | Path = "results/rca_output.json",
) -> dict[str, Any]:
    base_dir = Path(__file__).resolve().parent

    def resolve(path: str | Path) -> Path:
        path = Path(path)
        return path if path.is_absolute() else base_dir / path

    cluster_summary = load_json(resolve(cluster_path))
    alerts = load_alerts(resolve(alerts_path))
    graph_bundle = build_graph(resolve(services_path))
    incidents = load_json(resolve(incidents_path)).get("incidents", [])
    output = build_rca_output(cluster_summary, alerts, graph_bundle, incidents)

    output_file = resolve(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output_file.with_suffix(output_file.suffix + ".tmp")
    tmp_output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_output, output_file)
    return output


if __name__ == "__main__":
    print(json.dumps(write_rca_output(), indent=2))
