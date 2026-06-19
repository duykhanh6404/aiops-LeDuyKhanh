#!/usr/bin/env python3
"""Run the six lab acceptance scenarios for closed_loop.py."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from closed_loop import Alert, ClosedLoopOrchestrator, JsonLogger, RunbookExecutor


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"


class FakePrometheus:
    def __init__(self, values_by_service: dict[str, list[float]]):
        self.values_by_service = {k: list(v) for k, v in values_by_service.items()}

    def query_health_score(self, service: str) -> float:
        values = self.values_by_service.setdefault(service, [0.0])
        if len(values) > 1:
            return values.pop(0)
        return values[0]


def make_orchestrator(name: str, prom_values: dict[str, list[float]] | None = None,
                      decision_mode: str = "rule") -> ClosedLoopOrchestrator:
    logger = JsonLogger(LOG_DIR / f"{name}.jsonl")
    executor = RunbookExecutor(ROOT, emulate_when_bash_unavailable=True)
    orch = ClosedLoopOrchestrator(
        ROOT / "config.yaml",
        logger=logger,
        prom_client=FakePrometheus(prom_values or {}),
        executor=executor,
        decision_mode=decision_mode,
    )
    # Acceptance tests should be fast while preserving 3 verification polls.
    orch.config["verify"]["window_seconds"] = 0
    return orch


def summarize(name: str, orch: ClosedLoopOrchestrator, passed: bool, note: str) -> dict[str, Any]:
    return {
        "scenario": name,
        "passed": passed,
        "note": note,
        "events": [e["event_type"] for e in orch.logger.events],
        "log": str((LOG_DIR / f"{name}.jsonl").relative_to(ROOT)),
    }


def scenario_1_success() -> dict[str, Any]:
    name = "scenario_1_success"
    orch = make_orchestrator(name, {"api": [0.2, 0.8, 1.0]})
    result = orch.process_alert(Alert("HighLatency", "api"))
    passed = result.get("event_type") == "ACTION_SUCCESS" and any(e["event_type"] == "VERIFY_PASS" for e in orch.logger.events)
    return summarize(name, orch, passed, "Latency alert remediated by restart_service and verified stable.")


def scenario_2_rollback() -> dict[str, Any]:
    name = "scenario_2_rollback"
    orch = make_orchestrator(name, {"checkout": [0.0, 0.0, 0.0]})
    result = orch.process_alert(Alert("InstanceDown", "checkout"), env={"RUNBOOK_FORCE_FAIL": "1"})
    has_rollback = any(e["event_type"] == "ROLLBACK_EXECUTED" for e in orch.logger.events)
    passed = result.get("reason") == "act_failed" and has_rollback
    return summarize(name, orch, passed, "Restart failed and rollback runbook executed.")


def scenario_3_circuit_breaker() -> dict[str, Any]:
    name = "scenario_3_circuit_breaker"
    orch = make_orchestrator(name, {"inventory": [0.0, 0.0, 0.0]})
    # This scenario isolates circuit-breaker behavior; blast-radius is tested
    # separately, so allow enough restart attempts to reach 3 failures.
    orch.blast.max_restarts_per_service_per_hour = 10
    for _ in range(3):
        orch.process_alert(Alert("InstanceDown", "inventory"), env={"RUNBOOK_FORCE_FAIL": "1"})
    orch.process_alert(Alert("InstanceDown", "inventory"))
    circuit_events = [e for e in orch.logger.events if e["event_type"] == "CIRCUIT_OPEN"]
    skipped_after_open = any(e["event_type"] == "CIRCUIT_OPEN" and e["result"] == "skipped" for e in orch.logger.events)
    passed = len(circuit_events) >= 2 and skipped_after_open
    return summarize(name, orch, passed, "Three consecutive failures opened the circuit and a later alert was skipped.")


def scenario_4_transactional_rollback() -> dict[str, Any]:
    name = "scenario_4_transactional_rollback"
    orch = make_orchestrator(name, {"frontend": [0.0, 0.0, 0.0]})
    result = orch.process_alert(Alert("BadDeploy", "frontend"), env={"RUNBOOK_FAIL_STEP": "cleanup"})
    rollback_steps = [
        e.get("step")
        for e in orch.logger.events
        if e["event_type"] == "TRANSACTIONAL_ROLLBACK_STEP"
    ]
    passed = (
        result.get("reason") == "transaction_step_failed:cleanup"
        and rollback_steps == ["switch", "prepare"]
    )
    return summarize(name, orch, passed, "Step C failed; rollback ran step B then step A.")


def scenario_5_concurrent_alert_race() -> dict[str, Any]:
    name = "scenario_5_concurrent_alert_race"
    orch = make_orchestrator(name, {"api": [1.0, 1.0, 1.0], "payment": [0.3, 0.7, 1.0]})
    api_lock = orch.service_locks["api"]
    api_lock.acquire()
    try:
        busy_result = orch.process_alert(Alert("HighLatency", "api"))
    finally:
        api_lock.release()
    payment_result = orch.process_alert(Alert("HighLatency", "payment"))
    busy_logged = any(e["event_type"] == "SERVICE_LOCK_BUSY" for e in orch.logger.events)
    payment_done = payment_result.get("result") != "failed"
    passed = busy_result["event_type"] == "SERVICE_LOCK_BUSY" and busy_logged and payment_done
    return summarize(name, orch, passed, "Same-service alert was skipped as busy; different service proceeded.")


def scenario_6_llm_hallucination_defense() -> dict[str, Any]:
    name = "scenario_6_llm_hallucination_defense"
    orch = make_orchestrator(name, {"api": [1.0, 1.0, 1.0]}, decision_mode="llm")
    result = orch.process_alert(Alert("HighLatency", "api"), llm_suggestion="delete_database")
    act_events = [e for e in orch.logger.events if e["event_type"] == "RUNBOOK_EXEC"]
    passed = result["event_type"] == "DECISION_VALIDATION_FAILED" and result["result"] == "rejected" and not act_events
    return summarize(name, orch, passed, "Invalid LLM runbook suggestion was rejected before dry-run/action.")


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for old in LOG_DIR.glob("scenario_*.jsonl"):
        old.unlink()
    scenarios = [
        scenario_1_success,
        scenario_2_rollback,
        scenario_3_circuit_breaker,
        scenario_4_transactional_rollback,
        scenario_5_concurrent_alert_race,
        scenario_6_llm_hallucination_defense,
    ]
    results = [fn() for fn in scenarios]
    summary_path = ROOT / "scenario_results.json"
    summary_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    if not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
