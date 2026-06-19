from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


FEATURES = ["latency_p99", "error_rate", "rps"]
MODEL_NAME = "anomaly-detector"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
REGISTRY_PATH = OUTPUT_DIR / "registry.json"
MODEL_DIR = OUTPUT_DIR / "models"


@dataclass
class ModelBundle:
    model: Any
    scaler: Any
    features: list[str]
    version: str
    metadata: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "drift_reports").mkdir(parents=True, exist_ok=True)


def read_registry() -> dict[str, Any]:
    ensure_dirs()
    if not REGISTRY_PATH.exists():
        return {"model_name": MODEL_NAME, "versions": {}, "aliases": {}}
    with REGISTRY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_registry(registry: dict[str, Any]) -> None:
    ensure_dirs()
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY_PATH)


def next_version(registry: dict[str, Any]) -> str:
    versions = [int(v) for v in registry.get("versions", {}).keys()]
    return str(max(versions, default=0) + 1)


def load_frame(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required feature columns in {path}: {missing}")
    return df


def feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    return df[FEATURES].astype(float).dropna()


def register_bundle(
    model: Any,
    scaler: Any,
    alias: str,
    metrics: dict[str, Any],
    params: dict[str, Any],
    source_data: str,
    model_name: str = MODEL_NAME,
) -> str:
    registry = read_registry()
    if registry.get("model_name") != model_name:
        registry = {"model_name": model_name, "versions": {}, "aliases": {}}
    version = next_version(registry)
    version_dir = MODEL_DIR / model_name / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)
    bundle = ModelBundle(
        model=model,
        scaler=scaler,
        features=list(FEATURES),
        version=version,
        metadata={
            "model_name": model_name,
            "version": version,
            "created_at": utc_now(),
            "source_data": source_data,
            "metrics": metrics,
            "params": params,
        },
    )
    model_path = version_dir / "model.joblib"
    joblib.dump(bundle, model_path)
    registry["versions"][version] = {
        "path": str(model_path),
        "created_at": bundle.metadata["created_at"],
        "source_data": source_data,
        "metrics": metrics,
        "params": params,
    }
    registry.setdefault("aliases", {})[alias] = version
    write_registry(registry)
    append_audit(
        "model_registered",
        {
            "version": version,
            "alias": alias,
            "source_data": source_data,
            "metrics": metrics,
            "params": params,
        },
    )
    return version


def set_alias(alias: str, version: str, model_name: str = MODEL_NAME) -> None:
    registry = read_registry()
    if version not in registry.get("versions", {}):
        raise ValueError(f"Cannot set alias {alias}: version {version} does not exist")
    registry.setdefault("aliases", {})[alias] = str(version)
    write_registry(registry)
    append_audit("alias_set", {"alias": alias, "version": str(version), "model_name": model_name})


def get_alias(alias: str) -> str | None:
    return read_registry().get("aliases", {}).get(alias)


def load_bundle(version_or_alias: str = "production", model_name: str = MODEL_NAME) -> ModelBundle:
    registry = read_registry()
    version = registry.get("aliases", {}).get(version_or_alias, version_or_alias)
    info = registry.get("versions", {}).get(str(version))
    if not info:
        raise FileNotFoundError(f"No model version found for '{version_or_alias}'. Run pipeline.py first.")
    bundle = joblib.load(info["path"])
    bundle.version = str(version)
    return bundle


def append_audit(event: str, detail: dict[str, Any]) -> None:
    ensure_dirs()
    path = OUTPUT_DIR / "audit_log.jsonl"
    entry = {"timestamp": utc_now(), "event": event, **detail}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def predict_anomaly(bundle: ModelBundle, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    x = feature_matrix(df)
    x_scaled = bundle.scaler.transform(x)
    raw = bundle.model.predict(x_scaled)
    labels = (raw == -1).astype(int)
    if hasattr(bundle.model, "decision_function"):
        scores = -bundle.model.decision_function(x_scaled)
    else:
        scores = np.zeros(len(labels), dtype=float)
    return labels, scores


def precision_recall(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    y_true = np.asarray(y_true).astype(int)
    y_pred = np.asarray(y_pred).astype(int)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    if (tp + fp) == 0:
        precision = 1.0 if int(y_true.sum()) == 0 else 0.0
    else:
        precision = tp / (tp + fp)
    if (tp + fn) == 0:
        recall = 1.0 if int(y_pred.sum()) == 0 else 0.0
    else:
        recall = tp / (tp + fn)
    return precision, recall
