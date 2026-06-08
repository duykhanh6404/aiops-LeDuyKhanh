from __future__ import annotations

import argparse
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ALERTS_FILE = Path(__file__).with_name("alerts.jsonl")
HISTORY_SIZE = 120
SEVERITY_RANK = {"warning": 1, "critical": 2}


class DetectionState:
    """Keeps rolling stream context and writes deduplicated alerts."""

    def __init__(self, alerts_file: Path = ALERTS_FILE) -> None:
        self.alerts_file = alerts_file
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
        self.alerted: dict[str, str] = {}
        self.received = 0
        self.alert_count = 0
        self.lock = threading.Lock()

    def process(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        timestamp = str(payload.get("timestamp", ""))
        metrics = payload.get("metrics")
        logs = payload.get("logs", [])

        if not isinstance(metrics, dict):
            raise ValueError("payload.metrics must be an object")
        if not isinstance(logs, list):
            raise ValueError("payload.logs must be an array")

        with self.lock:
            self.received += 1
            self.history.append({"timestamp": timestamp, "metrics": metrics, "logs": logs})
            alerts = self._detect(timestamp, metrics, logs)
            new_alerts = [alert for alert in alerts if self._should_emit(alert)]

            for alert in new_alerts:
                self._write_alert(alert)
                self.alerted[alert["type"]] = alert["severity"]
                self.alert_count += 1

            return new_alerts

    def stats(self) -> dict[str, Any]:
        with self.lock:
            return {
                "received": self.received,
                "alerts": self.alert_count,
                "alerted_types": self.alerted,
                "history_size": len(self.history),
            }

    def _detect(
        self, timestamp: str, metrics: dict[str, Any], logs: list[Any]
    ) -> list[dict[str, str]]:
        memory_usage = _number(metrics, "memory_usage_bytes")
        memory_limit = max(_number(metrics, "memory_limit_bytes"), 1.0)
        memory_util = memory_usage / memory_limit
        cpu = _number(metrics, "cpu_usage_percent")
        rps = _number(metrics, "http_requests_per_sec")
        latency = _number(metrics, "http_p99_latency_ms")
        error_rate = _number(metrics, "http_5xx_rate")
        gc_pause = _number(metrics, "jvm_gc_pause_ms_avg")
        queue_depth = _number(metrics, "queue_depth")
        timeout_rate = _number(metrics, "upstream_timeout_rate")

        log_text = " ".join(
            str(log.get("message", "")) for log in logs if isinstance(log, dict)
        ).lower()
        error_logs = sum(
            1
            for log in logs
            if isinstance(log, dict) and str(log.get("level", "")).upper() in {"ERROR", "FATAL"}
        )

        candidates: list[dict[str, str]] = []

        # Dependency faults are identified first because retries can also raise
        # traffic and queue metrics. Baseline upstream timeout is only 0-0.4%.
        if timeout_rate >= 5 and error_rate >= 2 and latency >= 180:
            severity = "critical" if timeout_rate >= 20 or error_rate >= 10 else "warning"
            candidates.append(
                _alert(
                    timestamp,
                    "dependency_timeout",
                    severity,
                    "Upstream timeouts are abnormal: "
                    f"timeout_rate={timeout_rate:.1f}%, 5xx={error_rate:.1f}%, "
                    f"p99_latency={latency:.0f}ms",
                )
            )

        # Traffic spike: normal RPS is 80-160 and queue depth is 2-10, so these
        # thresholds stay far outside normal diurnal movement.
        if rps >= 320 and queue_depth >= 40 and latency >= 180:
            severity = "critical" if rps >= 500 or queue_depth >= 120 or error_rate >= 10 else "warning"
            candidates.append(
                _alert(
                    timestamp,
                    "traffic_spike",
                    severity,
                    "Traffic and backlog spiked: "
                    f"rps={rps:.1f}, queue_depth={queue_depth:.0f}, "
                    f"p99_latency={latency:.0f}ms",
                )
            )

        # Memory leak: normal utilization is around 40%; fault injects sustained
        # memory growth plus GC pressure before the service starts failing.
        memory_log_signal = "outofmemory" in log_text or "gc pause exceeded" in log_text
        if (memory_util >= 0.70 and gc_pause >= 45) or memory_log_signal:
            severity = (
                "critical"
                if memory_util >= 0.80 or gc_pause >= 100 or error_logs > 0
                else "warning"
            )
            candidates.append(
                _alert(
                    timestamp,
                    "memory_leak",
                    severity,
                    "Memory usage is growing with GC pressure: "
                    f"utilization={memory_util * 100:.1f}%, gc_pause={gc_pause:.0f}ms, "
                    f"cpu={cpu:.1f}%",
                )
            )

        return self._choose_best_candidates(candidates)

    def _choose_best_candidates(self, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        if not candidates:
            return []

        # Prefer the most specific root cause when cascading symptoms match
        # multiple rules in the same tick.
        priority = {
            "dependency_timeout": 3,
            "traffic_spike": 2,
            "memory_leak": 1,
        }
        candidates.sort(
            key=lambda alert: (
                priority.get(alert["type"], 0),
                SEVERITY_RANK.get(alert["severity"], 0),
            ),
            reverse=True,
        )
        return [candidates[0]]

    def _should_emit(self, alert: dict[str, str]) -> bool:
        previous = self.alerted.get(alert["type"])
        if previous is None:
            return True
        return SEVERITY_RANK[alert["severity"]] > SEVERITY_RANK[previous]

    def _write_alert(self, alert: dict[str, str]) -> None:
        with self.alerts_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")


STATE = DetectionState()


class PipelineHandler(BaseHTTPRequestHandler):
    server_version = "AIOpsPipeline/1.0"

    def do_POST(self) -> None:
        if self.path != "/ingest":
            self._send_json(404, {"error": "not found"})
            return

        try:
            payload = self._read_json_body()
            alerts = STATE.process(payload)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._send_json(500, {"error": f"pipeline error: {exc}"})
            return

        self._send_json(200, {"status": "ok", "alerts_written": len(alerts)})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/stats":
            self._send_json(200, STATE.stats())
            return
        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[HTTP] {self.address_string()} - {fmt % args}")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        return payload

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _number(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key, 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _alert(timestamp: str, fault_type: str, severity: str, message: str) -> dict[str, str]:
    return {
        "timestamp": timestamp,
        "type": fault_type,
        "severity": severity,
        "message": message,
    }


def run_server(host: str, port: int) -> None:
    ALERTS_FILE.touch(exist_ok=True)
    server = ThreadingHTTPServer((host, port), PipelineHandler)
    print(f"[PIPELINE] Listening on http://{host}:{port}/ingest")
    print(f"[PIPELINE] Alerts file: {ALERTS_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[PIPELINE] Stopping.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="AIOps W1 streaming anomaly pipeline")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
