from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from model_store import FEATURES, MODEL_NAME, OUTPUT_DIR, feature_matrix, load_frame, register_bundle


EXPERIMENT_NAME = "anomaly-detection"


def try_log_mlflow(
    model: IsolationForest,
    params: dict[str, Any],
    metrics: dict[str, Any],
    input_example: pd.DataFrame,
    alias: str,
) -> str | None:
    """Best-effort MLflow logging.

    The lab stack provides MLflow, but this file also works offline for local
    grading by falling back to outputs/registry.json.
    """
    try:
        import mlflow
        import mlflow.sklearn
        from mlflow import MlflowClient
    except Exception as exc:
        print(f"[pipeline] MLflow unavailable, using local registry ({type(exc).__name__}: {exc})")
        return None

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(EXPERIMENT_NAME)
        with mlflow.start_run(run_name=f"train-{alias}") as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.sklearn.log_model(
                sk_model=model,
                artifact_path="model",
                registered_model_name=MODEL_NAME,
                input_example=input_example,
            )
            run_id = run.info.run_id

        client = MlflowClient(tracking_uri=tracking_uri)
        versions = client.search_model_versions(f"name='{MODEL_NAME}'")
        latest = max(versions, key=lambda v: int(v.version))
        client.set_registered_model_alias(MODEL_NAME, alias, latest.version)
        print(f"[pipeline] MLflow run     : {run_id}")
        print(f"[pipeline] MLflow alias   : {MODEL_NAME} v{latest.version} -> @{alias}")
        return str(latest.version)
    except Exception as exc:
        print(f"[pipeline] MLflow logging skipped ({type(exc).__name__}: {exc})")
        return None


def train(
    data_path: str,
    alias: str = "production",
    contamination: float = 0.03,
    n_estimators: int = 150,
    random_state: int = 42,
    model_name: str = MODEL_NAME,
) -> str:
    df = load_frame(data_path)
    x = feature_matrix(df)
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)

    model = IsolationForest(
        contamination=contamination,
        n_estimators=n_estimators,
        random_state=random_state,
        n_jobs=-1,
    )
    model.fit(x_scaled)
    labels = model.predict(x_scaled)
    anomaly_rate = float((labels == -1).mean())

    params = {
        "contamination": contamination,
        "n_estimators": n_estimators,
        "random_state": random_state,
        "features": ",".join(FEATURES),
    }
    metrics = {
        "train_anomaly_rate": anomaly_rate,
        "feature_count": len(FEATURES),
        "training_rows": int(len(x)),
    }

    mlflow_version = try_log_mlflow(model, params, metrics, x.head(3), alias)
    local_version = register_bundle(
        model=model,
        scaler=scaler,
        alias=alias,
        metrics={**metrics, "mlflow_version": mlflow_version},
        params=params,
        source_data=str(Path(data_path)),
        model_name=model_name,
    )

    summary_path = OUTPUT_DIR / f"train_v{local_version}.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump({"version": local_version, "alias": alias, "params": params, "metrics": metrics}, f, indent=2)

    print(f"[pipeline] Trained rows  : {len(x)}")
    print(f"[pipeline] Anomaly rate  : {anomaly_rate:.4f}")
    print(f"[pipeline] Local alias   : {model_name} v{local_version} -> @{alias}")
    print(f"[pipeline] Summary       : {summary_path}")
    return local_version


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and register an IsolationForest anomaly detector")
    parser.add_argument("--data", required=True, help="Training CSV, usually data/baseline.csv")
    parser.add_argument("--alias", default="production", choices=["production", "staging", "archived"])
    parser.add_argument("--contamination", type=float, default=0.03)
    parser.add_argument("--n-estimators", type=int, default=150)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()
    train(
        data_path=args.data,
        alias=args.alias,
        contamination=args.contamination,
        n_estimators=args.n_estimators,
        random_state=args.random_state,
    )


if __name__ == "__main__":
    main()
