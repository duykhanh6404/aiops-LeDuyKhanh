# Thiết kế tự động khắc phục sự cố khép kín (Closed-Loop Auto-Remediation Design)

## 1. Dựa trên Rule (Quy tắc) hay LLM (Mô hình ngôn ngữ lớn)?

Em đã chọn engine dựa trên rule làm hướng tiếp cận chính. Quy trình khắc phục sự cố khép kín có thể làm thay đổi một hệ thống đang hoạt động, do đó quá trình ra quyết định phải mang tính xác định, có thể kiểm toán và bị giới hạn trong một danh sách cho phép (allowlist). Trong `config.yaml`, `HighLatency` ánh xạ tới `restart_service`, `HighErrorRate` ánh xạ tới `clear_cache`, `InstanceDown` ánh xạ tới `restart_service`, và `BadDeploy` ánh xạ tới `transactional_deploy`.

Kết quả đầu ra của LLM chỉ được coi là một gợi ý. Orchestrator sẽ xác thực bất kỳ runbook nào được gợi ý với `allowed_runbooks` trước khi chạy thử (dry-run) hoặc thực thi thực tế. Kịch bản 6 chứng minh cơ chế bảo vệ này: gợi ý kiểu LLM `delete_database` bị từ chối với lỗi `DECISION_VALIDATION_FAILED`, không có log `RUNBOOK_EXEC` nào được ghi lại và bộ đếm circuit breaker không tăng lên.

## 2. Cấu hình Blast-radius (Bán kính ảnh hưởng)

Các giá trị hiện tại:

- `max_actions_per_minute: 4`
- `max_restarts_per_service_per_hour: 2`

Cho phép 4 hành động mỗi phút giúp orchestrator xử lý một sự cố nhỏ ảnh hưởng đến nhiều service mà không để cơn bão cảnh báo từ Alertmanager tạo ra vô số hành động. Giới hạn 2 lần khởi động lại cho mỗi service mỗi giờ giúp ngăn chặn vòng lặp khởi động. Nếu 2 lần khởi động lại không giúp phục hồi service, cách xử lý an toàn hơn là leo thang sự cố (escalation) hoặc dừng hẳn bộ ngắt mạch (circuit breaker halt) thay vì liên tục thay đổi hệ thống.

Kịch bản 3 tạm thời tăng giới hạn khởi động lại bên trong trình giả lập để cô lập hành vi của circuit-breaker. Trong môi trường thực tế (production), blast-radius và circuit breaker phối hợp với nhau: blast-radius giới hạn phạm vi ảnh hưởng, circuit breaker dừng việc tự động hóa khi nó thất bại liên tục.

## 3. Metric xác minh, ngưỡng (threshold) và thời gian chờ (timeout)

Bước xác minh sẽ kiểm tra `service_health_score`, đây là một metric sức khỏe chuẩn hóa của Prometheus nằm trong khoảng `0.0` đến `1.0`. Orchestrator sẽ lấy mẫu (poll) ít nhất 3 lần trong khoảng thời gian xác minh là 60 giây. Hành động chỉ được coi là thành công khi mẫu cuối cùng đạt `>= 1.0`.

Kịch bản 1 sử dụng các giá trị Prometheus giả lập `[0.2, 0.8, 1.0]`. Orchestrator ghi nhận 3 sự kiện `VERIFY_SAMPLE` và chỉ ghi log `ACTION_SUCCESS` sau khi mẫu thứ 3 trở lại mức cơ bản (baseline). Nếu xác minh thất bại, tiến trình rollback sẽ được tự động kích hoạt.

## 4. Reset bộ ngắt mạch (Circuit breaker reset)

Bộ ngắt mạch sẽ mở ra sau 3 lần thất bại liên tiếp do lỗi thực thi hoặc xác minh thất bại. Khi được mở, orchestrator ghi log `CIRCUIT_OPEN` và `CIRCUIT_BREAKER_HALT`, sau đó bỏ qua các cảnh báo tiếp theo mà không thực hiện thêm hành động nào.

Việc reset (đặt lại) được thực hiện thủ công thông qua `reset_circuit(reason)`. Em chọn phương pháp reset thủ công vì các vòng lặp khắc phục tự động rất rủi ro: nếu nguyên nhân gốc rễ vẫn còn, việc tự động reset có thể khiến hệ thống tiếp tục bị thay đổi và gây ra lỗi. Một quy trình reset an toàn chỉ nên diễn ra sau khi đội ngũ on-call xác nhận nguyên nhân gốc rễ, trạng thái sức khỏe của service đã ổn định, các bộ đếm blast-radius đã giảm bớt, và runbook bị lỗi đã được sửa hoặc vô hiệu hóa.
