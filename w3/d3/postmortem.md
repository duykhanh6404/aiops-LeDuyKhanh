# Postmortem: Cloudflare WAF Regex Reproduction (2019-07-02 pattern)

**Status:** complete  
**Date:** 2026-06-18  
**Authors:** Khanh  
**Severity:** SEV1  
**Duration:** 2m 28s in local reproduction (2026-06-18T02:10:00Z -> 2026-06-18T02:12:28Z)

## Summary

A WAF regex rule was promoted to the hot request path and caused catastrophic-backtracking-style CPU work. Requests still looked like successful HTTP paths from an error-rate perspective, but latency jumped from a sub-millisecond baseline to a p99 of 913.2 ms and the CPU proxy reached 100%. The reproduction recovered after disabling the rule and restarting the worker process.

## Impact

- **Users affected:** 100% of synthetic requests routed through the WAF middleware during the active window.
- **Services affected:** `waf-api`, synthetic checkout/user path through edge middleware.
- **Revenue/SLA impact:** Modeled as a latency SLO breach; no direct revenue loss in local reproduction.
- **SLO budget consumed:** Local window exceeded the 500 ms request-latency threshold for the active WAF path.
- **External communication:** Not applicable for local lab; a real incident would require status-page update once user-visible latency is confirmed.
- **Duration:** 2026-06-18T02:10:00Z -> 2026-06-18T02:12:28Z, total 148 seconds.

## Timeline (UTC)

| UTC | Event |
|---|---|
| 2026-06-18 02:10:00 | Baseline steady-state confirmed; health checks p99 below 50 ms. |
| 2026-06-18 02:10:30 | New WAF regex rule promoted to active path for all requests. |
| 2026-06-18 02:10:36 | Regex evaluation p99 increased from 0.010 ms to 913.2 ms. |
| 2026-06-18 02:10:44 | CPU proxy reached 100.0% while error rate stayed near zero. |
| 2026-06-18 02:10:53 | External probe observed queued requests; max latency 920.3 ms. |
| 2026-06-18 02:11:06 | Latency anomaly alert fired for `waf-api`. |
| 2026-06-18 02:11:22 | RCA selected `waf-api/regex_middleware` with medium confidence. |
| 2026-06-18 02:11:50 | WAF rule disabled and worker process restarted. |
| 2026-06-18 02:12:28 | Latency returned to baseline; synthetic health checks passed. |

## Root cause

The request path allowed a regex with catastrophic-backtracking behavior to run synchronously on every request, and the deploy path lacked a pre-release ReDoS safety check plus a canary rollout gate.

## Contributing factors

1. The detector relied on runtime symptoms, so static regex risk was invisible before user impact.
2. Error-rate alerting alone would under-report the incident because the main symptom was slow 200 responses and queued requests, not a large 5xx spike.
3. The RCA evidence lacked rollout-scope data, so it could identify `regex_middleware` but could not prove whether blast radius came from global rollout, canary bypass, or traffic shape.
4. Docker and the live W1+W2 pipeline were unavailable in this workspace, so the reproduction used a bounded local simulator rather than a full service stack.

## Detection

- **How was it detected?** A synthetic latency signal and simulated AIOps alert detected p99 latency above the 500 ms threshold.
- **MTTD:** 36 seconds from WAF activation at 02:10:30Z to page alert at 02:11:06Z.
- **Pipeline gaps observed during reproduction:**
  - Gap 1: No static ReDoS detector in the deploy path; the bad regex becomes visible only after runtime impact.
  - Gap 2: RCA evidence lacks rollout/canary state, so blast-radius cause remains partially inferred.
  - Gap 3: An error-rate-only detector would miss or delay this incident because the response class is not primarily 5xx.

## Response

- **First responder action:** Treat `waf-api` latency as user-visible, inspect recent rule activation, and compare CPU/latency drift time against deploy time.
- **Time to mitigate:** 80 seconds from activation to rule disable at 02:11:50Z.
- **Time to fully resolve:** 118 seconds from activation to baseline recovery at 02:12:28Z.
- **What went well:** Latency-based alerting caught the incident even though 5xx rate stayed low, and RCA selected the correct component.
- **What went poorly:** Detection happened only after the bad rule affected requests; release safety checks were not represented in the pipeline.
- **Where we got lucky:** The reproduction had a bounded input and a single service, so rollback was straightforward.

## Action items

| # | Action | Owner | Type | ETA |
|---|---|---|---|---|
| 1 | Add static ReDoS analysis for WAF rules before promotion. | Platform | preventive | 2026-06-25 |
| 2 | Require staged rollout metadata in RCA evidence. | AIOps | detective | 2026-06-28 |
| 3 | Add latency SLI alerts for slow 200 responses, not only 5xx/429 rate. | SRE | detective | 2026-06-24 |
| 4 | Add automatic rollback when WAF p99 latency exceeds 500 ms for 30 seconds after deploy. | Edge | mitigation | 2026-07-02 |
| 5 | Run this reproduction in Docker once a live W1+W2 pipeline container is available. | Platform | validation | 2026-07-05 |
