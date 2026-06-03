# E2E Data Layer Architecture cho Anomaly Detection trên Payment Service

## 1. Use Case Description
Phát hiện các hành vi bất thường (anomaly detection) đối với hệ thống Payment Service (ví dụ: số lượng giao dịch lỗi tăng đột biến, độ trễ thanh toán tăng vọt, số lượng giao dịch từ một IP lạ tăng cao bất thường).

## 2. Các Components & Tool Choice
- **Service (Nguồn dữ liệu):** Payment Microservice (viết bằng Go/Java). Cung cấp các thông tin giao dịch (trạng thái, latency, metadata).
- **Collection (Thu thập):** OpenTelemetry (OTel) SDK & OTel Collector.
  - Sử dụng OTel SDK để tự động capture metrics, traces và logs trực tiếp từ code.
  - Gửi dữ liệu dưới chuẩn OTLP tới OTel Collector để filtering và batching.
- **Transport (Vận chuyển):** Apache Kafka.
  - OTel Collector gửi dữ liệu stream tới Kafka topics (ví dụ: `payment-metrics`, `payment-logs`).
  - Kafka đóng vai trò làm message broker giúp decouple giữa quá trình thu thập và xử lý, đảm bảo khả năng chịu tải và không bị mất dữ liệu khi lưu lượng giao dịch tăng vọt.
- **Processing (Xử lý Data/Stream):** Apache Flink.
  - Tích hợp trực tiếp với Kafka để đọc dữ liệu dạng real-time stream.
  - Flink tính toán các feature theo sliding/rolling windows (ví dụ: tỉ lệ lỗi trong 5 phút, độ trễ trung bình, Z-score).
  - Tích hợp một model Machine Learning nhẹ để dự đoán anomaly (Isolation Forest, AutoEncoder) chạy trên từng event hoặc micro-batch.
- **Storage (Lưu trữ):** 
  - Elasticsearch (đối với Logs và raw Traces) để phục vụ việc search/investigate khi có cảnh báo.
  - Prometheus / VictoriaMetrics (đối với Time-series Metrics và các aggregate features do Flink sinh ra).
- **Query / ML / Visualization (Hiển thị & Cảnh báo):** Grafana & Alertmanager.
  - Grafana kết nối với Prometheus và Elasticsearch để tạo các dashboard real-time về trạng thái Payment Service.
  - Alertmanager được cấu hình để gửi thông báo (qua Slack/PagerDuty) khi các metric vượt ngưỡng hoặc khi hệ thống ML phát hiện anomaly.

## 3. Data Flow
`Payment Service` -> `OTel Collector` -> `Apache Kafka` -> `Apache Flink` (ML Detection) -> `Prometheus / Elasticsearch` -> `Grafana / Alertmanager`
