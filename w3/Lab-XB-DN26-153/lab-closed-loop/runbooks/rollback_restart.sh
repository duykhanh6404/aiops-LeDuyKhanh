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
  echo "DRY_RUN rollback service=$SERVICE"
  exit 0
fi

echo "rollback executed service=$SERVICE"
