# Giải thích chi tiết: Closed-Loop Auto-Remediation Orchestrator

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Phát hiện sự cố (Detect)](#2-phát-hiện-sự-cố-detect)
3. [Quyết định hành động (Decide)](#3-quyết-định-hành-động-decide)
4. [Kiểm tra an toàn trước khi thực thi (Blast-radius + Dry-run)](#4-kiểm-tra-an-toàn-trước-khi-thực-thi)
5. [Thực thi hành động (Act)](#5-thực-thi-hành-động-act)
6. [Xác minh kết quả (Verify)](#6-xác-minh-kết-quả-verify)
7. [Khôi phục khi thất bại (Rollback)](#7-khôi-phục-khi-thất-bại-rollback)
8. [Bộ ngắt mạch (Circuit Breaker)](#8-bộ-ngắt-mạch-circuit-breaker)
9. [Giải thích từng Scenario](#9-giải-thích-từng-scenario)
10. [Câu hỏi mentor có thể hỏi và đáp án](#10-câu-hỏi-mentor-có-thể-hỏi-và-đáp-án)

---

## 1. Tổng quan hệ thống

Closed-Loop Orchestrator là một hệ thống tự động khắc phục sự cố theo mô hình **vòng lặp khép kín (closed-loop)**. Khác với cách xử lý thủ công truyền thống (nhận cảnh báo → SSH vào server → restart → kiểm tra), hệ thống này tự động hóa toàn bộ quy trình theo 5 bước tuần tự:

```
Detect → Decide → Dry-run/Blast-radius → Act → Verify → (Rollback nếu cần)
```

**Tại sao gọi là "khép kín" (closed-loop)?**

Vì kết quả của bước cuối cùng (Verify) sẽ **phản hồi ngược** lại hệ thống: nếu xác minh thành công thì dừng, nếu thất bại thì kích hoạt rollback và tăng bộ đếm lỗi. Bộ đếm lỗi này lại ảnh hưởng đến quyết định ở vòng lặp tiếp theo (Circuit Breaker). Đây chính là đặc trưng của hệ thống điều khiển khép kín — có phản hồi (feedback) từ đầu ra quay ngược lại đầu vào.

**Kiến trúc tổng thể trong code:**

- `closed_loop.py` — File chính chứa toàn bộ logic orchestrator.
- `config.yaml` — File cấu hình: ánh xạ cảnh báo → runbook, ngưỡng blast-radius, cấu hình verify và circuit breaker.
- `runbooks/*.sh` — Các bash script thực hiện hành động cụ thể (restart, clear cache, scale,...).
- `run_scenarios.py` — File chạy 6 kịch bản kiểm thử nghiệm thu.

---

## 2. Phát hiện sự cố (Detect)

### Cơ chế hoạt động

Orchestrator sử dụng cơ chế **polling** (hỏi vòng định kỳ) để lấy danh sách cảnh báo từ Alertmanager. Cứ mỗi **15 giây** (cấu hình qua `poll_interval_seconds` trong `config.yaml`), orchestrator gửi một HTTP request tới API của Alertmanager:

```
GET http://localhost:9093/api/v2/alerts
```

### Luồng xử lý trong code

Trong class `AlertmanagerClient` (dòng 70–86 của `closed_loop.py`):

1. Orchestrator gọi `poll_alerts()` để lấy danh sách cảnh báo.
2. API trả về một mảng JSON, mỗi phần tử là một cảnh báo với các `labels` (nhãn).
3. Code phân tích (parse) từng cảnh báo, trích xuất 3 thông tin quan trọng:
   - `alertname` — Tên cảnh báo, ví dụ: `HighLatency`, `InstanceDown`, `HighErrorRate`.
   - `service` — Service nào đang bị ảnh hưởng, ví dụ: `payment-svc`, `api-gateway`.
   - `severity` — Mức độ nghiêm trọng, ví dụ: `page`, `critical`.
4. Mỗi cảnh báo được đóng gói thành một đối tượng `Alert` (dòng 62–67).

```python
@dataclass(frozen=True)
class Alert:
    name: str        # Ví dụ: "HighLatency"
    service: str     # Ví dụ: "payment-svc"
    severity: str    # Ví dụ: "page"
    labels: dict     # Toàn bộ nhãn gốc từ Alertmanager
```

### Ghi log sự kiện

Ngay khi phát hiện cảnh báo, orchestrator ghi log sự kiện `ALERT_DETECTED` với đầy đủ thông tin:

```json
{
  "ts": "2026-06-18T08:00:00+00:00",
  "event_type": "ALERT_DETECTED",
  "service": "api",
  "action": null,
  "result": "ok",
  "alert_name": "HighLatency",
  "severity": "page"
}
```

### Tại sao dùng polling thay vì webhook?

- **Polling** đơn giản hơn, không cần cấu hình Alertmanager gửi webhook ngược lại.
- Orchestrator kiểm soát được nhịp độ xử lý (15 giây/lần), tránh bị tràn ngập cảnh báo.
- Trong lab, polling là đủ cho mục đích minh họa. Trong production thật, có thể kết hợp cả webhook để giảm độ trễ.

---

## 3. Quyết định hành động (Decide)

### Cơ chế hoạt động

Sau khi phát hiện cảnh báo, orchestrator phải quyết định: **dùng runbook nào để xử lý?** Hệ thống hỗ trợ 2 chế độ quyết định:

### Chế độ 1: Rule-based (Dựa trên quy tắc) — Mặc định

Đây là một bảng ánh xạ cố định trong `config.yaml`:

```yaml
rules:
  HighLatency:
    runbook: restart_service    # Cảnh báo latency cao → restart service
    action: restart
  HighErrorRate:
    runbook: clear_cache        # Tỷ lệ lỗi cao → xóa cache
    action: clear_cache
  InstanceDown:
    runbook: restart_service    # Service chết → restart
    action: restart
  BadDeploy:
    runbook: transactional_deploy  # Deploy lỗi → chạy transactional deploy
    action: transactional_deploy
```

Trong code, hàm `decide()` (dòng 250–258) tra bảng này:

```python
def decide(self, alert, llm_suggestion=None):
    rule = self.config["rules"].get(alert.name)  # Tìm rule khớp tên cảnh báo
    if not rule:
        return None, "no_matching_rule"           # Không tìm thấy → từ chối
    return rule["runbook"], "rule_match"           # Trả về tên runbook
```

**Ưu điểm:** Xác định (deterministic), kiểm toán được (auditable), không phụ thuộc mạng/API bên ngoài.

### Chế độ 2: LLM-based (Dựa trên mô hình ngôn ngữ lớn)

Khi `decision_mode="llm"`, orchestrator nhận gợi ý từ LLM (ví dụ: Claude). Tuy nhiên, gợi ý này **bắt buộc phải nằm trong danh sách `allowed_runbooks`** mới được chấp nhận:

```python
if self.decision_mode == "llm":
    if llm_suggestion in self.config["allowed_runbooks"]:
        return llm_suggestion, "llm_allowlisted"       # Gợi ý hợp lệ
    return None, "llm_suggestion_not_allowlisted"       # Gợi ý bị từ chối
```

**Danh sách `allowed_runbooks`** đóng vai trò như một **allowlist (danh sách trắng)**:

```yaml
allowed_runbooks:
  restart_service:
    path: runbooks/restart_service.sh
    rollback: runbooks/rollback_restart.sh
  clear_cache:
    path: runbooks/clear_cache.sh
    rollback: runbooks/rollback_restart.sh
  scale_service:
    path: runbooks/scale_replicas.sh
    rollback: runbooks/rollback_restart.sh
  transactional_deploy:
    path: runbooks/transactional_deploy.sh
    type: transaction
    steps: [prepare, switch, cleanup]
```

Nếu LLM gợi ý `delete_database` (không có trong danh sách), orchestrator **từ chối** và ghi log `DECISION_VALIDATION_FAILED`. Đây là cơ chế phòng chống **LLM hallucination** (ảo giác AI).

---

## 4. Kiểm tra an toàn trước khi thực thi

Trước khi thực sự chạy một hành động, orchestrator phải vượt qua 2 chốt kiểm tra:

### 4.1. Blast-radius (Bán kính ảnh hưởng)

Blast-radius giới hạn số lượng hành động trong một khoảng thời gian, ngăn chặn orchestrator "bão" hành động khi có quá nhiều cảnh báo cùng lúc.

**Cấu hình:**

```yaml
blast_radius:
  max_actions_per_minute: 4          # Tối đa 4 hành động mỗi phút
  max_restarts_per_service_per_hour: 2  # Tối đa 2 lần restart cho mỗi service mỗi giờ
```

**Cách hoạt động trong code** (class `BlastRadiusPolicy`, dòng 124–153):

- Mỗi khi thực hiện một hành động, orchestrator ghi lại **timestamp** vào một hàng đợi (`deque`).
- Trước mỗi hành động mới, nó đếm số hành động đã thực hiện trong cửa sổ thời gian:
  - Trong 60 giây gần nhất: đã có bao nhiêu hành động tổng cộng?
  - Trong 3600 giây (1 giờ) gần nhất: service này đã bị restart bao nhiêu lần?
- Nếu vượt ngưỡng → **từ chối** và ghi log `BLAST_RADIUS_REJECTED`.
- Nếu chưa vượt → **cho phép** và ghi log `BLAST_RADIUS_PASS`.

### 4.2. Dry-run (Chạy thử)

Trước khi thực thi thật, orchestrator **luôn** gọi runbook với cờ `--dry-run` trước:

```bash
bash runbooks/restart_service.sh --service payment-svc --dry-run
```

Trong chế độ dry-run, script chỉ in ra những gì nó **sẽ làm** mà không thực hiện bất kỳ thay đổi nào:

```
DRY_RUN restart_service service=payment-svc
```

- Nếu dry-run trả về exit code `0` → ghi log `DRY_RUN_PASS`, tiến hành thực thi thật.
- Nếu dry-run trả về exit code khác 0 → ghi log `DRY_RUN_FAIL`, **dừng ngay**, không thực thi.

**Mục đích:** Phát hiện sớm các lỗi cấu hình (script không tồn tại, thiếu quyền, tham số sai,...) trước khi gây hậu quả thật trên hệ thống.

---

## 5. Thực thi hành động (Act)

Sau khi vượt qua cả blast-radius và dry-run, orchestrator thực thi runbook thật:

### Hành động đơn lẻ (Single action)

Với các runbook loại `single` (restart, clear_cache, scale):

```bash
bash runbooks/restart_service.sh --service payment-svc
```

Code gọi `executor.safe_run()` (dòng 300) và kiểm tra kết quả:

- Exit code `0` → Ghi log `RUNBOOK_EXEC` với kết quả `ok`, tiến sang bước Verify.
- Exit code khác 0 → Ghi log `RUNBOOK_EXEC` với kết quả `failed`, kích hoạt rollback ngay.

### Hành động giao dịch (Transactional action)

Với các runbook loại `transaction` (ví dụ: `transactional_deploy`), hành động được chia thành nhiều bước tuần tự:

```yaml
transactional_deploy:
  steps: [prepare, switch, cleanup]
  rollback_steps: [switch, prepare]
```

Code trong `_run_transaction()` (dòng 323–337):

1. Chạy lần lượt từng bước: `prepare` → `switch` → `cleanup`.
2. Nếu bước nào thất bại → **dừng ngay** và rollback tất cả các bước đã hoàn thành **theo thứ tự ngược lại**.
3. Ví dụ: nếu `cleanup` thất bại → rollback `switch` trước, rồi rollback `prepare` sau.

---

## 6. Xác minh kết quả (Verify)

### Cơ chế hoạt động

Sau khi thực thi hành động, orchestrator **không tin ngay** rằng sự cố đã được giải quyết. Nó phải xác minh bằng cách hỏi Prometheus:

**Cấu hình verify:**

```yaml
verify:
  attempts: 3               # Lấy mẫu 3 lần
  window_seconds: 60         # Trong khoảng 60 giây
  success_threshold: 1.0     # Ngưỡng thành công: health score >= 1.0
```

### Luồng xử lý trong code (hàm `verify()`, dòng 362–376)

1. Orchestrator gửi PromQL query tới Prometheus để lấy `service_health_score`:
   ```
   avg_over_time(service_health_score{service="payment-svc"}[1m])
   ```
2. Lấy mẫu **3 lần** trong khoảng 60 giây (mỗi lần cách nhau ~20 giây).
3. Mỗi lần lấy mẫu, ghi log `VERIFY_SAMPLE` với giá trị đo được:

   ```json
   {"event_type": "VERIFY_SAMPLE", "service": "api", "attempt": 1, "value": 0.2, "result": "not_yet"}
   {"event_type": "VERIFY_SAMPLE", "service": "api", "attempt": 2, "value": 0.8, "result": "not_yet"}
   {"event_type": "VERIFY_SAMPLE", "service": "api", "attempt": 3, "value": 1.0, "result": "ok"}
   ```

4. **Quyết định cuối cùng** dựa trên **mẫu cuối cùng** (lần lấy mẫu thứ 3):
   - Nếu `value >= 1.0` → `VERIFY_PASS` → `ACTION_SUCCESS`.
   - Nếu `value < 1.0` → `VERIFY_FAIL` → Kích hoạt Rollback.

### Tại sao lấy mẫu 3 lần thay vì 1 lần?

- Service vừa restart có thể cần thời gian để khởi động (warm-up).
- Lần đầu có thể vẫn báo lỗi vì container chưa sẵn sàng.
- 3 lần lấy mẫu trong 60 giây cho service đủ thời gian phục hồi và ổn định.

---

## 7. Khôi phục khi thất bại (Rollback)

Rollback là cơ chế **tự động hoàn tác** hành động đã thực hiện khi kết quả không như mong đợi.

### Rollback đơn lẻ

Khi runbook thực thi thất bại hoặc verify thất bại, orchestrator gọi script rollback được cấu hình sẵn:

```yaml
restart_service:
  path: runbooks/restart_service.sh
  rollback: runbooks/rollback_restart.sh   # ← Script rollback tương ứng
```

Luồng trong code (`_rollback_single()`, dòng 352–360):

1. Ghi log `ROLLBACK_TRIGGERED` — báo hiệu bắt đầu rollback.
2. Gọi script rollback: `bash runbooks/rollback_restart.sh --service payment-svc`.
3. Ghi log `ROLLBACK_EXECUTED` với kết quả (thành công hay thất bại).

### Rollback giao dịch (Transactional Rollback)

Với các hành động nhiều bước, rollback phức tạp hơn — phải hoàn tác **theo thứ tự ngược lại**:

Luồng trong code (`_rollback_transaction()`, dòng 339–350):

1. Lấy danh sách các bước đã hoàn thành trước khi lỗi xảy ra.
2. **Đảo ngược** danh sách đó.
3. Chạy từng bước rollback theo thứ tự ngược:
   - Ví dụ: Nếu đã hoàn thành `prepare` → `switch`, và `cleanup` lỗi:
     - Rollback `switch` trước.
     - Rollback `prepare` sau.
4. Ghi log `TRANSACTIONAL_ROLLBACK_STEP` cho mỗi bước.
5. Khi hoàn tất, ghi log `TRANSACTIONAL_ROLLBACK_COMPLETE` với danh sách các bước đã rollback.

---

## 8. Bộ ngắt mạch (Circuit Breaker)

### Circuit Breaker là gì?

Circuit Breaker (bộ ngắt mạch) là một **design pattern** lấy cảm hứng từ cầu dao điện trong đời thực. Khi dòng điện quá tải, cầu dao tự động ngắt để bảo vệ thiết bị. Tương tự, khi orchestrator thất bại liên tục nhiều lần, circuit breaker sẽ **tự động dừng** toàn bộ quá trình tự động hóa để tránh gây hại thêm cho hệ thống.

### Tại sao cần Circuit Breaker?

Nếu một service bị lỗi nặng (ví dụ: lỗi phần cứng, lỗi cấu hình hệ thống), việc liên tục restart sẽ:

- Không giải quyết được vấn đề gốc rễ.
- Tạo thêm tải cho hệ thống (mỗi lần restart tiêu tốn tài nguyên).
- Có thể gây ra lỗi lan truyền (cascade failure) sang các service khác.

Circuit Breaker ngăn chặn vòng lặp "thất bại → retry → thất bại → retry → ..." vô tận này.

### Cách hoạt động trong code (class `CircuitBreaker`, dòng 104–121)

**Cấu hình:**

```yaml
circuit_breaker:
  max_consecutive_failures: 3   # Mở circuit sau 3 lần thất bại liên tiếp
```

**Trạng thái:**

| Trạng thái | Ý nghĩa |
|---|---|
| `CLOSED` (Đóng) | Hoạt động bình thường — orchestrator xử lý cảnh báo |
| `OPEN` (Mở) | Dừng hoàn toàn — orchestrator từ chối mọi hành động mới |

**Logic hoạt động:**

```python
class CircuitBreaker:
    def __init__(self, max_failures: int):
        self.max_failures = max_failures       # Ngưỡng: 3
        self.consecutive_failures = 0          # Bộ đếm lỗi liên tiếp
        self.open = False                      # Trạng thái ban đầu: CLOSED

    def record_success(self):
        self.consecutive_failures = 0          # Thành công → reset bộ đếm về 0

    def record_failure(self):
        self.consecutive_failures += 1         # Thất bại → tăng bộ đếm lên 1
        if self.consecutive_failures >= self.max_failures:
            self.open = True                   # Đạt ngưỡng → MỞ circuit
        return self.open
```

**Ví dụ minh họa:**

```
Lần 1: Restart payment-svc → Thất bại → consecutive_failures = 1 → CLOSED
Lần 2: Restart payment-svc → Thất bại → consecutive_failures = 2 → CLOSED
Lần 3: Restart payment-svc → Thất bại → consecutive_failures = 3 → OPEN ⚡
Lần 4: Cảnh báo mới đến → Orchestrator kiểm tra circuit.open == True → BỎ QUA
```

Khi circuit mở, log ghi nhận:

```json
{"event_type": "CIRCUIT_OPEN", "service": "inventory", "result": "halted", "consecutive_failures": 3}
{"event_type": "CIRCUIT_BREAKER_HALT", "service": "inventory", "result": "halted", "consecutive_failures": 3}
```

Các cảnh báo tiếp theo bị bỏ qua:

```json
{"event_type": "CIRCUIT_OPEN", "service": "inventory", "result": "skipped"}
```

### Reset Circuit Breaker

Việc reset được thực hiện **thủ công** qua hàm `reset_circuit(reason)`:

```python
def reset_circuit(self, reason: str):
    self.circuit.reset()                       # Đặt consecutive_failures = 0, open = False
    self.logger.emit("CIRCUIT_RESET", ...)     # Ghi log lý do reset
```

**Tại sao reset thủ công chứ không tự động?** Vì nếu nguyên nhân gốc rễ chưa được khắc phục, auto-reset sẽ khiến orchestrator lại tiếp tục thất bại và gây thêm hại. Chỉ nên reset sau khi đội on-call đã xác nhận nguyên nhân và sửa xong.

---

## 9. Giải thích từng Scenario

### Scenario 1 — Hành động thành công (Action succeeds)

**Mô phỏng:** Service `api` bị latency cao (500ms).

**Luồng xử lý:**

```
1. ALERT_DETECTED     → Phát hiện cảnh báo "HighLatency" trên service "api"
2. BLAST_RADIUS_PASS  → Chưa vượt giới hạn (< 4 actions/phút) → Cho phép
3. DRY_RUN_PASS       → Chạy thử restart_service.sh --dry-run → Thành công
4. RUNBOOK_EXEC       → Chạy thật restart_service.sh → Thành công
5. VERIFY_SAMPLE #1   → Health score = 0.2 → Chưa ổn
6. VERIFY_SAMPLE #2   → Health score = 0.8 → Đang phục hồi
7. VERIFY_SAMPLE #3   → Health score = 1.0 → Đã phục hồi hoàn toàn
8. VERIFY_PASS        → Xác minh thành công
9. ACTION_SUCCESS     → Kết thúc, ghi nhận thành công
```

**Điểm quan trọng:** Health score tăng dần từ 0.2 → 0.8 → 1.0 mô phỏng quá trình service khởi động lại và dần trở lại trạng thái bình thường.

---

### Scenario 2 — Hành động thất bại, kích hoạt Rollback

**Mô phỏng:** Service `checkout` bị chết hoàn toàn (container bị kill), restart không thể phục hồi.

**Luồng xử lý:**

```
1. ALERT_DETECTED      → Phát hiện "InstanceDown" trên "checkout"
2. BLAST_RADIUS_PASS   → Cho phép
3. DRY_RUN_PASS        → Chạy thử thành công (dry-run luôn thành công vì chỉ in)
4. RUNBOOK_EXEC        → Chạy thật restart_service.sh → THẤT BẠI (exit code 10)
5. ROLLBACK_TRIGGERED  → Kích hoạt rollback
6. ROLLBACK_EXECUTED   → Chạy rollback_restart.sh → Hoàn tất
7. FAILURE             → Ghi nhận thất bại, consecutive_failures = 1
```

**Điểm quan trọng:** 
- Biến môi trường `RUNBOOK_FORCE_FAIL=1` được sử dụng để buộc script restart thất bại (mô phỏng container không thể khởi động lại).
- Dù hành động thất bại, orchestrator vẫn chạy rollback để đưa hệ thống về trạng thái trước đó.
- Bộ đếm circuit breaker tăng lên 1.

---

### Scenario 3 — Circuit Breaker mở sau 3 lần thất bại liên tiếp

**Mô phỏng:** Service `inventory` bị lỗi nặng, restart liên tục 3 lần đều thất bại.

**Luồng xử lý:**

```
Lần 1:
  ALERT_DETECTED → DRY_RUN_PASS → RUNBOOK_EXEC (fail) → ROLLBACK → FAILURE (count=1)

Lần 2:
  ALERT_DETECTED → DRY_RUN_PASS → RUNBOOK_EXEC (fail) → ROLLBACK → FAILURE (count=2)

Lần 3:
  ALERT_DETECTED → DRY_RUN_PASS → RUNBOOK_EXEC (fail) → ROLLBACK → FAILURE (count=3)
  → CIRCUIT_OPEN ⚡
  → CIRCUIT_BREAKER_HALT

Lần 4 (cảnh báo mới):
  ALERT_DETECTED → CIRCUIT_OPEN (skipped) — Bị bỏ qua hoàn toàn!
```

**Điểm quan trọng:**
- Giới hạn blast-radius cho restart được tạm tăng lên (`max_restarts_per_service_per_hour = 10`) để cô lập hành vi circuit breaker (không bị chặn bởi blast-radius trước).
- Sau khi circuit mở, **mọi cảnh báo mới đều bị bỏ qua** — không có bất kỳ hành động nào được thực thi.

---

### Scenario 4 — Transactional Rollback (Khôi phục nhiều bước)

**Mô phỏng:** Một quá trình deploy gồm 3 bước (prepare → switch → cleanup), bước `cleanup` thất bại.

**Luồng xử lý:**

```
1. ALERT_DETECTED             → Phát hiện "BadDeploy" trên "frontend"
2. BLAST_RADIUS_PASS          → Cho phép
3. DRY_RUN_PASS               → Chạy thử thành công
4. TRANSACTIONAL_STEP_SUCCESS → Bước "prepare" thành công ✓
5. TRANSACTIONAL_STEP_SUCCESS → Bước "switch" thành công ✓
6. TRANSACTIONAL_STEP_FAIL    → Bước "cleanup" THẤT BẠI ✗
7. TRANSACTIONAL_ROLLBACK_STEP → Rollback "switch" (đảo ngược bước 2)
8. TRANSACTIONAL_ROLLBACK_STEP → Rollback "prepare" (đảo ngược bước 1)
9. TRANSACTIONAL_ROLLBACK_COMPLETE → Hoàn tất rollback: [switch, prepare]
10. FAILURE                   → Ghi nhận thất bại
```

**Điểm quan trọng:**
- Rollback chạy **theo thứ tự ngược lại**: `switch` trước rồi mới `prepare`.
- Đây giống nguyên lý **stack (LIFO — Last In, First Out)**: bước hoàn thành cuối cùng phải hoàn tác đầu tiên.
- Không có sự kiện `ACTION_SUCCESS` — một deploy lỗi không bao giờ được đánh dấu là thành công.

---

### Scenario 5 — Concurrent Alert Race (Xử lý cảnh báo đồng thời)

**Mô phỏng:** 2 cảnh báo đến cùng lúc — 1 trên service `api` (đang bận), 1 trên service `payment` (rảnh).

**Cơ chế Mutex (Per-service Lock):**

Mỗi service có một **lock riêng** (threading.Lock). Khi một runbook đang chạy trên service A, lock của A bị khóa. Nếu cảnh báo mới đến cho service A, nó không thể acquire lock → bị từ chối.

```python
self.service_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
```

**Luồng xử lý:**

```
Cảnh báo 1: HighLatency trên "api"
  → ALERT_DETECTED
  → Lock "api" đã bị khóa (đang xử lý)
  → SERVICE_LOCK_BUSY → Bỏ qua, KHÔNG xử lý

Cảnh báo 2: HighLatency trên "payment"
  → ALERT_DETECTED
  → Lock "payment" rảnh → Acquire thành công
  → BLAST_RADIUS_PASS → DRY_RUN_PASS → RUNBOOK_EXEC → VERIFY → ACTION_SUCCESS
```

**Điểm quan trọng:**
- 2 service **khác nhau** KHÔNG chặn lẫn nhau — `payment` vẫn chạy dù `api` đang bận.
- Cùng 1 service thì CÓ chặn — tránh chạy 2 runbook đồng thời trên cùng 1 service (có thể gây xung đột).
- `SERVICE_LOCK_BUSY` chỉ xuất hiện khi cùng service nhận cảnh báo thứ 2 trong lúc đang bận.

---

### Scenario 6 — LLM Hallucination Defense (Phòng chống AI ảo giác)

**Mô phỏng:** LLM gợi ý chạy `delete_database` — một hành động nguy hiểm và không nằm trong danh sách cho phép.

**Luồng xử lý:**

```
1. ALERT_DETECTED              → Phát hiện "HighLatency" trên "api"
2. Orchestrator ở chế độ LLM   → Nhận gợi ý: "delete_database"
3. Kiểm tra allowed_runbooks   → "delete_database" KHÔNG có trong danh sách
4. DECISION_VALIDATION_FAILED  → Từ chối gợi ý, ghi log đầy đủ:
   {
     "bad_runbook": "delete_database",
     "alertname": "HighLatency",
     "action": "escalate_no_auto_action",
     "reason": "llm_suggestion_not_allowlisted"
   }
```

**Điểm quan trọng:**
- **Không có** `DRY_RUN_PASS`, `RUNBOOK_EXEC`, hay bất kỳ subprocess nào được spawn.
- **Circuit breaker KHÔNG tăng** bộ đếm — vì đây là lỗi validation (xác thực), không phải lỗi thực thi. Nếu tăng, LLM ảo giác liên tục sẽ mở circuit và chặn luôn cả các xử lý hợp lệ.
- Hành động mặc định khi từ chối là `escalate_no_auto_action` — leo thang cho con người xử lý.

---

## 10. Câu hỏi mentor có thể hỏi và đáp án

### Câu 1: Tại sao em chọn engine dựa trên rule thay vì LLM?

**Trả lời:** Em chọn rule-based vì hệ thống closed-loop có quyền **thay đổi trực tiếp hệ thống production** (restart, scale, clear cache). Quyết định phải **xác định (deterministic)** — cùng một cảnh báo thì luôn ra cùng một hành động, không phụ thuộc vào "sáng tạo" của LLM. Rule-based cho phép:

- **Kiểm toán (audit):** Nhìn vào config.yaml là biết ngay cảnh báo nào dẫn đến hành động nào.
- **Không phụ thuộc mạng:** Không cần gọi API bên ngoài, orchestrator vẫn hoạt động khi mất internet.
- **Dự đoán được:** Trong tình huống khẩn cấp lúc 3 giờ sáng, đội on-call cần biết chắc hệ thống sẽ làm gì.

LLM được hỗ trợ nhưng chỉ là **gợi ý** — mọi gợi ý đều phải qua allowlist validation trước khi thực thi.

---

### Câu 2: Blast-radius hoạt động như thế nào? Tại sao chọn giá trị 4 actions/phút và 2 restarts/giờ?

**Trả lời:** Blast-radius dùng cơ chế **sliding window** (cửa sổ trượt thời gian) để giới hạn tần suất hành động:

- **4 actions/phút:** Hệ thống có 5 service. Nếu có sự cố ảnh hưởng đa service, 4 actions/phút cho phép xử lý hầu hết các service mà không tạo ra quá nhiều thay đổi cùng lúc. Nếu cảnh báo thứ 5 đến trong cùng phút, nó bị từ chối và leo thang cho con người.
- **2 restarts/service/giờ:** Nếu restart 2 lần mà service vẫn lỗi, rõ ràng nguyên nhân gốc rễ không phải do service cần restart (có thể do database, network, config,...). Tiếp tục restart chỉ tốn tài nguyên và có thể gây cascade failure.

Giá trị này không phải "magic number" — nó dựa trên đặc điểm hệ thống Ronki: 5 service, peak traffic 2 lần/ngày, mỗi incident ảnh hưởng trung bình 1-2 service.

---

### Câu 3: Verify step lấy mẫu 3 lần trong 60 giây — tại sao không lấy 1 lần hoặc 10 lần?

**Trả lời:**

- **1 lần không đủ** vì service vừa restart cần thời gian warm-up (nạp cache, thiết lập kết nối database, load config). Lấy mẫu ngay sau restart sẽ thấy health score thấp dù service đang phục hồi bình thường.
- **10 lần quá nhiều** vì kéo dài thời gian xác minh (có thể 5-10 phút), trong thời gian đó service có thể lại bị lỗi khác hoặc cảnh báo chất đống. Orchestrator bị "block" quá lâu trên 1 service.
- **3 lần trong 60 giây** (mỗi lần cách nhau ~20 giây) là đủ để service hoàn tất warm-up và cho thấy xu hướng phục hồi. Trong scenario 1, giá trị tăng dần 0.2 → 0.8 → 1.0 cho thấy đúng pattern phục hồi.

Quyết định cuối cùng dựa trên **mẫu cuối cùng** (lần thứ 3), không phải trung bình, vì em muốn xác nhận service đã **ổn định** ở thời điểm gần nhất.

---

### Câu 4: Circuit breaker reset thủ công hay tự động? Tại sao?

**Trả lời:** Em chọn reset **thủ công** thông qua hàm `reset_circuit(reason)`. Lý do:

- **An toàn hơn:** Nếu auto-reset sau 5 phút, và nguyên nhân gốc rễ chưa được sửa, orchestrator sẽ lại thực hiện 3 lần restart → lại mở circuit → lại reset → vòng lặp vô tận. Mỗi vòng lặp đều tiêu tốn tài nguyên và có thể gây thêm hại.
- **Quy trình chuẩn:** Reset chỉ nên xảy ra **sau khi đội on-call đã:**
  1. Xác nhận nguyên nhân gốc rễ (root cause).
  2. Xác nhận service đã ổn định.
  3. Kiểm tra các bộ đếm blast-radius đã hạ nhiệt.
  4. Sửa hoặc vô hiệu hóa runbook bị lỗi (nếu lỗi do runbook).
- **Ghi nhận lý do:** Hàm `reset_circuit(reason)` bắt buộc phải truyền lý do reset, tạo audit trail rõ ràng.

---

### Câu 5: Dry-run có ý nghĩa gì? Nếu dry-run pass nhưng run thật lại fail thì sao?

**Trả lời:** Dry-run là bước **"thử trên giấy"** trước khi thực thi thật. Nó kiểm tra:

- Script runbook có tồn tại không.
- Tham số truyền vào có đúng format không.
- Quyền thực thi script có đủ không.
- Logic cơ bản của script có hoạt động không.

**Khi dry-run pass nhưng run thật fail** (rất có thể xảy ra trong thực tế): đây là lý do em có thêm bước **Verify** và **Rollback** sau khi thực thi. Dry-run chỉ là tuyến phòng thủ đầu tiên, không phải duy nhất. Hệ thống có **phòng thủ nhiều lớp (defense in depth)**:

1. Dry-run → bắt lỗi cấu hình.
2. Blast-radius → giới hạn phạm vi ảnh hưởng.
3. Verify → kiểm tra kết quả thực tế.
4. Rollback → hoàn tác nếu lỗi.
5. Circuit breaker → dừng hoàn toàn nếu lỗi lặp lại.

---

### Câu 6: Nếu 2 cảnh báo khác nhau đến cùng lúc trên cùng 1 service, hệ thống xử lý thế nào?

**Trả lời:** Hệ thống sử dụng **per-service mutex** (`threading.Lock` cho mỗi service). Khi cảnh báo đầu tiên được xử lý, lock của service đó bị khóa. Cảnh báo thứ 2 trên cùng service sẽ gọi `lock.acquire(blocking=False)` — trả về `False` ngay lập tức (không chờ) → ghi log `SERVICE_LOCK_BUSY` và bỏ qua.

**Tại sao không queue lại cảnh báo thứ 2?** Vì:

- Nếu hành động đầu tiên thành công, cảnh báo thứ 2 có thể tự hết (Alertmanager sẽ resolve nó).
- Nếu hành động đầu tiên thất bại, cảnh báo thứ 2 sẽ xuất hiện lại ở vòng poll tiếp theo (15 giây sau) và được xử lý lúc đó.
- Queue cảnh báo tạo thêm complexity và risk: nếu queue quá dài, orchestrator bị backlog.

**Quan trọng:** Lock chỉ áp dụng **per-service**. Hai service khác nhau (`api` và `payment`) có lock riêng biệt và xử lý song song hoàn toàn bình thường.

---

### Câu 7: `DECISION_VALIDATION_FAILED` có làm tăng bộ đếm circuit breaker không? Tại sao?

**Trả lời:** **Không.** Đây là quyết định thiết kế có chủ đích.

Circuit breaker chỉ đếm **lỗi thực thi** (action fail hoặc verify fail) — tức là khi orchestrator đã thực sự thay đổi hệ thống nhưng kết quả không tốt.

`DECISION_VALIDATION_FAILED` là **lỗi xác thực** — orchestrator từ chối ngay từ đầu, chưa thực hiện bất kỳ thay đổi nào. Nếu tính lỗi này vào circuit breaker:

- LLM ảo giác liên tục 3 lần → circuit mở → chặn luôn cả các cảnh báo hợp lệ khác.
- Hacker có thể cố ý gửi cảnh báo giả để trigger validation failure liên tục, khiến circuit mở và vô hiệu hóa orchestrator (denial of service).

---

### Câu 8: Transactional rollback khác gì rollback đơn lẻ? Khi nào cần dùng transactional?

**Trả lời:**

| | Rollback đơn lẻ | Transactional rollback |
|---|---|---|
| **Khi nào dùng** | Hành động 1 bước (restart, clear cache) | Hành động nhiều bước (deploy, migration) |
| **Cách rollback** | Gọi 1 script rollback duy nhất | Hoàn tác từng bước theo thứ tự ngược |
| **Ví dụ** | `rollback_restart.sh` | Rollback `switch` → rollback `prepare` |
| **Đảm bảo** | Service quay về trạng thái trước | Toàn bộ tiến trình quay về trạng thái trước |

Transactional rollback cần dùng khi hành động có **tính phụ thuộc tuần tự**: bước 2 phụ thuộc vào kết quả bước 1. Nếu bước 3 lỗi mà chỉ rollback bước 3, hệ thống ở trạng thái "nửa chừng" — đã prepare và switch nhưng chưa cleanup. Trạng thái này rất nguy hiểm. Transactional rollback đảm bảo **all-or-nothing**: hoặc tất cả thành công, hoặc quay về hoàn toàn.

---

### Câu 9: Trong production thật, em sẽ thay đổi gì so với lab này?

**Trả lời:** Em sẽ thay đổi hoặc bổ sung:

1. **Thêm notification:** Khi circuit breaker mở hoặc rollback xảy ra, gửi thông báo qua Slack/PagerDuty cho đội on-call.
2. **Persistent state:** Lưu trạng thái circuit breaker và blast-radius counters vào Redis/database thay vì in-memory (nếu orchestrator restart thì mất hết state).
3. **Webhook thay cho polling:** Cấu hình Alertmanager gửi webhook để giảm độ trễ phát hiện từ 15 giây xuống gần real-time.
4. **Canary rollout:** Thay vì restart toàn bộ, restart dần 1 instance trước, verify ổn rồi mới restart các instance còn lại.
5. **Audit trail tập trung:** Gửi log sang hệ thống tập trung (ELK/Loki) thay vì chỉ ghi file local.
6. **Auto-reset có điều kiện:** Cho phép circuit breaker tự reset **nếu** metric đã ổn định liên tục trong 30 phút (kết hợp cả thủ công và tự động).
7. **Rate limiting theo severity:** Cảnh báo `critical` có giới hạn blast-radius khác với `warning`.

---

### Câu 10: Em có thể giải thích sự khác nhau giữa "closed-loop" và "open-loop" remediation không?

**Trả lời:**

| | Open-loop | Closed-loop |
|---|---|---|
| **Cách hoạt động** | Nhận cảnh báo → thực hiện hành động → xong | Nhận cảnh báo → thực hiện → **xác minh** → phản hồi |
| **Có feedback không?** | ❌ Không | ✅ Có (verify step) |
| **Biết hành động có hiệu quả không?** | Không biết — "bắn và quên" | Biết — kiểm tra metric sau khi thực hiện |
| **Xử lý khi thất bại** | Không xử lý | Tự động rollback |
| **Tự bảo vệ** | Không | Có (circuit breaker, blast-radius) |
| **Ví dụ đời thực** | Cron job restart lúc 2h sáng mỗi ngày | Orchestrator trong lab này |

Closed-loop giống **lái xe có mắt**: em nhìn đường (verify), nếu đi sai thì quay lại (rollback), nếu đường quá nguy hiểm thì dừng xe (circuit breaker). Open-loop giống **lái xe bịt mắt**: đạp ga rồi hy vọng không đâm vào đâu.

Trong AIOps, closed-loop là tiêu chuẩn vì hệ thống production quá phức tạp để "bắn và quên". Mỗi hành động đều có thể gây ra side-effect không lường trước, nên **phải** có feedback loop để kiểm tra và phản ứng.
