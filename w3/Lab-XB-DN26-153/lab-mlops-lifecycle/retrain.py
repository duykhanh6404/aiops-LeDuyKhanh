from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

from drift_detector import DEFAULT_PRECISION_THRESHOLD, detect_drift
from model_store import (
    FEATURES,
    append_audit,
    get_alias,
    load_bundle,
    load_frame,
    precision_recall,
    predict_anomaly,
    set_alias,
)
from pipeline import train


POST_DEPLOY_CYCLES = 24


def sliding_window(reference_df: pd.DataFrame, current_df: pd.DataFrame, reference_fraction: float = 0.50) -> pd.DataFrame:
    """Mix recent baseline with current data so v2 does not overfit the drifted week only."""
    keep_reference_rows = max(int(len(current_df) * reference_fraction), 1)
    reference_tail = reference_df.tail(keep_reference_rows)
    return pd.concat([reference_tail, current_df], ignore_index=True)


def evaluate_alias(alias: str, labeled_path: str) -> tuple[float, float]:
    df = load_frame(labeled_path)
    if "anomaly_label" not in df.columns:
        raise ValueError(f"{labeled_path} must contain anomaly_label")
    bundle = load_bundle(alias)
    y_pred, _ = predict_anomaly(bundle, df)
    return precision_recall(df["anomaly_label"].to_numpy(), y_pred)


def write_temp_training_csv(df: pd.DataFrame, name: str = "sliding_window_train.csv") -> str:
    out = Path(__file__).resolve().parent / "outputs" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return str(out)


def call_reload(serve_url: str) -> None:
    try:
        resp = requests.post(f"{serve_url.rstrip('/')}/reload", timeout=5)
        print(f"[retrain] serve reload    : HTTP {resp.status_code}")
    except Exception as exc:
        print(f"[retrain] serve reload    : skipped ({type(exc).__name__}: {exc})")


def prompt_approval(auto_approve: bool) -> bool:
    if auto_approve:
        print("[retrain] Approval gate  : auto-approved for test run")
        return True
    answer = input("[retrain] Promote staging to production? [y/N] ").strip().lower()
    return answer == "y"


def monitor_and_maybe_rollback(
    post_deploy_eval: str,
    v1_version: str,
    v2_version: str,
    serve_url: str,
    threshold: float = DEFAULT_PRECISION_THRESHOLD,
    cycles: int = POST_DEPLOY_CYCLES,
) -> bool:
    rolled_back = False
    for cycle in range(1, cycles + 1):
        precision, recall = evaluate_alias("production", post_deploy_eval)
        print(f"post_deploy_monitor Cycle {cycle:02d}/{cycles} precision: {precision:.4f}  recall: {recall:.4f}")
        append_audit(
            "post_deploy_cycle",
            {"cycle": cycle, "version": v2_version, "precision": precision, "recall": recall},
        )
        if precision < threshold:
            set_alias("archived", v2_version)
            set_alias("production", v1_version)
            append_audit(
                "auto_rollback_v2_to_v1",
                {
                    "demoted_version": v2_version,
                    "restored_version": v1_version,
                    "trigger_precision": precision,
                    "cycle": cycle,
                },
            )
            call_reload(serve_url)
            print(f"Rollback complete. v{v1_version} restored to @production. v{v2_version} -> @archived")
            rolled_back = True
            break
    if not rolled_back:
        append_audit("post_deploy_stable", {"version": v2_version, "cycles": cycles})
        print(f"[retrain] v{v2_version} passed {cycles} post-deploy cycles")
    return rolled_back


def ensure_v1(reference_path: str) -> str:
    production = get_alias("production")
    if production:
        return production
    print("[retrain] No @production model found. Training v1 from reference first.")
    return train(reference_path, alias="production")


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect drift, retrain, stage, approve, promote, and rollback")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--threshold", type=float, default=0.15)
    parser.add_argument("--holdout")
    parser.add_argument("--post-deploy-eval")
    parser.add_argument("--serve-url", default="http://localhost:8000")
    parser.add_argument("--auto-approve", "--yes", action="store_true", dest="auto_approve")
    parser.add_argument("--contamination", type=float, default=0.01)
    parser.add_argument("--n-estimators", type=int, default=150)
    args = parser.parse_args()

    reference_df = load_frame(args.reference)
    current_df = load_frame(args.current)
    v1_version = ensure_v1(args.reference)

    print(f"[retrain] Reference rows : {len(reference_df)}")
    print(f"[retrain] Current rows   : {len(current_df)}")
    drift = detect_drift(reference_df, current_df, threshold=args.threshold, report_label="retrain")
    print(f"[retrain] Drift score    : {drift.score:.4f}")
    print(f"[retrain] Drift detected : {drift.is_drift}")
    print(f"[retrain] Drift report   : {drift.report_path}")

    if not drift.is_drift:
        append_audit("retrain_skipped_no_drift", {"score": drift.score, "threshold": args.threshold})
        print("[retrain] No retrain needed.")
        return

    train_df = sliding_window(reference_df, current_df)
    train_path = write_temp_training_csv(train_df)
    print(f"[retrain] Sliding window : {len(train_df)} rows ({len(reference_df.tail(max(int(len(current_df) * 0.50), 1)))} old + {len(current_df)} current)")
    v2_version = train(
        train_path,
        alias="staging",
        contamination=args.contamination,
        n_estimators=args.n_estimators,
    )
    print(f"[retrain] Registered     : v{v2_version} -> @staging")

    if args.holdout:
        v1_precision, v1_recall = evaluate_alias("production", args.holdout)
        v2_precision, v2_recall = evaluate_alias("staging", args.holdout)
        print(f"[retrain] Holdout v1 precision: {v1_precision:.4f}  recall: {v1_recall:.4f}")
        print(f"Holdout validation — v2 precision: {v2_precision:.4f}  recall: {v2_recall:.4f}")
        append_audit(
            "holdout_validation",
            {
                "v1_version": v1_version,
                "v2_version": v2_version,
                "v1_precision": v1_precision,
                "v2_precision": v2_precision,
                "v2_recall": v2_recall,
            },
        )
        if v2_precision < v1_precision:
            print("[retrain] Staging rejected: v2 precision is lower than v1 on holdout.")
            append_audit("staging_rejected_holdout", {"v1_version": v1_version, "v2_version": v2_version})
            sys.exit(2)

    if not prompt_approval(args.auto_approve):
        append_audit("promotion_rejected_by_human", {"v1_version": v1_version, "v2_version": v2_version})
        print("[retrain] Promotion rejected. v1 remains @production.")
        return

    set_alias("production", v2_version)
    append_audit("promoted_staging_to_production", {"old_version": v1_version, "new_version": v2_version})
    print(f"[retrain] Promoted       : v{v2_version} -> @production")
    call_reload(args.serve_url)

    if args.post_deploy_eval:
        monitor_and_maybe_rollback(
            post_deploy_eval=args.post_deploy_eval,
            v1_version=v1_version,
            v2_version=v2_version,
            serve_url=args.serve_url,
            threshold=DEFAULT_PRECISION_THRESHOLD,
        )


if __name__ == "__main__":
    main()
