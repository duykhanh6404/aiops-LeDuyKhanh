# AIOps Mini-Platform Spec - Khanh

## 1. Platform overview

The platform monitors a small ecommerce-style stack with frontend, API, and database layers, plus a W1/W2-style AIOps pipeline for detection, correlation, and root-cause analysis. Its users are on-call engineers who need faster MTTD, better RCA evidence, and lower alert noise. The scope is reliability operations and incident learning; the platform is not a replacement for code review, load testing, or human incident command.

## 2. SLO definition (from W3-D1)

Three service SLOs are defined in `w3/d1/slo_spec.yaml`.

| Service | SLI | Target | Monthly events | Failure budget | Downtime equivalent |
|---|---|---:|---:|---:|---:|
| frontend | RUM events without network error / all RUM events | 99.0% | 5,184,000 | 51,840 | 432 min |
| api | availability excluding client-caused non-429 4xx | 99.9% | 20,737,800 | 20,738 | 43 min |
| db | successful queries under 100 ms / sampled queries | 99.0% | 1,726,380 | 17,264 | 432 min |

Burn-rate alerting uses MWMBR tiers. The D1 validation report passed with noise reduction 86.4%, MTTD delta 0 seconds, and false negatives 0.

## 3. Detection + Correlation + RCA stack (from W1+W2)

**Detector:** The detector consumes metric/log-derived time series and emits anomaly events for latency, error rate, CPU saturation, and synthetic probe failures. W3-D3 adds a required pre-deploy guardrail class for risky config and regex changes.

**Correlator:** Alerts are grouped by time window, service, dependency graph, and rollout event. Temporal proximity alone is not enough because independent faults can happen in the same window.

**RCA:** RCA ranks candidate roots using topology distance, first-drift time, alert evidence, deploy metadata, and synthetic probe impact. ADR-001 rejects count-only RCA and LLM-only RCA as primary decision engines.

## 4. Reliability validation (from W3-D2)

Chaos validation is represented by the W3-D2 scoreboard:

- Total experiments: 10
- Detected: 8/10
- RCA correct: 7/8
- False alarms in baseline windows: 1
- Precision: 0.89
- Recall: 0.80
- MTTD p50: 40.0s, p95: 62s

Top D2 gaps were auth clock-skew detector coverage, log-collector meta-monitoring, and DNS latency RCA. The steady-state signal is an external synthetic probe plus internal service metrics.

## 5. Operational pattern (from W3-D3)

The reproduced outage is Cloudflare WAF Regex 2019, failure mode `catastrophic_backtracking`. In local reproduction, WAF rule activation raised regex p99 from 0.010 ms to 913.2 ms, CPU proxy reached 100.0%, and the latency alert fired after 36 seconds. RCA selected `waf-api/regex_middleware`, but two gaps remain: no static ReDoS guardrail and missing rollout/canary metadata in RCA evidence. Postmortems use the blameless template in `postmortem.md`; ADRs use Nygard format, starting with ADR-001.

## 6. Cost model (from W3-D3)

`cost_model.py` implements `is_worth_it(...)` with ROI and payback months.

For the current mini-platform scenario:

```text
num_services: 12
monthly_value: 75000.0
monthly_cost: 18000
roi: 4.17
payback_months: 0.24
verdict: worth_it
```

The break-even point with $18,000 monthly cost, $50,000/hour downtime cost, and 40% MTTR reduction is 0.9 incident-hours avoided per month.

## 7. Open risks

| Risk | Severity | Mitigation |
|---|---|---|
| Docker/live W1+W2 pipeline runtime is not present in this workspace. | High | Re-run D3 against the real composed stack once available. |
| Static ReDoS guardrail is not implemented yet. | High | Add regex safety test in CI/CD before WAF rule rollout. |
| RCA lacks rollout/canary state. | Medium | Add deploy events and rollout scope to evidence objects. |
| DNS and shared dependency topology can be incomplete. | Medium | Maintain dependency graph and validate it in chaos runs. |
| Meta-monitoring gaps can blind the pipeline. | High | Alert on telemetry freshness, ingestion lag, and collector storage. |
