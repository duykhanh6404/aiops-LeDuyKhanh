# W3-D2 Submission - Khanh

## 3 thu toi hoc duoc ve AIOps pipeline cua minh

1. Detection alone is not enough; chaos also has to score whether RCA picks the actual root instead of the loudest downstream symptom.
2. External synthetic probe data is useful because it measures the user-visible path even when internal metrics or the observability pipeline are incomplete.
3. Meta-monitoring is part of reliability. If log ingestion or alert input breaks, the AIOps pipeline can become silent during the incidents it is supposed to explain.

## 1 fault ma toi mong pipeline catch nhung no miss

- Experiment: `auth_clock_skew`
- Why I expected detection: a 60-second clock skew on `auth-svc` should create JWT or certificate validation failures that users experience as authentication problems.
- Why pipeline missed (hypothesis): auth errors were probably grouped into broad HTTP error metrics or client-looking 4xx classes, so the detector threshold did not see them as a distinct anomaly.

## 1 trade-off trong design pipeline ma toi muon rethink

I want to rethink how much the pipeline relies on service-local alert volume for RCA. It helps catch common failures quickly, but DNS latency showed that a high-traffic gateway can look like the root when the real fault is a shared dependency. Topology and causal timing should have more weight than raw alert count.

## Scoreboard summary

- detected: 8/10
- rca_correct: 7/8
- mttd_p50: 40.0s
- false_alarms: 1
- verdict: pass
