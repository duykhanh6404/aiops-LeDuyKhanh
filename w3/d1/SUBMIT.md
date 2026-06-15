# W3-D1 Submission - Khanh

## 3 thứ học được

1. SLI phải đo từ trải nghiệm người dùng và tỷ lệ thuận với user pain; CPU hoặc memory chỉ nên là saturation signal.
2. Error budget biến một mục tiêu phần trăm thành số failure và thời gian cụ thể, giúp quyết định release hay ưu tiên reliability.
3. MWMBR kết hợp cửa sổ dài và ngắn bằng điều kiện AND nên vừa giảm spike noise vừa dừng cảnh báo nhanh sau khi incident kết thúc.

## 1 thứ vẫn chưa rõ

E vẫn chưa rõ nên chuẩn hóa metric instrumentation thế nào để một request chậm và một request lỗi chỉ bị đếm một lần khi availability và latency được gộp thành một SLI.

## 1 trade-off trong SLO decision của em mà không chắc

Em tune API Tier 1 từ burn rate 14.4 xuống 13 để MTTD delta giảm từ 60 giây xuống 0 giây. Replay không tăng false positive, nhưng ba ngày dữ liệu có thể chưa đại diện đủ cho traffic production dài hạn.

## Validation report

- noise_reduction_pct: 86.4%
- mttd_delta_s: 0s
- false_negative: 0
- verdict: pass
