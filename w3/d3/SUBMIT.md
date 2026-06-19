# W3-D3 Submission - Khanh

## Outage chosen

- ID: 3
- Name: Cloudflare WAF regex 2019-07-02
- Why this one: I chose this outage because it tests a subtle AIOps failure mode: the service may not crash and may not emit many 5xx errors, but users still experience severe latency. It also connects directly to W3-D1 latency SLOs and W3-D2 gaps around RCA evidence quality.
- Failure mode: catastrophic_backtracking

## 3 thu toi hoc tu outage nay

1. A request can be user-visible bad even when it returns a slow 200, so availability-only alerting is not enough.
2. Runtime AIOps is necessary but not sufficient; deploy-time guardrails such as ReDoS checks can prevent impact earlier.
3. RCA needs rollout metadata. Knowing the root component is useful, but knowing whether blast radius came from global rollout or canary failure changes the fix.

## 1 thu pipeline cua toi se van miss neu outage nay xay ra real

- Pattern: catastrophic regex or parser path activated by a config/rule deploy.
- Why miss: the current pipeline can detect latency after impact, but it has no static ReDoS scanner and no pre-deploy risk score. It also has limited rollout/canary evidence for blast-radius explanation.
- Mitigation idea: add regex safety checks, staged rollout gates, and deploy metadata as first-class RCA evidence.

## 1 quyet dinh trong ADR ma toi khong hoan toan chac

I am not fully sure about adding pre-deploy guardrails into the AIOps platform boundary. It is technically the right prevention layer for regex risk, but it expands AIOps from observation into release governance. That can improve safety, but it can also slow delivery and create false blocks if the guardrail is noisy.

## Cost model verdict cho stack cua toi

- ROI: 4.17
- Payback: 0.24 thang
- Verdict: worth_it
