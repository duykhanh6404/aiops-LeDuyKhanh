# ADR-001: Use Pre-Deploy Guardrails Plus Runtime Topology-Aware RCA

## Status

Accepted

## Context

The Cloudflare WAF regex reproduction showed a class of outage where runtime AIOps can detect impact but only after users are already exposed. The observed pipeline selected `waf-api/regex_middleware` correctly with confidence 0.74, but the postmortem gaps show two missing signals: static ReDoS risk before deploy and rollout-scope evidence during RCA. W3-D2 also showed that RCA can choose a noisy gateway over a shared dependency when topology evidence is incomplete.

## Decision

The AIOps platform will combine pre-deploy guardrails for known high-risk changes with runtime topology-aware RCA that uses latency, CPU, rollout metadata, and dependency graph evidence.

## Alternatives considered

1. **Runtime alerting only**
   - Pros: simple, cheaper, works with existing metrics, fewer CI/CD integration points.
   - Cons: detects regex catastrophes only after user impact; cannot stop a risky rule before rollout; weak evidence for whether blast radius came from canary bypass or traffic pattern.
   - Reason rejected: it directly fails Gap 1 from the reproduction.

2. **Count-based RCA ranking**
   - Pros: fast to implement; easy to explain; no topology graph maintenance.
   - Cons: picks the noisiest service, which fails cascading and shared-dependency patterns; W3-D2 DNS latency selected `api-gateway` instead of `dns-resolver` under this style of bias.
   - Reason rejected: it cannot handle the D2 and D3 gap patterns consistently.

3. **LLM-only RCA**
   - Pros: flexible narrative output; can summarize logs and postmortems well.
   - Cons: may hallucinate a plausible root without metric evidence; hard to make deterministic; weak fit for automated mitigation.
   - Reason rejected: it is useful as an explanation layer but not safe as the primary RCA decision engine.

## Consequences

- **Positive:** Regex, config, and rollout failures can be stopped before full production impact when static guardrails catch them.
- **Positive:** Runtime RCA is more resilient to retry storms and shared dependencies because alert volume is only one signal.
- **Negative:** The platform needs updated topology and rollout metadata, which adds operational burden and new failure modes.
- **Negative:** CI/CD integration adds latency to deploys and can create false blocks if guardrail rules are too strict.
- **Risk introduced:** Static ReDoS checks may miss novel parser pathologies; mitigation is to keep runtime latency and CPU alerts as a second line of defense.
- **What gets locked in:** The platform treats evidence objects as first-class RCA input: metric drift, topology edge, deploy event, rollout scope, and synthetic probe result.
