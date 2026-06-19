from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from model_store import FEATURES, MODEL_NAME, load_bundle, predict_anomaly


app = FastAPI(title="MLOps Lifecycle Anomaly Detector", version="1.0")
ACTIVE = {"bundle": None, "version": None, "alias": "production"}


class PredictRequest(BaseModel):
    latency_p99: float = Field(..., ge=0)
    error_rate: float = Field(..., ge=0)
    rps: float = Field(..., ge=0)


def load_active(alias: str = "production") -> dict[str, Any]:
    bundle = load_bundle(alias)
    ACTIVE["bundle"] = bundle
    ACTIVE["version"] = bundle.version
    ACTIVE["alias"] = alias
    return {"model_name": MODEL_NAME, "version": bundle.version, "alias": alias}


@app.on_event("startup")
def startup() -> None:
    try:
        load_active("production")
    except Exception as exc:
        print(f"[serve] Startup load skipped: {type(exc).__name__}: {exc}")


@app.get("/health/active-version")
def active_version() -> dict[str, Any]:
    if ACTIVE["bundle"] is None:
        raise HTTPException(status_code=503, detail="No active production model. Run pipeline.py first.")
    return {
        "status": "ok",
        "model_name": MODEL_NAME,
        "alias": ACTIVE["alias"],
        "version": ACTIVE["version"],
        "features": FEATURES,
    }


@app.post("/reload")
def reload_model() -> dict[str, Any]:
    return {"status": "reloaded", **load_active("production")}


@app.post("/predict")
def predict(req: PredictRequest) -> dict[str, Any]:
    if ACTIVE["bundle"] is None:
        try:
            load_active("production")
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"No active model: {exc}") from exc
    df = pd.DataFrame([req.dict()])
    label, score = predict_anomaly(ACTIVE["bundle"], df)
    return {
        "model_name": MODEL_NAME,
        "version": ACTIVE["version"],
        "prediction": int(label[0]),
        "is_anomaly": bool(label[0] == 1),
        "anomaly_score": float(score[0]),
    }


@app.get("/metrics")
def metrics() -> str:
    version = ACTIVE["version"] or "none"
    return (
        "# HELP mlops_active_model_version Active model version exposed as label.\n"
        "# TYPE mlops_active_model_info gauge\n"
        f'mlops_active_model_info{{model="{MODEL_NAME}",version="{version}",alias="{ACTIVE["alias"]}"}} 1\n'
    )


def main() -> None:
    import uvicorn

    uvicorn.run("serve:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
