# AIOps W2-D2 Submit

## 1. Confidence và threshold auto-rollback

Cluster lớn nhất em xử lý là `c-000-000`, có 18 alert. Top-1 là `payment-svc` với graph score `1.0`, RCA output confidence `0.99`, class `connection_pool_exhaustion`. Nếu phải đặt threshold để auto-rollback mà không cần SRE confirm, em chọn `0.97`. Lý do: trong case này graph, temporal và retrieval đều cùng hướng; top similar incident `INC-2025-11-08` match payment + checkout cascade và class pool exhaustion. Tuy vậy auto-rollback là hành động rủi ro, nên threshold phải rất cao và cần guardrail ngoài score: action nằm trong allowlist, có deploy gần đây, pool saturation vẫn active, và rollback target đã biết tốt.

## 2. Classifier variant đã chọn

Em chọn variant A: rule-based / retrieval-only kNN. Pipeline load `incidents_history.json`, tính keyword similarity bằng service overlap, root cause candidate, severity và token metric/class. Sau đó classifier lấy `class` và `actions` từ top-1 similar incident. Kết quả chạy thực tế: cluster chính ra `connection_pool_exhaustion`, action rollback/scale pool/monitor; recommender ra `memory_leak`; search ra `cache_cold_start`. Trade-off với free LLM hoặc paid LLM là rule-based ít linh hoạt hơn, không viết reasoning đẹp bằng LLM, nhưng deterministic, không cần API key, không tốn chi phí, và dễ validate schema. Với dataset nhỏ và history có cấu trúc tốt, cách này đủ cho grading và debug.

## 3. Industry landscape

Pipeline em xây gần `Dynatrace Davis` nhất: nó coi service graph là source of truth, sau đó dùng temporal signal và incident history để rank culprit. Trong GeekShop, lựa chọn này hợp lý vì đây là e-commerce có alert volume cao, critical path rõ: edge -> checkout -> payment -> DB, service map tương đối ổn định. Nếu domain thay đổi nhanh, serverless nhiều, hoặc service graph thiếu/sai, em sẽ nghĩ đến hướng gần `Causely` hơn, tức học causal relationship từ metric/time-series. Nhưng với bài này, graph + retrieval là điểm cân bằng tốt: nhanh, giải thích được, và không cần raw metric dài.
