#!/usr/bin/env bash
set -euo pipefail

SERVICE=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "--service is required" >&2
  exit 2
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN restart_service service=$SERVICE"
  exit 0
fi

if [[ "${RUNBOOK_FORCE_FAIL:-0}" == "1" ]]; then
  echo "restart_service failed for service=$SERVICE" >&2
  exit 10
fi

echo "restart_service executed service=$SERVICE"
