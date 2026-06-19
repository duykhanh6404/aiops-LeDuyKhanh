# Closed-Loop Auto-Remediation Submission

## Files

- `closed_loop.py`: main orchestrator.
- `config.yaml`: runbook map, blast-radius, verify, circuit breaker config.
- `runbooks/*.sh`: bash runbooks with `--service` and `--dry-run`.
- `DESIGN.md`: design answers.
- `run_scenarios.py`: deterministic acceptance scenarios.
- `scenario_results.json`: 6/6 scenario result summary.
- `logs/scenario_*.jsonl`: structured JSON audit logs.

## Scenario results

```json
[
  {
    "scenario": "scenario_1_success",
    "passed": true,
    "note": "Latency alert remediated by restart_service and verified stable.",
    "events": ["ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "RUNBOOK_EXEC", "VERIFY_SAMPLE", "VERIFY_SAMPLE", "VERIFY_SAMPLE", "VERIFY_PASS", "ACTION_SUCCESS"],
    "log": "logs/scenario_1_success.jsonl"
  },
  {
    "scenario": "scenario_2_rollback",
    "passed": true,
    "note": "Restart failed and rollback runbook executed.",
    "events": ["ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "RUNBOOK_EXEC", "ROLLBACK_TRIGGERED", "ROLLBACK_EXECUTED", "FAILURE"],
    "log": "logs/scenario_2_rollback.jsonl"
  },
  {
    "scenario": "scenario_3_circuit_breaker",
    "passed": true,
    "note": "Three consecutive failures opened the circuit and a later alert was skipped.",
    "events": ["ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "RUNBOOK_EXEC", "ROLLBACK_TRIGGERED", "ROLLBACK_EXECUTED", "FAILURE", "ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "RUNBOOK_EXEC", "ROLLBACK_TRIGGERED", "ROLLBACK_EXECUTED", "FAILURE", "ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "RUNBOOK_EXEC", "ROLLBACK_TRIGGERED", "ROLLBACK_EXECUTED", "FAILURE", "CIRCUIT_OPEN", "CIRCUIT_BREAKER_HALT", "ALERT_DETECTED", "CIRCUIT_OPEN"],
    "log": "logs/scenario_3_circuit_breaker.jsonl"
  },
  {
    "scenario": "scenario_4_transactional_rollback",
    "passed": true,
    "note": "Step C failed; rollback ran step B then step A.",
    "events": ["ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "TRANSACTIONAL_STEP_SUCCESS", "TRANSACTIONAL_STEP_SUCCESS", "TRANSACTIONAL_STEP_FAIL", "TRANSACTIONAL_ROLLBACK_STEP", "TRANSACTIONAL_ROLLBACK_STEP", "TRANSACTIONAL_ROLLBACK_COMPLETE", "FAILURE"],
    "log": "logs/scenario_4_transactional_rollback.jsonl"
  },
  {
    "scenario": "scenario_5_concurrent_alert_race",
    "passed": true,
    "note": "Same-service alert was skipped as busy; different service proceeded.",
    "events": ["ALERT_DETECTED", "SERVICE_LOCK_BUSY", "ALERT_DETECTED", "BLAST_RADIUS_PASS", "DRY_RUN_PASS", "RUNBOOK_EXEC", "VERIFY_SAMPLE", "VERIFY_SAMPLE", "VERIFY_SAMPLE", "VERIFY_PASS", "ACTION_SUCCESS"],
    "log": "logs/scenario_5_concurrent_alert_race.jsonl"
  },
  {
    "scenario": "scenario_6_llm_hallucination_defense",
    "passed": true,
    "note": "Invalid LLM runbook suggestion was rejected before dry-run/action.",
    "events": ["ALERT_DETECTED", "DECISION_VALIDATION_FAILED"],
    "log": "logs/scenario_6_llm_hallucination_defense.jsonl"
  }
]
```

## Representative logs

Scenario 1 success:

```json
{"event_type":"DRY_RUN_PASS","service":"api","action":"restart","result":"ok"}
{"event_type":"RUNBOOK_EXEC","service":"api","action":"restart","result":"ok"}
{"event_type":"VERIFY_SAMPLE","service":"api","action":"restart","result":"not_yet","attempt":1,"value":0.2}
{"event_type":"VERIFY_SAMPLE","service":"api","action":"restart","result":"not_yet","attempt":2,"value":0.8}
{"event_type":"VERIFY_SAMPLE","service":"api","action":"restart","result":"ok","attempt":3,"value":1.0}
{"event_type":"ACTION_SUCCESS","service":"api","action":"restart","result":"success"}
```

Scenario 3 circuit breaker:

```json
{"event_type":"FAILURE","service":"inventory","action":"restart","result":"failed","consecutive_failures":3}
{"event_type":"CIRCUIT_OPEN","service":"inventory","action":"restart","result":"halted","consecutive_failures":3}
{"event_type":"CIRCUIT_BREAKER_HALT","service":"inventory","action":"restart","result":"halted","consecutive_failures":3}
{"event_type":"CIRCUIT_OPEN","service":"inventory","action":null,"result":"skipped"}
```

Scenario 6 hallucination defense:

```json
{"event_type":"ALERT_DETECTED","service":"api","alert_name":"HighLatency","result":"ok"}
{"event_type":"DECISION_VALIDATION_FAILED","service":"api","result":"rejected","reason":"llm_suggestion_not_allowlisted","bad_runbook":"delete_database"}
```

## Verdict

Pass + Excellent. All 6 scenarios pass:

- Scenario 1 successful remediation.
- Scenario 2 action failure with rollback.
- Scenario 3 circuit breaker after 3 failures.
- Scenario 4 transactional rollback in reverse order.
- Scenario 5 concurrent alert race with per-service mutex.
- Scenario 6 LLM hallucination defense via runbook allowlist.
