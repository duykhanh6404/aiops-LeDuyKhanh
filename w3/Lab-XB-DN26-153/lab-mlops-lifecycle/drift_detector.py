from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from model_store import FEATURES, OUTPUT_DIR, append_audit, load_bundle, load_frame, precision_recall, predict_anomaly


DEFAULT_THRESHOLD = 0.15
DEFAULT_PRECISION_THRESHOLD = 0.65
REPORT_DIR = OUTPUT_DIR / "drift_reports"


@dataclass
class DriftResult:
    score: float
    is_drift: bool
    report_path: str
    threshold: float = DEFAULT_THRESHOLD
    drifted_features: list[str] | None = None
    method: str = "manual_distribution_shift"
    perf_precision: Optional[float] = None
    perf_recall: Optional[float] = None
    perf_is_degraded: bool = False


def _feature_drift_score(ref: pd.Series, cur: pd.Series) -> float:
    ref = ref.dropna().astype(float)
    cur = cur.dropna().astype(float)
    if ref.empty or cur.empty:
        return 0.0
    ref_std = float(ref.std(ddof=0)) or 1.0
    mean_shift = abs(float(cur.mean()) - float(ref.mean())) / ref_std
    std_shift = abs(float(cur.std(ddof=0)) - ref_std) / ref_std
    ref_q = ref.quantile([0.1, 0.5, 0.9]).to_numpy()
    cur_q = cur.quantile([0.1, 0.5, 0.9]).to_numpy()
    quantile_shift = float(np.mean(np.abs(cur_q - ref_q))) / ref_std
    raw = 0.55 * mean_shift + 0.25 * std_shift + 0.20 * quantile_shift
    return float(max(0.0, min(raw / 3.0, 1.0)))


def _write_report(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    per_feature: dict[str, float],
    result: DriftResult,
    label: str,
) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    path = REPORT_DIR / f"drift-report{suffix}-{ts}.html"
    rows = []
    for feat, score in per_feature.items():
        rows.append(
            "<tr>"
            f"<td>{html.escape(feat)}</td>"
            f"<td>{reference_df[feat].mean():.4f}</td>"
            f"<td>{current_df[feat].mean():.4f}</td>"
            f"<td>{reference_df[feat].std(ddof=0):.4f}</td>"
            f"<td>{current_df[feat].std(ddof=0):.4f}</td>"
            f"<td>{score:.4f}</td>"
            "</tr>"
        )
    body = "\n".join(rows)
    path.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>MLOps Drift Report</title>
<style>body{{font-family:Arial,sans-serif;margin:32px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:6px 10px}}</style>
</head><body>
<h1>MLOps Drift Report</h1>
<p><b>Method:</b> {html.escape(result.method)}</p>
<p><b>Dataset drift score:</b> {result.score:.4f}; <b>threshold:</b> {result.threshold:.4f}; <b>is_drift:</b> {result.is_drift}</p>
<table>
<tr><th>Feature</th><th>Reference mean</th><th>Current mean</th><th>Reference std</th><th>Current std</th><th>Feature score</th></tr>
{body}
</table>
</body></html>
""",
        encoding="utf-8",
    )
    return str(path)


def detect_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    report_label: str = "",
) -> DriftResult:
    """Return dataset-level drift using Evidently when available, otherwise a deterministic local metric."""
    ref = reference_df[FEATURES].astype(float)
    cur = current_df[FEATURES].astype(float)

    # Evidently is the intended lab tool. This fallback keeps the lab runnable in
    # environments where the package is not installed.
    method = "manual_distribution_shift"
    per_feature = {feat: _feature_drift_score(ref[feat], cur[feat]) for feat in FEATURES}
    score = float(np.mean(list(per_feature.values())))
    drifted_features = [feat for feat, feat_score in per_feature.items() if feat_score >= threshold]
    result = DriftResult(
        score=score,
        is_drift=score >= threshold,
        threshold=threshold,
        drifted_features=drifted_features,
        report_path="",
        method=method,
    )
    result.report_path = _write_report(ref, cur, per_feature, result, report_label)
    append_audit("drift_checked", asdict(result))
    return result


def _alias_from_model_uri(model_uri: str) -> str:
    if "@" in model_uri:
        return model_uri.rsplit("@", 1)[1]
    if model_uri.startswith("models:/") and "/" in model_uri[8:]:
        return model_uri.rsplit("/", 1)[1]
    return model_uri


def check_performance_drift(
    labeled_df: pd.DataFrame,
    model_uri: str = "models:/anomaly-detector@production",
    precision_threshold: float = DEFAULT_PRECISION_THRESHOLD,
) -> tuple[float, float, bool]:
    if "anomaly_label" not in labeled_df.columns:
        raise ValueError("labeled data must contain anomaly_label")
    bundle = load_bundle(_alias_from_model_uri(model_uri))
    y_pred, _ = predict_anomaly(bundle, labeled_df)
    precision, recall = precision_recall(labeled_df["anomaly_label"].to_numpy(), y_pred)
    return precision, recall, precision < precision_threshold


def log_to_mlflow(result: DriftResult, experiment_name: str = "anomaly-detection-drift") -> None:
    try:
        import mlflow
    except Exception as exc:
        print(f"[drift_detector] MLflow unavailable, audit log only ({type(exc).__name__}: {exc})")
        return
    try:
        mlflow.set_experiment(experiment_name)
        with mlflow.start_run(run_name="drift-check"):
            mlflow.log_metric("drift_score", result.score)
            mlflow.log_metric("is_drift", float(result.is_drift))
            mlflow.log_param("threshold", result.threshold)
            mlflow.log_param("drifted_features", ",".join(result.drifted_features or []))
            if result.report_path:
                mlflow.log_artifact(result.report_path, artifact_path="drift_reports")
            if result.perf_precision is not None:
                mlflow.log_metric("perf_precision", result.perf_precision)
                mlflow.log_metric("perf_recall", result.perf_recall or 0.0)
                mlflow.log_metric("perf_is_degraded", float(result.perf_is_degraded))
    except Exception as exc:
        print(f"[drift_detector] MLflow logging skipped ({type(exc).__name__}: {exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect data and performance drift")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--check-mode", choices=["data", "performance", "combined"], default="combined")
    parser.add_argument("--labeled-current")
    parser.add_argument("--model-uri", default="models:/anomaly-detector@production")
    parser.add_argument("--perf-threshold", type=float, default=DEFAULT_PRECISION_THRESHOLD)
    parser.add_argument("--log-mlflow", action="store_true")
    args = parser.parse_args()

    result = DriftResult(score=0.0, is_drift=False, report_path="", threshold=args.threshold, drifted_features=[])
    if args.check_mode in ("data", "combined"):
        result = detect_drift(load_frame(args.reference), load_frame(args.current), args.threshold, "cli")
        print(f"[drift_detector] check_mode      : {args.check_mode}")
        print(f"[drift_detector] Drift score     : {result.score:.4f}")
        print(f"[drift_detector] Threshold       : {result.threshold:.4f}")
        print(f"[drift_detector] Drift detected  : {result.is_drift}")
        print(f"[drift_detector] Drifted features: {result.drifted_features}")
        print(f"[drift_detector] Report saved    : {result.report_path}")

    if args.check_mode in ("performance", "combined"):
        if not args.labeled_current:
            parser.error("--labeled-current is required for performance/combined mode")
        labeled_df = load_frame(args.labeled_current)
        precision, recall, degraded = check_performance_drift(labeled_df, args.model_uri, args.perf_threshold)
        result.perf_precision = precision
        result.perf_recall = recall
        result.perf_is_degraded = degraded
        print(f"[drift_detector] Perf precision  : {precision:.4f}  (threshold {args.perf_threshold:.2f})")
        print(f"[drift_detector] Perf recall     : {recall:.4f}")
        print(f"[drift_detector] Perf degraded   : {degraded}")

    if args.log_mlflow:
        log_to_mlflow(result)

    print(f"[drift_detector] Final drift flag: {result.is_drift or result.perf_is_degraded}")
    print("[drift_detector] JSON summary   : " + json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
