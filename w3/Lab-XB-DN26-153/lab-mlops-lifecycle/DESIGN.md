# DESIGN - MLOps Lifecycle: Anomaly Detection

## 1. Ngưỡng drift và lý do chọn

Ngưỡng drift được đặt là `0.15` cho dataset-level drift score. Trên bộ `baseline.csv` so với `drifted.csv`, pipeline tính được drift score `0.5481`, lớn hơn ngưỡng gần 3.65 lần, nên đây là tín hiệu drift rõ ràng. Ngưỡng 0.15 được chọn vì các feature payment như `latency_p99`, `error_rate`, `rps` có dao động tự nhiên theo traffic, nếu đặt ngưỡng quá thấp sẽ retrain liên tục và gây noise. Trong code, nếu có Evidently thì có thể thay bằng DataDriftPreset; nếu môi trường chưa cài Evidently, fallback dùng mean/std/quantile shift để lab vẫn chạy được.

## 2. Đây là Data drift, Concept drift hay Performance drift?

Sự cố này có cả Data drift và Performance drift. Data drift thể hiện bằng phân phối của `latency_p99`, `error_rate`, `rps` thay đổi: `drifted.csv` có latency trung bình khoảng `162.37ms` so với baseline `128.95ms`, error rate `1.4818` so với `0.7913`, và rps `610.05` so với `468.17`. Performance drift thể hiện khi model v1 chạy trên `drifted.csv` chỉ đạt precision `0.3154` với ngưỡng chấp nhận `0.65`. Concept drift có khả năng xảy ra vì nhãn anomaly trong dữ liệu mới không còn phân tách giống thời điểm train ban đầu, nên `drift_detector.py` hỗ trợ `--check-mode combined` để không chỉ nhìn data drift.

## 3. Tại sao chọn cơ chế phê duyệt này?

Pipeline dùng human approval gate trước khi đổi alias `staging` thành `production`, vì đây là model phát hiện bất thường cho cổng thanh toán và false positive có thể kéo theo cảnh báo/on-call sai. Quá trình retrain được tự động hóa đến bước staging: detect drift, train v2 bằng sliding window, validate holdout, rồi mới hỏi `[y/N]`. Khi cần chạy test tự động, có thể dùng `--auto-approve` hoặc `--yes`, nhưng mặc định vẫn là cần con người phê duyệt. Cách này cân bằng giữa tốc độ MLOps và an toàn vận hành.

## 4. Nếu model v2 chạy tệ thì rollback v1 như thế nào?

Trước khi promote, retrain lưu lại version đang production, ví dụ `v1`. Sau khi v2 được promote thành `@production`, `retrain.py` theo dõi `post_deploy_eval.csv` trong 24 chu kỳ và tính precision/recall mỗi chu kỳ. Nếu precision nhỏ hơn `0.65`, orchestrator tự động set alias `production` về lại `v1`, set alias `archived` cho v2, gọi `/reload` của `serve.py`, và ghi event `auto_rollback_v2_to_v1` vào `outputs/audit_log.jsonl`. Nếu v2 ổn định, như lần chạy với data hiện tại precision `1.0000` trong 24/24 chu kỳ, pipeline giữ v2 ở production và ghi `post_deploy_stable`.

## 5. Vì sao dùng sliding window khi retrain?

Không train v2 chỉ trên `drifted.csv`, vì cách đó làm model học quá sát phân phối mới và dễ quên old-pattern traffic vẫn còn xuất hiện trong production. `retrain.py` tạo training set gồm 1008 dòng current window và 504 dòng cuối từ baseline, tổng cộng 1512 dòng. Cách sliding window này giúp v2 bắt được drift mới nhưng vẫn giữ lại một phần ngữ cảnh lịch sử. Trong lần test, v2 đạt holdout precision `1.0000`, tốt hơn v1 `0.0000` trên old-pattern holdout.
