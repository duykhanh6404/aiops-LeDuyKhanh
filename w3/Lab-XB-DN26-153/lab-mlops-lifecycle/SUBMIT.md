# SUBMIT - MLOps Lifecycle

## 1. Hệ thống của bạn giải quyết vấn đề gì?

Hệ thống quản lý vòng đời cho model anomaly detection của cổng thanh toán. Nó train model v1 từ `baseline.csv`, serve model qua FastAPI, theo dõi drift giữa baseline và current window, retrain v2 khi drift vượt ngưỡng, đưa v2 vào `staging`, chờ phê duyệt của con người, rồi rollout sang `production` bằng cơ chế alias/reload. Nếu sau rollout precision bị giảm dưới `0.65`, orchestrator có cơ chế rollback về version production trước đó.

## 2. Bạn đã chọn metric drift nào và kết quả ra sao?

Metric drift chính là dataset-level distribution shift trên 3 feature `latency_p99`, `error_rate`, `rps`, ngưỡng `0.15`. Lần chạy với `baseline.csv` và `drifted.csv` cho kết quả:

```text
[drift_detector] Drift score     : 0.5481
[drift_detector] Threshold       : 0.1500
[drift_detector] Drift detected  : True
[drift_detector] Drifted features: ['latency_p99', 'error_rate', 'rps']
[drift_detector] Perf precision  : 0.3154  (threshold 0.65)
[drift_detector] Perf recall     : 0.8020
[drift_detector] Perf degraded   : True
```

Kết quả này cho thấy drift không chỉ nằm ở dữ liệu đầu vào mà còn làm chất lượng model v1 giảm rõ rệt.

## 3. Bạn retrain và validate model v2 như thế nào?

V2 không được train chỉ trên dữ liệu drifted. `retrain.py` dùng sliding window gồm 504 dòng baseline gần nhất và 1008 dòng current, tổng 1512 dòng, để tránh overfit vào phân phối mới. Sau khi train, model được gán alias `staging` và validate trên `holdout.csv` trước khi chờ phê duyệt.

```text
[retrain] Sliding window : 1512 rows (504 old + 1008 current)
[pipeline] Trained rows  : 1512
[pipeline] Anomaly rate  : 0.0106
[pipeline] Local alias   : anomaly-detector v2 -> @staging
[retrain] Holdout v1 precision: 0.0000  recall: 0.0000
Holdout validation — v2 precision: 1.0000  recall: 1.0000
```

## 4. Cơ chế phê duyệt và rollout hoạt động ra sao?

Mặc định, pipeline hỏi `Promote staging to production? [y/N]` để bắt buộc có người phê duyệt trước khi đổi production model. Khi chạy acceptance test, mình dùng `--auto-approve` để không bị treo terminal, nhưng luồng vẫn ghi rõ là đã qua approval gate. Sau khi promote, orchestrator gọi `/reload` của service; nếu service chưa chạy thì log warning và không làm fail cả pipeline.

```text
[retrain] Approval gate  : auto-approved for test run
[retrain] Promoted       : v2 -> @production
[retrain] serve reload    : skipped (ConnectionError: service was not running)
```

## 5. Nếu v2 tệ sau deploy thì hệ thống rollback ra sao?

`retrain.py` theo dõi 24 chu kỳ post-deploy bằng `post_deploy_eval.csv`. Mỗi chu kỳ tính precision/recall của model đang production; nếu precision < `0.65`, code set `@production` về version cũ, set v2 thành `@archived`, gọi `/reload`, và ghi event `auto_rollback_v2_to_v1` vào `outputs/audit_log.jsonl`. Với data hiện tại, v2 pass toàn bộ 24 chu kỳ nên rollback không bị kích hoạt:

```text
post_deploy_monitor Cycle 01/24 precision: 1.0000  recall: 1.0000
...
post_deploy_monitor Cycle 24/24 precision: 1.0000  recall: 1.0000
[retrain] v2 passed 24 post-deploy cycles
```

Điều mình vẫn cẩn thận nhất là threshold `0.65`: nó phù hợp để chặn model quá kém, nhưng trong production thật nên được review theo chi phí false positive/false negative của cổng thanh toán.

## Lệnh đã dùng để verify

```bash
python pipeline.py --data data/baseline.csv

python drift_detector.py \
  --reference data/baseline.csv \
  --current data/drifted.csv \
  --check-mode combined \
  --labeled-current data/drifted.csv \
  --model-uri models:/anomaly-detector@production

python retrain.py \
  --reference data/baseline.csv \
  --current data/drifted.csv \
  --holdout data/holdout.csv \
  --post-deploy-eval data/post_deploy_eval.csv \
  --auto-approve
```
