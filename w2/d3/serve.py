from __future__ import annotations

import importlib.util
import json
import logging
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


APP_VERSION = "0.1.0"
GAP_SEC = int(os.getenv("AIOPS_GAP_SEC", "120"))
MAX_HOP = int(os.getenv("AIOPS_MAX_HOP", "2"))
USE_LLM = os.getenv("AIOPS_USE_LLM", "false").lower() == "true"

BASE_DIR = Path(__file__).resolve().parent
W2_DIR = BASE_DIR.parent
D1_DIR = W2_DIR / "d1"
D2_DIR = W2_DIR / "d2"
SERVICES_PATH = D2_DIR / "dataset" / "services.json"
INCIDENTS_PATH = D2_DIR / "dataset" / "incidents_history.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


correlate_mod = load_module("w2d1_correlate", D1_DIR / "correlate.py")
rca_mod = load_module("w2d2_rca", D2_DIR / "rca.py")

GRAPH_LOADED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
CORRELATION_GRAPH = correlate_mod.load_service_graph(SERVICES_PATH)
RCA_GRAPH_BUNDLE = rca_mod.build_graph(SERVICES_PATH)
HISTORY = rca_mod.load_json(INCIDENTS_PATH).get("incidents", [])

logger = logging.getLogger("aiops.serve")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="AIOps Incident API", version=APP_VERSION)


class Alert(BaseModel):
    id: str
    ts: str
    service: str
    metric: str
    severity: str
    value: float | int
    threshold: float | int
    labels: dict[str, Any] = Field(default_factory=dict)


class IncidentRequest(BaseModel):
    alerts: list[Alert]


class RootCause(BaseModel):
    cluster_id: str
    root_cause: str
    root_cause_class: str
    confidence: float
    graph_top3: list[list[Any]]
    reasoning: str
    method: str


class IncidentResponse(BaseModel):
    clusters: list[dict[str, Any]]
    root_cause: RootCause
    recommended_actions: list[str]
    similar_incidents: list[str]
    rca_results: list[dict[str, Any]]
    pipeline_version: str
    timings_ms: dict[str, float]


@app.middleware("http")
async def latency_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    logger.info(
        "request_completed",
        extra={
            "extra": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
            }
        },
    )
    return response


def now_ms() -> float:
    return time.perf_counter() * 1000


def pick_primary_cluster(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    return max(clusters, key=lambda cluster: cluster.get("alert_count", 0))


def process_batch(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    timings: dict[str, float] = {}

    started = now_ms()
    clusters = correlate_mod.correlate(alerts, CORRELATION_GRAPH, gap_sec=GAP_SEC, max_hop=MAX_HOP)
    timings["correlate"] = round(now_ms() - started, 2)

    if not clusters:
        return {
            "clusters": [],
            "root_cause": {
                "cluster_id": "none",
                "root_cause": "unknown",
                "root_cause_class": "other",
                "confidence": 0.0,
                "graph_top3": [],
                "reasoning": "No clusters returned from correlation.",
                "method": "empty-fallback",
            },
            "recommended_actions": ["Investigate manually"],
            "similar_incidents": [],
            "rca_results": [],
            "pipeline_version": APP_VERSION,
            "timings_ms": timings,
        }

    started = now_ms()
    rca_results = [
        rca_mod.analyze_cluster(cluster, alerts, RCA_GRAPH_BUNDLE, HISTORY)
        for cluster in clusters
    ]
    timings["rca"] = round(now_ms() - started, 2)

    primary_cluster = pick_primary_cluster(clusters)
    primary = next(
        result for result in rca_results if result["cluster_id"] == primary_cluster["cluster_id"]
    )

    response = {
        "clusters": clusters,
        "root_cause": {
            "cluster_id": primary["cluster_id"],
            "root_cause": primary["root_cause"],
            "root_cause_class": primary["class"],
            "confidence": primary["confidence"],
            "graph_top3": primary["graph_top3"],
            "reasoning": primary["reasoning"],
            "method": primary["method"],
        },
        "recommended_actions": primary["actions"],
        "similar_incidents": primary["similar_incidents"],
        "rca_results": rca_results,
        "pipeline_version": APP_VERSION,
        "timings_ms": timings,
    }
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    checks = {
        "graph_loaded": bool(CORRELATION_GRAPH) and bool(RCA_GRAPH_BUNDLE.get("graph")),
        "history_loaded": len(HISTORY) > 0,
        "llm_required": USE_LLM,
        "llm_ready": True,
    }
    ready = checks["graph_loaded"] and checks["history_loaded"]
    if not ready:
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ready", "checks": checks}


@app.get("/version")
def version() -> dict[str, Any]:
    graph = RCA_GRAPH_BUNDLE.get("graph", {})
    edge_count = sum(len(targets) for targets in graph.values())
    return {
        "app": APP_VERSION,
        "pipeline_config": {
            "gap_sec": GAP_SEC,
            "max_hop": MAX_HOP,
            "rca_method": "graph+retrieval-knn",
            "use_llm": USE_LLM,
        },
        "graph_version": SERVICES_PATH.stat().st_mtime_ns,
        "graph_loaded_at": GRAPH_LOADED_AT,
        "graph_source": str(SERVICES_PATH),
        "graph_node_count": len(graph),
        "graph_edge_count": edge_count,
    }


@app.post("/incident", response_model=IncidentResponse)
def incident(request: IncidentRequest) -> dict[str, Any]:
    if not request.alerts:
        raise HTTPException(status_code=400, detail="alerts must not be empty")

    validate_start = now_ms()
    alerts = [alert.model_dump() for alert in request.alerts]
    validate_ms = round(now_ms() - validate_start, 2)

    try:
        result = process_batch(alerts)
        result["timings_ms"]["validate"] = validate_ms

        serialize_start = now_ms()
        json.dumps(result)
        result["timings_ms"]["serialize"] = round(now_ms() - serialize_start, 2)
        result["timings_ms"]["total_pipeline"] = round(sum(result["timings_ms"].values()), 2)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("incident_processing_failed")
        raise HTTPException(status_code=500, detail="incident processing failed") from exc


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil((percent / 100) * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def summarize_latencies(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "p50": round(statistics.median(values), 2) if values else 0.0,
        "p99": round(percentile(values, 99), 2),
        "min": round(min(values), 2) if values else 0.0,
        "max": round(max(values), 2) if values else 0.0,
    }
