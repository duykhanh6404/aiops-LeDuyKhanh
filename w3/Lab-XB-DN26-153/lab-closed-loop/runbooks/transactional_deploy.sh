#!/usr/bin/env bash
set -euo pipefail

SERVICE=""
STEP=""
ROLLBACK=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="${2:-}"; shift 2 ;;
    --step) STEP="${2:-}"; shift 2 ;;
    --rollback) ROLLBACK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "--service is required" >&2
  exit 2
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN transactional_deploy service=$SERVICE step=${STEP:-all} rollback=$ROLLBACK"
  exit 0
fi

if [[ "$ROLLBACK" == "1" ]]; then
  echo "transactional rollback step=$STEP service=$SERVICE"
  exit 0
fi

if [[ "${RUNBOOK_FAIL_STEP:-}" == "$STEP" ]]; then
  echo "transactional step failed step=$STEP service=$SERVICE" >&2
  exit 20
fi

echo "transactional step executed step=$STEP service=$SERVICE"
