# Chaos Engineering Report - Khanh

## 1. Setup

- Stack version + commit hash: W3-D2 starter pack, no 10-service docker-compose included, workspace snapshot local.
- Pipeline version + commit hash: `chaos_runner.py` simulator-backed AIOps pipeline substitute, local workspace snapshot.
- Baseline window: 2026-06-16T07:00:00+00:00 -> 2026-06-16T07:05:00+00:00.
- Total experiments run: 10.
- Execution mode: `python chaos_runner.py --mode simulate --cooldown 0 --experiments experiments.yaml --out chaos_results.json`.
- External probe: `probe.log` contains 15 samples against `http://127.0.0.1:18080/`, pass-rate 100%, p50 latency 22 ms, p95 latency 28 ms.

The downloaded W3-D2 pack is a starter pack and explicitly does not include the 10-service Docker stack, AIOps FastAPI pipeline, Pumba, or Toxiproxy binaries. To keep the assignment executable and auditable, I implemented `live` command dispatch for the real tools and used `simulate` mode for this submission. The simulated run is not presented as production evidence; it validates experiment design, runner logic, scoring, and the report workflow.

## 2. Results table

```text
==== Chaos Run ====
Total: 10
Detected: 8/10
RCA correct: 7/8
False alarms in baseline windows: 1
Precision: 0.89
Recall: 0.80
MTTD p50: 40.0s, p95: 62s

Per-experiment:
| # | name | detected | mttd | rca_service | rca_correct |
|---|---|---|---|---|---|
| 1 | payment_latency | Y | 28s | payment-svc | Y |
| 2 | payment_packet_loss | Y | 34s | payment-svc | Y |
| 3 | inventory_pod_kill | Y | 21s | inventory-svc | Y |
| 4 | api_gateway_cpu_saturation | Y | 46s | api-gateway | Y |
| 5 | payment_db_memory_fill | Y | 62s | payment-db | Y |
| 6 | auth_clock_skew | N | - | - | N |
| 7 | log_collector_disk_fill | N | - | - | N |
| 8 | frontend_gateway_partition | Y | 17s | edge:frontend-api-gateway | Y |
| 9 | dns_resolver_latency | Y | 55s | api-gateway | N |
| 10 | checkout_retry_storm | Y | 73s | payment-svc | Y |

Gaps identified:
- 6: auth clock-skew anomaly below detector noise floor -> inspect detector thresholds, topology correlation, and evidence grounding
- 7: meta-monitoring missing for log ingestion path -> inspect detector thresholds, topology correlation, and evidence grounding
- 9: topology/causal RCA favored noisy gateway symptom -> inspect detector thresholds, topology correlation, and evidence grounding
```

## 3. Detailed per-experiment analysis

### 1. payment_latency

Hypothesis: adding 500 ms +/- 100 ms latency to `payment-svc` for 60 seconds should trip a latency anomaly within 30 seconds and RCA should pick `payment-svc`. Observed result matched the hypothesis: the simulated detector fired at 28 seconds, with RCA root `payment-svc` and confidence 0.88. Evidence was a payment p99 latency breach above 500 ms. This is a clean case for topology-aware RCA because checkout is affected, but payment is the first service with a direct degraded signal. The result supports the pipeline's ability to detect user-visible dependency latency before the fault is misattributed to checkout.

### 2. payment_packet_loss

Hypothesis: 30% packet loss on `payment-svc` should increase timeout and 5xx rates within 45 seconds, and RCA should select `payment-svc`. The run detected the fault in 34 seconds and RCA selected `payment-svc` with confidence 0.84. Evidence linked payment timeout and 5xx rate increases, which is stronger than a single latency metric because packet loss can appear as both slow calls and failed calls. The result matches expected behavior. It also confirms that the detector should treat packet loss as an error-rate problem rather than only a latency problem, which matters when retries hide individual request failures from downstream services.

### 3. inventory_pod_kill

Hypothesis: killing one `inventory-svc` container every 60 seconds should create an availability anomaly while restart policy recovers the container, and RCA should select `inventory-svc`. Observed detection was fast at 21 seconds, with RCA root `inventory-svc` and confidence 0.91. The important evidence was that inventory restart and availability signals preceded checkout errors. This is the expected causal order: checkout is only the caller that experiences failed inventory calls. The experiment passed and shows the pipeline can separate a leaf service crash from its downstream symptom path when availability and restart signals are present.

### 4. api_gateway_cpu_saturation

Hypothesis: CPU saturation at 90% on `api-gateway` for 90 seconds should produce a latency cascade across downstream paths, and RCA should choose the gateway as shared upstream root. The pipeline detected in 46 seconds and RCA selected `api-gateway` with confidence 0.79. This matched expectation, although detection was slower than the direct service faults because the signal is a fanout pattern rather than one local failure. The key evidence was gateway CPU rising together with broad downstream latency. This case validates topology-aware correlation: if RCA ranked only by alert volume, it might pick a downstream service with many symptoms instead of the gateway.

### 5. payment_db_memory_fill

Hypothesis: filling `payment-db` memory to 95% should increase payment connection pool wait and query latency, while RCA should pick `payment-db` rather than `payment-svc`. The run detected the fault at 62 seconds and RCA selected `payment-db` with confidence 0.73. This passed but was the slowest correct RCA except the retry-storm test. The slower MTTD is plausible because database memory pressure often degrades through pool waits before it becomes a clear request error. The experiment indicates that the pipeline needs DB exporter signals and service metrics together; service-only symptoms would probably blame `payment-svc`.

### 6. auth_clock_skew

Hypothesis: skewing `auth-svc` clock forward by 60 seconds should create JWT or certificate validation failures and RCA should pick `auth-svc`. The pipeline missed this experiment: no alert fired and there was no RCA output. The simulated evidence says JWT errors stayed below the detector noise floor. This does not match expectation and is a meaningful gap because clock skew can cause authentication failures that look like client errors or intermittent cert issues. The likely weakness is detector coverage: auth-specific error classes need explicit SLIs or segmented anomaly thresholds instead of being buried inside aggregate HTTP 4xx/5xx counts.

### 7. log_collector_disk_fill

Hypothesis: filling the `log-collector` disk to 95% should raise log ingestion lag, and meta-monitoring should detect that the AIOps pipeline is losing visibility. The pipeline missed this experiment: no alert fired and RCA had no root service. This is especially important because user-facing probe pass-rate can stay healthy while the observability path is failing. The result suggests the pipeline monitors application symptoms but not its own telemetry freshness. This matches the monitoring dependency loop failure mode from the material: when the observability substrate degrades, the AIOps system can go silent exactly when operators need it most.

### 8. frontend_gateway_partition

Hypothesis: partitioning `frontend` from `api-gateway` for 30 seconds should cause broad downstream timeout from the user's edge path, and RCA should identify the edge between frontend and gateway. The run detected the fault in 17 seconds, the fastest MTTD in the set, and RCA returned `edge:frontend-api-gateway` with confidence 0.86. This matched expectation. The evidence was that frontend timeouts began before service-local errors, while gateway request rate dropped. This case is a good example of why external probe data matters: internal services may look mostly healthy, but the user-visible path is broken.

### 9. dns_resolver_latency

Hypothesis: injecting 2 seconds of DNS lookup latency should cause intermittent errors across unrelated services, and topology-aware RCA should pick `dns-resolver`. The detector fired at 55 seconds, so detection succeeded, but RCA returned `api-gateway` with confidence 0.58. This did not match expected root. The likely problem is temporal correlation bias: the gateway is noisy because most user traffic passes through it, so it can appear as the common root even when DNS is the shared dependency. The fix is to model DNS as an explicit dependency and include lookup latency as evidence before ranking gateway fanout.

### 10. checkout_retry_storm

Hypothesis: injecting 20% HTTP 500 on `checkout-svc` should create retry amplification toward payment and inventory, and RCA must not pick checkout as root. The detector fired at 73 seconds and RCA selected `payment-svc`, so the negative ground-truth condition passed. MTTD was the slowest in the run, which is expected because retry-storm causality needs enough time to distinguish cause from symptom volume. This result suggests the RCA logic can avoid the naive "loudest service wins" failure mode for this case. The confidence was only 0.67, so evidence grounding should still be improved before trusting an automated fix recommendation.

## 4. Gap analysis - top 3 pipeline weakness

### Gap 1: auth clock skew missed

- Symptom: Experiment 6 had no detection, no MTTD, and no RCA output.
- Likely cause in pipeline: detector miss because JWT/certificate errors are below the aggregate error-rate noise floor or are excluded as client-looking 4xx.
- Recommended fix: add auth-specific SLIs for token validation failures and certificate verification errors, then alert on sustained deviations by endpoint and error class. This maps to failure mode 7.1, where the anomaly is real but submerged under broad metric noise.

### Gap 2: log ingestion failure missed

- Symptom: Experiment 7 had no alert even though log ingestion lag and collector disk pressure should have changed.
- Likely cause in pipeline: missing meta-monitoring. The pipeline watches services but does not measure freshness, lag, or completeness of the telemetry path it depends on.
- Recommended fix: add black-box and white-box health checks for the observability pipeline: log ingestion lag, dropped log count, collector disk usage, and "last event age" per service. This addresses failure mode 7.5, the monitoring dependency loop.

### Gap 3: DNS latency RCA wrong

- Symptom: Experiment 9 was detected in 55 seconds, but RCA selected `api-gateway` instead of `dns-resolver`.
- Likely cause in pipeline: topology model is incomplete or correlation overweights high-volume gateway symptoms. DNS is a shared dependency, so many services degrade together, but the gateway becomes the loudest visible node.
- Recommended fix: make DNS an explicit node in the dependency graph and add causal lag scoring. The root should be a shared upstream dependency whose metric shifts before downstream timeout volume, matching failure modes 7.2 and 7.3.

## 5. Hypothesis cho gap chua khang dinh

The main unconfirmed hypothesis is that experiment 9 fails RCA because DNS is absent from the topology graph. A follow-up experiment should inject DNS latency while also exposing `dns_lookup_p99` and resolver error metrics to the pipeline. If RCA becomes correct after adding those edges and metrics, the root problem is topology/evidence coverage. If it remains wrong, the ranking algorithm likely overweights alert volume and needs causal lag analysis.
