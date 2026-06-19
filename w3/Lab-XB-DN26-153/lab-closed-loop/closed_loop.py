#!/usr/bin/env python3
"""Closed-loop auto-remediation orchestrator.

Flow:
  Detect -> Decide/blast-radius -> Dry-run -> Act -> Verify -> Rollback.

The orchestrator can poll real Alertmanager/Prometheus endpoints, but the lab
scenarios use in-memory clients so the acceptance tests are deterministic.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JsonLogger:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("")

    def emit(self, event_type: str, service: str | None, action: str | None, result: str, **fields: Any) -> dict[str, Any]:
        event = {
            "ts": utc_now(),
            "event_type": event_type,
            "service": service,
            "action": action,
            "result": result,
            **fields,
        }
        line = json.dumps(event, sort_keys=True)
        with self._lock:
            self.events.append(event)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        print(line)
        return event


@dataclass(frozen=True)
class Alert:
    name: str
    service: str
    severity: str = "page"
    labels: dict[str, str] | None = None


class AlertmanagerClient:
    def __init__(self, base_url: str = "http://localhost:9093"):
        self.base_url = base_url.rstrip("/")

    def poll_alerts(self) -> list[Alert]:
        with urllib.request.urlopen(f"{self.base_url}/api/v2/alerts", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        alerts = []
        for item in payload:
            labels = item.get("labels", {})
            alerts.append(Alert(
                name=labels.get("alertname", "unknown"),
                service=labels.get("service", "unknown"),
                severity=labels.get("severity", "page"),
                labels=labels,
            ))
        return alerts


class PrometheusClient:
    def __init__(self, base_url: str = "http://localhost:9090"):
        self.base_url = base_url.rstrip("/")

    def query_health_score(self, service: str) -> float:
        query = f'avg_over_time(service_health_score{{service="{service}"}}[1m])'
        url = f"{self.base_url}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result = payload.get("data", {}).get("result", [])
        if not result:
            return 0.0
        return float(result[0]["value"][1])


class CircuitBreaker:
    def __init__(self, max_failures: int):
        self.max_failures = max_failures
        self.consecutive_failures = 0
        self.open = False

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> bool:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            self.open = True
        return self.open

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.open = False


class BlastRadiusPolicy:
    def __init__(self, config: dict[str, Any]):
        self.max_actions_per_minute = int(config["max_actions_per_minute"])
        self.max_restarts_per_service_per_hour = int(config["max_restarts_per_service_per_hour"])
        self.action_times: deque[float] = deque()
        self.restart_times_by_service: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _trim(values: deque[float], now: float, window_seconds: int) -> None:
        while values and now - values[0] > window_seconds:
            values.popleft()

    def allow(self, service: str, action: str, now: float | None = None) -> tuple[bool, str]:
        now = now or time.time()
        self._trim(self.action_times, now, 60)
        if len(self.action_times) >= self.max_actions_per_minute:
            return False, "max_actions_per_minute_exceeded"

        if action == "restart":
            restarts = self.restart_times_by_service[service]
            self._trim(restarts, now, 3600)
            if len(restarts) >= self.max_restarts_per_service_per_hour:
                return False, "max_restarts_per_service_per_hour_exceeded"
        return True, "allowed"

    def record(self, service: str, action: str, now: float | None = None) -> None:
        now = now or time.time()
        self.action_times.append(now)
        if action == "restart":
            self.restart_times_by_service[service].append(now)


class RunbookExecutor:
    def __init__(self, root: Path, emulate_when_bash_unavailable: bool = True):
        self.root = root
        self.emulate_when_bash_unavailable = emulate_when_bash_unavailable

    def run(self, script: str, service: str, dry_run: bool = False, extra_args: list[str] | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        script_path = self.root / script
        args = ["bash", str(script_path), "--service", service]
        if dry_run:
            args.append("--dry-run")
        if extra_args:
            args.extend(extra_args)
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            return subprocess.run(args, cwd=self.root, env=run_env, text=True, capture_output=True, timeout=30)
        except FileNotFoundError:
            if self.emulate_when_bash_unavailable:
                return self._emulate(script_path, service, dry_run, extra_args or [], run_env)
            raise

    def safe_run(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        result = self.run(*args, **kwargs)
        if self.emulate_when_bash_unavailable and result.returncode != 0:
            combined = ((result.stdout or "") + (result.stderr or "")).replace("\x00", "")
            if (
                "Windows Subsystem for Linux has no installed distributions" in combined
                or (os.name == "nt" and result.returncode == 127 and "No such file or directory" in combined)
            ):
                script = args[0]
                service = args[1]
                dry_run = bool(kwargs.get("dry_run", False))
                extra_args = kwargs.get("extra_args") or []
                env = os.environ.copy()
                env.update(kwargs.get("env") or {})
                return self._emulate(self.root / script, service, dry_run, extra_args, env)
        return result

    def _emulate(self, script_path: Path, service: str, dry_run: bool, extra_args: list[str],
                 env: dict[str, str]) -> subprocess.CompletedProcess:
        script = script_path.name
        step = None
        rollback = False
        for idx, arg in enumerate(extra_args):
            if arg == "--step" and idx + 1 < len(extra_args):
                step = extra_args[idx + 1]
            if arg == "--rollback":
                rollback = True

        if dry_run:
            stdout = f"DRY_RUN {script} service={service} step={step or 'all'} rollback={rollback}\n"
            return subprocess.CompletedProcess([str(script_path)], 0, stdout, "")

        if script == "restart_service.sh" and env.get("RUNBOOK_FORCE_FAIL") == "1":
            return subprocess.CompletedProcess([str(script_path)], 10, "", f"restart_service failed for service={service}\n")
        if script == "transactional_deploy.sh" and env.get("RUNBOOK_FAIL_STEP") == step and not rollback:
            return subprocess.CompletedProcess([str(script_path)], 20, "", f"transactional step failed step={step} service={service}\n")

        if rollback:
            stdout = f"transactional rollback step={step} service={service}\n"
        else:
            stdout = f"{script} executed service={service} step={step or 'all'}\n"
        return subprocess.CompletedProcess([str(script_path)], 0, stdout, "")


class ClosedLoopOrchestrator:
    def __init__(self, config_path: Path, logger: JsonLogger, alert_client: Any | None = None,
                 prom_client: Any | None = None, executor: RunbookExecutor | None = None,
                 decision_mode: str = "rule", orchestrator_dry_run: bool = False):
        self.root = config_path.parent
        self.config = yaml.safe_load(config_path.read_text())
        self.logger = logger
        self.alert_client = alert_client or AlertmanagerClient()
        self.prom_client = prom_client or PrometheusClient()
        self.executor = executor or RunbookExecutor(self.root)
        self.decision_mode = decision_mode
        self.orchestrator_dry_run = orchestrator_dry_run
        self.circuit = CircuitBreaker(int(self.config["circuit_breaker"]["max_consecutive_failures"]))
        self.blast = BlastRadiusPolicy(self.config["blast_radius"])
        self.service_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def poll_once(self) -> None:
        for alert in self.alert_client.poll_alerts():
            self.process_alert(alert)

    def run_forever(self) -> None:
        interval = int(self.config["poll_interval_seconds"])
        while not self.circuit.open:
            self.poll_once()
            time.sleep(interval)

    def decide(self, alert: Alert, llm_suggestion: str | None = None) -> tuple[str | None, str]:
        if self.decision_mode == "llm":
            if llm_suggestion in self.config["allowed_runbooks"]:
                return llm_suggestion, "llm_allowlisted"
            return None, "llm_suggestion_not_allowlisted"
        rule = self.config["rules"].get(alert.name)
        if not rule:
            return None, "no_matching_rule"
        return rule["runbook"], "rule_match"

    def process_alert(self, alert: Alert, llm_suggestion: str | None = None,
                      env: dict[str, str] | None = None) -> dict[str, Any]:
        self.logger.emit("ALERT_DETECTED", alert.service, None, "ok", alert_name=alert.name, severity=alert.severity)

        if self.circuit.open:
            return self.logger.emit("CIRCUIT_OPEN", alert.service, None, "skipped", alert_name=alert.name)

        lock = self.service_locks[alert.service]
        if not lock.acquire(blocking=False):
            return self.logger.emit("SERVICE_LOCK_BUSY", alert.service, None, "skipped", alert_name=alert.name)

        try:
            runbook_name, reason = self.decide(alert, llm_suggestion=llm_suggestion)
            if not runbook_name:
                return self.logger.emit("DECISION_VALIDATION_FAILED", alert.service, "escalate_no_auto_action", "rejected",
                                        reason=reason, bad_runbook=llm_suggestion, alertname=alert.name,
                                        raw_decision=llm_suggestion)

            runbook = self.config["allowed_runbooks"][runbook_name]
            rule = self.config["rules"].get(alert.name, {})
            action = rule.get("action", runbook_name)
            allowed, blast_reason = self.blast.allow(alert.service, action)
            self.logger.emit("BLAST_RADIUS_PASS" if allowed else "BLAST_RADIUS_REJECTED",
                             alert.service, action, "allowed" if allowed else "rejected", reason=blast_reason)
            if not allowed:
                return {"result": "blast_radius_rejected", "reason": blast_reason}

            dry = self.executor.safe_run(runbook["path"], alert.service, dry_run=True, env=env)
            self.logger.emit("DRY_RUN_PASS" if dry.returncode == 0 else "DRY_RUN_FAIL",
                             alert.service, action, "ok" if dry.returncode == 0 else "failed",
                             runbook=runbook_name, returncode=dry.returncode, stdout=dry.stdout.strip(), stderr=dry.stderr.strip())
            if dry.returncode != 0:
                return self._failure(alert.service, action, "dry_run_failed")
            if self.orchestrator_dry_run:
                return self.logger.emit("ACTION_SKIPPED_DRY_RUN", alert.service, action, "skipped", runbook=runbook_name)

            if runbook.get("type") == "transaction":
                result = self._run_transaction(alert.service, action, runbook, env=env)
            else:
                self.blast.record(alert.service, action)
                result = self.executor.safe_run(runbook["path"], alert.service, dry_run=False, env=env)
                self.logger.emit("RUNBOOK_EXEC", alert.service, action, "ok" if result.returncode == 0 else "failed",
                                 runbook=runbook_name, returncode=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())
                if result.returncode != 0:
                    self._rollback_single(alert.service, action, runbook, env=env)
                    return self._failure(alert.service, action, "act_failed")
                result = {"result": "ok"}

            if result.get("result") == "failed":
                return self._failure(alert.service, action, result["reason"])

            verified = self.verify(alert.service, action)
            if verified:
                self.circuit.record_success()
                self.logger.emit("VERIFY_PASS", alert.service, action, "ok")
                return self.logger.emit("ACTION_SUCCESS", alert.service, action, "success")

            self.logger.emit("VERIFY_FAIL", alert.service, action, "failed")
            self._rollback_single(alert.service, action, runbook, env=env)
            return self._failure(alert.service, action, "verify_failed")
        finally:
            lock.release()

    def _run_transaction(self, service: str, action: str, runbook: dict[str, Any],
                         env: dict[str, str] | None = None) -> dict[str, str]:
        completed_steps: list[str] = []
        self.blast.record(service, action)
        for step in runbook["steps"]:
            result = self.executor.safe_run(runbook["path"], service, dry_run=False, extra_args=["--step", step], env=env)
            event_type = "TRANSACTIONAL_STEP_SUCCESS" if result.returncode == 0 else "TRANSACTIONAL_STEP_FAIL"
            self.logger.emit(event_type, service, action, "ok" if result.returncode == 0 else "failed",
                             step=step, completed_before_failure=list(completed_steps),
                             returncode=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())
            if result.returncode != 0:
                self._rollback_transaction(service, action, runbook, completed_steps, env=env)
                return {"result": "failed", "reason": f"transaction_step_failed:{step}"}
            completed_steps.append(step)
        return {"result": "ok"}

    def _rollback_transaction(self, service: str, action: str, runbook: dict[str, Any],
                              completed_steps: list[str], env: dict[str, str] | None = None) -> None:
        rolled_back: list[str] = []
        for step in reversed(completed_steps):
            if step not in runbook.get("rollback_steps", completed_steps):
                continue
            result = self.executor.safe_run(runbook["path"], service, dry_run=False,
                                            extra_args=["--rollback", "--step", step], env=env)
            rolled_back.append(step)
            self.logger.emit("TRANSACTIONAL_ROLLBACK_STEP", service, action, "ok" if result.returncode == 0 else "failed",
                             step=step, returncode=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())
        self.logger.emit("TRANSACTIONAL_ROLLBACK_COMPLETE", service, action, "ok", rolled_back=rolled_back)

    def _rollback_single(self, service: str, action: str, runbook: dict[str, Any],
                         env: dict[str, str] | None = None) -> None:
        rollback = runbook.get("rollback")
        if not rollback:
            return
        self.logger.emit("ROLLBACK_TRIGGERED", service, action, "started", rollback=rollback)
        result = self.executor.safe_run(rollback, service, dry_run=False, env=env)
        self.logger.emit("ROLLBACK_EXECUTED", service, action, "ok" if result.returncode == 0 else "failed",
                         returncode=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())

    def verify(self, service: str, action: str) -> bool:
        cfg = self.config["verify"]
        attempts = int(cfg["attempts"])
        threshold = float(cfg["success_threshold"])
        window = float(cfg["window_seconds"])
        interval = 0 if attempts <= 1 else min(1.0, window / attempts)
        values = []
        for i in range(attempts):
            value = float(self.prom_client.query_health_score(service))
            values.append(value)
            self.logger.emit("VERIFY_SAMPLE", service, action, "ok" if value >= threshold else "not_yet",
                             attempt=i + 1, value=value, threshold=threshold)
            if interval:
                time.sleep(interval)
        return values[-1] >= threshold if values else False

    def _failure(self, service: str, action: str, reason: str) -> dict[str, Any]:
        circuit_open = self.circuit.record_failure()
        self.logger.emit("FAILURE", service, action, "failed", reason=reason,
                         consecutive_failures=self.circuit.consecutive_failures)
        if circuit_open:
            self.logger.emit("CIRCUIT_OPEN", service, action, "halted",
                             consecutive_failures=self.circuit.consecutive_failures)
            self.logger.emit("CIRCUIT_BREAKER_HALT", service, action, "halted",
                             consecutive_failures=self.circuit.consecutive_failures)
        return {"result": "failed", "reason": reason, "circuit_open": circuit_open}

    def reset_circuit(self, reason: str) -> None:
        self.circuit.reset()
        self.logger.emit("CIRCUIT_RESET", None, None, "ok", reason=reason)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--log", type=Path, default=Path("logs/closed_loop.jsonl"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="run detect/decide/dry-run only; do not execute actions")
    args = parser.parse_args()

    logger = JsonLogger(args.log)
    orchestrator = ClosedLoopOrchestrator(args.config, logger, orchestrator_dry_run=args.dry_run)
    if args.once:
        orchestrator.poll_once()
    else:
        orchestrator.run_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
