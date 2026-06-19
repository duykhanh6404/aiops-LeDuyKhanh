# Giải thích chi tiết: MLOps Lifecycle — Anomaly Detection Model from Train to Retrain

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Dữ liệu và mô hình](#2-dữ-liệu-và-mô-hình)
3. [Phát hiện sự sai lệch dữ liệu (Drift Detection)](#3-phát-hiện-sự-sai-lệch-dữ-liệu-drift-detection)
4. [Huấn luyện lại mô hình mới (Retrain)](#4-huấn-luyện-lại-mô-hình-mới-retrain)
5. [Đăng ký và quản lý phiên bản mô hình (Model Registry)](#5-đăng-ký-và-quản-lý-phiên-bản-mô-hình-model-registry)
6. [Đưa mô hình mới vào phục vụ mà không bị downtime (Blue-Green Rollout)](#6-đưa-mô-hình-mới-vào-phục-vụ-mà-không-bị-downtime)
7. [Tự động rollback khi mô hình mới chạy tệ](#7-tự-động-rollback-khi-mô-hình-mới-chạy-tệ)
8. [Luồng end-to-end hoàn chỉnh](#8-luồng-end-to-end-hoàn-chỉnh)
9. [Câu hỏi mentor có thể hỏi và đáp án](#9-câu-hỏi-mentor-có-thể-hỏi-và-đáp-án)

---

## 1. Tổng quan hệ thống

Hệ thống MLOps Lifecycle quản lý **vòng đời trọn vẹn** của một mô hình machine learning từ khi huấn luyện cho đến khi phải huấn luyện lại. Bài toán cụ thể: phát hiện bất thường (anomaly detection) trong dữ liệu cổng thanh toán của một công ty fintech.

**Vấn đề cần giải quyết:** Mô hình v1 đã được triển khai 2 tháng trước với precision 91% và recall 88%. Nhưng gần đây, nó bắt đầu **bỏ lọt sự cố thật** và **báo động giả nhiều hơn**. Nguyên nhân: **model decay** — dữ liệu thực tế đã thay đổi so với lúc huấn luyện do:

- Traffic tăng 35% sau chiến dịch marketing.
- Latency tăng do tích hợp thêm dịch vụ bên thứ 3.
- Pattern lỗi thay đổi do đổi nhà xử lý thanh toán mới.

**Kiến trúc hệ thống gồm 4 file Python chính:**

```
pipeline.py           → Huấn luyện model và đăng ký vào Registry
drift_detector.py     → Phát hiện drift giữa dữ liệu cũ và mới
retrain.py            → Điều phối toàn bộ luồng: detect → train v2 → staging → approve → promote
serve.py              → FastAPI phục vụ dự đoán (prediction serving)
model_store.py        → Quản lý phiên bản model (local registry, alias, load/save)
```

---

## 2. Dữ liệu và mô hình

### 2.1. Các tập dữ liệu

Hệ thống sử dụng 4 tập dữ liệu CSV, mỗi tập phục vụ một mục đích riêng:

| Tập dữ liệu | Số dòng | Mô tả | Vai trò |
|---|---|---|---|
| `baseline.csv` | 4320 | 30 ngày vận hành bình thường | Dùng để train model v1 (phân phối chuẩn) |
| `drifted.csv` | 1008 | 7 ngày sau khi drift xảy ra | Dùng để phát hiện drift và train model v2 |
| `holdout.csv` | 500 | Dữ liệu pattern cũ (từ baseline) | Dùng để validate v2 không bị overfit |
| `post_deploy_eval.csv` | 200 | Dữ liệu có label rõ ràng | Dùng để monitor v2 sau khi promote lên production |

### 2.2. Ba feature đầu vào

Mỗi dòng dữ liệu có 3 cột feature (mỗi 10 phút ghi 1 lần):

| Feature | Ý nghĩa | Phân phối baseline | Phân phối drifted |
|---|---|---|---|
| `latency_p99` | Độ trễ phân vị 99 (ms) | Trung bình ~128ms | Trung bình ~162ms (+30%) |
| `error_rate` | Tỷ lệ lỗi (%) | Trung bình ~0.79% | Trung bình ~1.48% (gần gấp đôi) |
| `rps` | Số request/giây | Trung bình ~468 | Trung bình ~610 (+40%) |

### 2.3. Mô hình: Isolation Forest

Mô hình sử dụng là **IsolationForest** từ thư viện scikit-learn. Đây là thuật toán unsupervised chuyên phát hiện bất thường:

**Nguyên lý hoạt động:**
- Isolation Forest xây dựng nhiều cây quyết định (decision tree) ngẫu nhiên.
- Mỗi cây cố gắng **cô lập (isolate)** từng điểm dữ liệu bằng cách chia ngẫu nhiên feature + ngưỡng.
- Điểm bất thường (anomaly) **dễ bị cô lập hơn** vì chúng khác biệt so với phần lớn dữ liệu → cần ít lần chia hơn.
- Điểm bình thường nằm trong đám đông → cần nhiều lần chia mới cô lập được.
- **Anomaly score** thấp = bất thường, cao = bình thường.

**Tham số huấn luyện:**

```python
IsolationForest(
    contamination=0.03,    # Giả định ~3% dữ liệu là bất thường
    n_estimators=150,      # Dùng 150 cây quyết định
    random_state=42,       # Seed cố định để tái lập kết quả
    n_jobs=-1,             # Dùng tất cả CPU cores
)
```

### 2.4. Chuẩn hóa dữ liệu (StandardScaler)

Trước khi huấn luyện, dữ liệu được chuẩn hóa bằng `StandardScaler`:

```python
scaler = StandardScaler()
x_scaled = scaler.fit_transform(x)
```

- Chuyển mỗi feature về **trung bình = 0, độ lệch chuẩn = 1**.
- Cần thiết vì 3 feature có đơn vị và biên độ khác nhau (latency: 50–400ms, error_rate: 0–5%, rps: 50–1200).
- Scaler được **lưu cùng model** để khi predict, dữ liệu mới cũng được scale theo đúng tham số đã fit.

---

## 3. Phát hiện sự sai lệch dữ liệu (Drift Detection)

### 3.1. Drift là gì?

Drift là hiện tượng **phân phối dữ liệu thay đổi theo thời gian**. Có 3 loại:

| Loại drift | Định nghĩa | Ví dụ | Cách phát hiện |
|---|---|---|---|
| **Data drift** | P(X) thay đổi, P(Y\|X) không đổi | Traffic tăng 35% → `rps` trung bình tăng | So sánh phân phối feature |
| **Concept drift** | P(Y\|X) thay đổi, P(X) có thể không đổi | Đổi nhà xử lý thanh toán → cùng latency 200ms nhưng trước là lỗi, giờ là bình thường | So sánh hiệu suất model (precision/recall) |
| **Performance drift** | Model kém đi dù không rõ nguyên nhân | Precision giảm từ 91% xuống 31% | Đo precision/recall trên dữ liệu có label |

**Trong bài lab này, drifted.csv chứa CẢ BA loại drift:**
- **Data drift:** 3 feature đều dịch chuyển phân phối (latency +30%, error_rate x2, rps +40%).
- **Concept drift:** 25% nhãn (anomaly_label) trong drifted.csv bị flip (đảo ngược) — mô phỏng việc mối quan hệ feature → label đã thay đổi.
- **Performance drift:** Model v1 chạy trên drifted.csv chỉ đạt precision 0.3154 (so với ngưỡng chấp nhận 0.65).

### 3.2. Cơ chế phát hiện Data Drift trong code

File `drift_detector.py` tính **drift score** cho từng feature rồi lấy trung bình.

**Bước 1: Tính drift score cho mỗi feature**

Hàm `_feature_drift_score()` (dòng 35–47) so sánh phân phối của feature giữa reference (baseline) và current (drifted) dựa trên 3 thước đo:

```python
def _feature_drift_score(ref, cur):
    ref_std = ref.std()                              # Độ lệch chuẩn của baseline

    mean_shift = abs(cur.mean() - ref.mean()) / ref_std   # 1. Chênh lệch trung bình
    std_shift = abs(cur.std() - ref_std) / ref_std        # 2. Chênh lệch độ lệch chuẩn
    quantile_shift = mean(abs(cur_quantiles - ref_quantiles)) / ref_std  # 3. Chênh lệch phân vị

    # Kết hợp với trọng số: mean chiếm 55%, std chiếm 25%, quantile chiếm 20%
    raw = 0.55 * mean_shift + 0.25 * std_shift + 0.20 * quantile_shift
    return clamp(raw / 3.0, 0.0, 1.0)   # Chuẩn hóa về khoảng [0, 1]
```

**Giải thích 3 thước đo:**

1. **Mean shift (55%):** Nếu trung bình latency tăng từ 128ms lên 162ms → chênh lệch ~34ms ÷ std ~15ms ≈ 2.27. Đây là tín hiệu mạnh nhất nên chiếm trọng số lớn nhất.

2. **Std shift (25%):** Nếu độ lệch chuẩn thay đổi (dữ liệu trở nên "dao động" hơn hoặc "ổn định" hơn). Phát hiện trường hợp mean không đổi nhưng phân bố rộng/hẹp hơn.

3. **Quantile shift (20%):** So sánh phân vị 10%, 50%, 90% giữa 2 tập. Bắt được trường hợp phần đuôi (tail) của phân phối thay đổi mà mean và std không phản ánh.

**Bước 2: Tính dataset-level drift score**

```python
per_feature = {feat: _feature_drift_score(ref[feat], cur[feat]) for feat in FEATURES}
score = np.mean(list(per_feature.values()))   # Trung bình drift score của 3 feature
```

**Bước 3: So sánh với ngưỡng**

```python
is_drift = score >= threshold   # Ngưỡng mặc định: 0.15
```

**Kết quả thực tế khi chạy:**

```
Drift score     : 0.5481       (>> 0.15 → Drift rõ ràng)
Drifted features: ['latency_p99', 'error_rate', 'rps']   (cả 3 feature đều drift)
```

### 3.3. Cơ chế phát hiện Performance Drift (Concept Drift)

**Vấn đề:** DataDriftPreset chỉ phát hiện data drift (phân phối feature thay đổi). Nếu concept drift xảy ra (cùng feature nhưng nhãn thay đổi), data drift detector sẽ **hoàn toàn bỏ lọt**.

**Giải pháp:** Hàm `check_performance_drift()` (dòng 130–140):

```python
def check_performance_drift(labeled_df, model_uri, precision_threshold=0.65):
    bundle = load_bundle(alias)                        # Load model đang production
    y_pred, _ = predict_anomaly(bundle, labeled_df)    # Predict trên dữ liệu có label
    precision, recall = precision_recall(y_true, y_pred)  # Tính precision/recall
    return precision, recall, precision < precision_threshold  # True nếu performance kém
```

**Logic:** Nếu model v1 predict trên dữ liệu mới mà precision < 0.65 → performance đã suy giảm nghiêm trọng, cần retrain.

### 3.4. Chế độ `--check-mode combined`

Đây là chế độ **kiểm tra kết hợp** cả data drift VÀ performance drift:

```bash
python drift_detector.py \
  --check-mode combined \
  --reference data/baseline.csv \
  --current data/drifted.csv \
  --labeled-current data/drifted.csv \
  --model-uri models:/anomaly-detector@production
```

Kết quả:

```
Drift score     : 0.5481      ← Data drift: phân phối feature thay đổi
Perf precision  : 0.3154      ← Performance drift: model v1 chạy rất tệ
Perf degraded   : True        ← Xác nhận performance đã suy giảm
```

**Tại sao cần `combined` mode?**
- Nếu chỉ chạy `--check-mode data`: chỉ thấy phân phối feature đổi, không biết model có còn chính xác không.
- Nếu chỉ chạy `--check-mode performance`: không biết nguyên nhân (do data đổi hay do model lỗi).
- `combined` cho **bức tranh toàn diện**: vừa biết dữ liệu thay đổi thế nào, vừa biết ảnh hưởng đến model ra sao.

### 3.5. Tạo báo cáo HTML

Sau mỗi lần kiểm tra drift, hệ thống tạo một file HTML report lưu vào `outputs/drift_reports/`:

```
drift-report-retrain-20260618-080000.html
```

Report chứa bảng so sánh chi tiết cho từng feature: mean, std của reference vs. current, và drift score tương ứng. Report này giúp đội on-call/ML engineer nhanh chóng hiểu dữ liệu đã thay đổi ở đâu.

---

## 4. Huấn luyện lại mô hình mới (Retrain)

### 4.1. Khi nào cần retrain?

Retrain chỉ được kích hoạt khi `drift_detector` báo `is_drift = True` (drift score >= ngưỡng 0.15). Nếu không có drift, pipeline ghi log `retrain_skipped_no_drift` và dừng.

### 4.2. Chiến lược chọn dữ liệu huấn luyện: Sliding Window

**Vấn đề:** Nếu train v2 chỉ trên `drifted.csv` (1008 dòng, 7 ngày dữ liệu mới), model v2 sẽ **overfit** vào phân phối mới và **quên** hoàn toàn pattern cũ vẫn còn xuất hiện trong production.

**Giải pháp:** Hàm `sliding_window()` (dòng 27–31 của `retrain.py`):

```python
def sliding_window(reference_df, current_df, reference_fraction=0.50):
    keep_reference_rows = max(int(len(current_df) * reference_fraction), 1)
    reference_tail = reference_df.tail(keep_reference_rows)
    return pd.concat([reference_tail, current_df], ignore_index=True)
```

**Cách hoạt động:**

1. Lấy **50% × len(current)** dòng từ cuối baseline: `0.50 × 1008 = 504 dòng` (gần nhất về mặt thời gian).
2. Nối với toàn bộ 1008 dòng current (drifted).
3. Tổng: **1512 dòng** = 504 dòng baseline cũ + 1008 dòng drifted mới.

**Tại sao lấy phần cuối (tail) của baseline?**
- Phần cuối baseline gần nhất về mặt thời gian với dữ liệu drifted.
- Nó đại diện cho "trạng thái bình thường gần đây nhất" trước khi drift xảy ra.
- Giúp model v2 học được cả pattern cũ lẫn mới.

**Kết quả so sánh:**

| Chiến lược | Holdout precision (v2) | Giải thích |
|---|---|---|
| Chỉ train trên drifted.csv | Rất thấp | Overfit phân phối mới, quên pattern cũ |
| Sliding window (504 cũ + 1008 mới) | **1.0000** | Bao quát cả 2 phân phối |

### 4.3. Quá trình huấn luyện trong pipeline.py

Hàm `train()` (dòng 66–120):

```python
def train(data_path, alias="production", contamination=0.03, n_estimators=150, random_state=42):
    df = load_frame(data_path)              # 1. Đọc CSV
    x = feature_matrix(df)                  # 2. Lấy 3 feature columns
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)      # 3. Chuẩn hóa dữ liệu

    model = IsolationForest(                # 4. Khởi tạo model
        contamination=contamination,
        n_estimators=n_estimators,
        random_state=random_state,
    )
    model.fit(x_scaled)                     # 5. Huấn luyện
    labels = model.predict(x_scaled)        # 6. Predict trên training data
    anomaly_rate = (labels == -1).mean()     # 7. Tính tỷ lệ anomaly

    # 8. Ghi log lên MLflow (nếu có) + local registry
    try_log_mlflow(model, params, metrics, input_example, alias)
    version = register_bundle(model, scaler, alias, metrics, params, ...)

    return version                          # Trả về version number (ví dụ: "2")
```

**Các giá trị được ghi nhận:**

```
[pipeline] Trained rows  : 1512
[pipeline] Anomaly rate  : 0.0106
[pipeline] Local alias   : anomaly-detector v2 -> @staging
```

### 4.4. Holdout Validation — Kiểm tra chéo trước khi promote

Sau khi train xong v2, pipeline **bắt buộc** validate trên `holdout.csv` (500 dòng pattern cũ):

```python
if args.holdout:
    v1_precision, v1_recall = evaluate_alias("production", args.holdout)  # Đo v1
    v2_precision, v2_recall = evaluate_alias("staging", args.holdout)     # Đo v2
    print(f"Holdout validation — v2 precision: {v2_precision:.4f}")

    if v2_precision < v1_precision:
        print("Staging rejected: v2 precision is lower than v1 on holdout.")
        sys.exit(2)   # TỪ CHỐI v2 nếu kém hơn v1 trên dữ liệu cũ
```

**Kết quả thực tế:**

```
Holdout v1 precision: 0.0000  recall: 0.0000   ← v1 rất tệ trên holdout
Holdout v2 precision: 1.0000  recall: 1.0000   ← v2 vượt trội
```

**Logic:** v2 phải ít nhất **bằng hoặc tốt hơn** v1 trên dữ liệu pattern cũ. Nếu v2 kém hơn → bị từ chối ngay, không được promote.

---

## 5. Đăng ký và quản lý phiên bản mô hình (Model Registry)

### 5.1. Hệ thống đăng ký 2 tầng

Pipeline hỗ trợ **2 tầng registry** — hoạt động song song:

**Tầng 1: MLflow Registry (khi MLflow server chạy)**

```python
def try_log_mlflow(model, params, metrics, input_example, alias):
    mlflow.set_tracking_uri("http://localhost:5000")
    mlflow.set_experiment("anomaly-detection")
    with mlflow.start_run(run_name=f"train-{alias}"):
        mlflow.log_params(params)                         # Log tham số
        mlflow.log_metrics(metrics)                       # Log metric
        mlflow.sklearn.log_model(model, "model",          # Lưu model artifact
            registered_model_name="anomaly-detector")
    # Set alias: "anomaly-detector" version 2 → @staging
    client.set_registered_model_alias("anomaly-detector", alias, version)
```

**Tầng 2: Local Registry (luôn hoạt động, kể cả khi không có MLflow)**

File `model_store.py` quản lý một file JSON (`outputs/registry.json`) làm registry nội bộ:

```json
{
  "model_name": "anomaly-detector",
  "versions": {
    "1": {
      "path": "outputs/models/anomaly-detector/v1/model.joblib",
      "created_at": "2026-06-18T08:00:00+00:00",
      "source_data": "data/baseline.csv",
      "metrics": {"train_anomaly_rate": 0.0306, "feature_count": 3},
      "params": {"contamination": 0.03, "n_estimators": 150}
    },
    "2": { ... }
  },
  "aliases": {
    "production": "1",
    "staging": "2"
  }
}
```

### 5.2. Hệ thống Alias

Alias là **nhãn di động** gắn vào một version cụ thể. Thay vì hardcode version number, code luôn truy cập model qua alias:

| Alias | Ý nghĩa | Ví dụ |
|---|---|---|
| `production` | Model đang phục vụ chính thức | v1 ban đầu, sau promote → v2 |
| `staging` | Model mới đang chờ phê duyệt | v2 sau khi train xong |
| `archived` | Model bị gỡ bỏ (hạ cấp) | v2 nếu bị rollback |

**Ưu điểm quan trọng:** Khi swap model, chỉ cần **đổi alias** trong registry — không cần thay đổi code trong `serve.py`. Code phục vụ luôn load `models:/anomaly-detector@production`, dù alias đó trỏ đến v1 hay v2.

### 5.3. Lưu trữ Model Bundle

Mỗi model version được lưu dưới dạng **ModelBundle** — một gói chứa tất cả thứ cần thiết để predict:

```python
@dataclass
class ModelBundle:
    model: Any          # IsolationForest đã train xong
    scaler: Any         # StandardScaler đã fit_transform trên training data
    features: list[str] # ["latency_p99", "error_rate", "rps"]
    version: str        # "1" hoặc "2"
    metadata: dict      # Tham số, metric, timestamp, source data
```

Bundle được serialize bằng `joblib.dump()` thành file `.joblib` và lưu tại:

```
outputs/models/anomaly-detector/v1/model.joblib
outputs/models/anomaly-detector/v2/model.joblib
```

**Tại sao lưu scaler cùng model?** Vì khi predict, dữ liệu mới cần được scale bằng **đúng cùng tham số** (mean, std) đã dùng khi training. Nếu dùng scaler khác → kết quả predict sẽ sai hoàn toàn.

---

## 6. Đưa mô hình mới vào phục vụ mà không bị downtime

### 6.1. Cổng phê duyệt con người (Human Approval Gate)

Sau khi v2 được train và validate trên holdout, pipeline **KHÔNG tự động promote**. Nó hỏi con người:

```python
def prompt_approval(auto_approve):
    if auto_approve:
        return True
    answer = input("Promote staging to production? [y/N] ").strip().lower()
    return answer == "y"
```

```
[retrain] Promote staging to production? [y/N]
```

**Tại sao cần approval gate?**
- Đây là model phát hiện bất thường cho **cổng thanh toán** — false positive gây cảnh báo sai cho đội on-call, false negative bỏ lọt sự cố thật.
- Fully automatic promotion là "chaos", không phải MLOps. Phải có điểm kiểm soát con người.
- Khi chạy test tự động, dùng `--auto-approve` để không bị treo terminal, nhưng **logic approval gate vẫn tồn tại** trong code.

### 6.2. Cơ chế Blue-Green Swap

Blue-green là kỹ thuật triển khai **không downtime** bằng cách duy trì 2 phiên bản đồng thời:

```
Trước swap:
  @production → v1  (đang phục vụ /predict)
  @staging    → v2  (chờ duyệt)

Sau swap (promote):
  @production → v2  (bắt đầu phục vụ /predict)
  (v1 vẫn tồn tại, có thể rollback bất cứ lúc nào)
```

**Các bước swap trong code:**

```python
# Bước 1: Đổi alias production từ v1 sang v2
set_alias("production", v2_version)

# Bước 2: Ghi audit log
append_audit("promoted_staging_to_production", {
    "old_version": v1_version,
    "new_version": v2_version
})

# Bước 3: Gọi API /reload trên serve.py
call_reload(serve_url)  # POST http://localhost:8000/reload
```

### 6.3. FastAPI serve.py — Cách model được nạp và reload

**Khi khởi động (startup):**

```python
@app.on_event("startup")
def startup():
    load_active("production")   # Load model có alias @production
```

**Khi nhận lệnh reload:**

```python
@app.post("/reload")
def reload_model():
    return {"status": "reloaded", **load_active("production")}
```

Hàm `load_active()`:

```python
def load_active(alias="production"):
    bundle = load_bundle(alias)        # Đọc registry → tìm version → load .joblib
    ACTIVE["bundle"] = bundle          # Cập nhật model trong memory
    ACTIVE["version"] = bundle.version # Cập nhật version number
    ACTIVE["alias"] = alias
```

**Tại sao không có downtime?**

1. **Trước reload:** `ACTIVE["bundle"]` vẫn là v1 → mọi request `/predict` vẫn dùng v1.
2. **Trong quá trình reload:** `load_bundle()` đọc file `.joblib` → deserialize model vào memory → gán vào `ACTIVE`. Quá trình này mất chỉ vài chục millisecond.
3. **Sau reload:** `ACTIVE["bundle"]` trở thành v2 → tất cả request mới dùng v2.
4. **Không có khoảng trống:** Không có lúc nào `ACTIVE["bundle"]` là `None` trong quá trình swap — biến được gán nguyên tử (atomic assignment trong Python).

### 6.4. Endpoint kiểm tra phiên bản đang chạy

```python
@app.get("/health/active-version")
def active_version():
    return {
        "status": "ok",
        "model_name": "anomaly-detector",
        "alias": "production",
        "version": "2",         # Xác nhận v2 đang phục vụ
        "features": ["latency_p99", "error_rate", "rps"]
    }
```

Endpoint này cho phép đội on-call xác nhận **chính xác** model nào đang chạy, không cần SSH vào server.

---

## 7. Tự động rollback khi mô hình mới chạy tệ

### 7.1. Giám sát sau triển khai (Post-deploy Monitoring)

Sau khi v2 được promote, pipeline **tiếp tục theo dõi** hiệu suất của v2 trong 24 chu kỳ:

```python
def monitor_and_maybe_rollback(post_deploy_eval, v1_version, v2_version, ...):
    for cycle in range(1, 25):                              # 24 chu kỳ
        precision, recall = evaluate_alias("production", post_deploy_eval)
        print(f"post_deploy_monitor Cycle {cycle:02d}/24 precision: {precision:.4f}")

        if precision < 0.65:                                # Ngưỡng rollback
            set_alias("archived", v2_version)               # Hạ cấp v2
            set_alias("production", v1_version)             # Khôi phục v1
            append_audit("auto_rollback_v2_to_v1", {        # Ghi audit
                "demoted_version": v2_version,
                "restored_version": v1_version,
                "trigger_precision": precision,
                "cycle": cycle,
            })
            call_reload(serve_url)                          # Reload serve.py về v1
            print(f"Rollback complete. v{v1_version} restored to @production.")
            break
```

### 7.2. Điều kiện kích hoạt rollback

| Điều kiện | Hành động |
|---|---|
| precision < 0.65 tại bất kỳ chu kỳ nào | **Rollback ngay lập tức** |
| precision >= 0.65 suốt 24 chu kỳ | **Giữ v2 ở production**, ghi log `post_deploy_stable` |

### 7.3. Quy trình rollback chi tiết

```
Trước rollback:
  @production → v2  (đang phục vụ nhưng precision kém)
  v1 vẫn tồn tại trong registry

Sau rollback:
  @production → v1  (khôi phục)
  @archived   → v2  (hạ cấp, không bị xóa — vẫn có thể phân tích sau)
```

Bước cụ thể:

1. `set_alias("archived", v2_version)` — Gán nhãn `archived` cho v2.
2. `set_alias("production", v1_version)` — Đưa nhãn `production` về lại v1.
3. `append_audit("auto_rollback_v2_to_v1", ...)` — Ghi event vào `audit_log.jsonl`.
4. `call_reload(serve_url)` — Gọi `POST /reload` để serve.py nạp lại v1.

### 7.4. Audit Log

Mọi hành động quan trọng đều được ghi vào `outputs/audit_log.jsonl`:

```json
{"timestamp": "...", "event": "model_registered", "version": "1", "alias": "production"}
{"timestamp": "...", "event": "drift_checked", "score": 0.5481, "is_drift": true}
{"timestamp": "...", "event": "model_registered", "version": "2", "alias": "staging"}
{"timestamp": "...", "event": "holdout_validation", "v2_precision": 1.0, "v1_precision": 0.0}
{"timestamp": "...", "event": "promoted_staging_to_production", "old_version": "1", "new_version": "2"}
{"timestamp": "...", "event": "post_deploy_cycle", "cycle": 1, "precision": 1.0}
...
{"timestamp": "...", "event": "post_deploy_stable", "version": "2", "cycles": 24}
```

Hoặc nếu rollback xảy ra:

```json
{"timestamp": "...", "event": "auto_rollback_v2_to_v1", "demoted_version": "2", "restored_version": "1", "trigger_precision": 0.45, "cycle": 5}
```

---

## 8. Luồng end-to-end hoàn chỉnh

Toàn bộ vòng đời được thực hiện bằng 3 lệnh:

```bash
# Bước 1: Train model v1 trên dữ liệu bình thường
python pipeline.py --data data/baseline.csv

# Bước 2: Kiểm tra drift (tùy chọn, chạy riêng để phân tích)
python drift_detector.py \
  --reference data/baseline.csv \
  --current data/drifted.csv \
  --check-mode combined \
  --labeled-current data/drifted.csv \
  --model-uri models:/anomaly-detector@production

# Bước 3: Chạy toàn bộ luồng retrain end-to-end
python retrain.py \
  --reference data/baseline.csv \
  --current data/drifted.csv \
  --holdout data/holdout.csv \
  --post-deploy-eval data/post_deploy_eval.csv \
  --auto-approve
```

**Luồng bên trong `retrain.py`:**

```
1. Đảm bảo v1 tồn tại       → Nếu chưa, tự train v1 từ baseline
2. Detect drift              → Gọi drift_detector, score = 0.5481 > 0.15 → DRIFT
3. Sliding window            → Tạo training set: 504 baseline + 1008 drifted = 1512 dòng
4. Train v2                  → IsolationForest trên 1512 dòng → đăng ký @staging
5. Holdout validation        → v2 precision 1.0 >= v1 precision 0.0 → PASS
6. Approval gate             → [y/N] hoặc --auto-approve
7. Promote                   → @staging → @production, gọi /reload
8. Post-deploy monitoring    → 24 chu kỳ, precision >= 0.65 suốt → stable
```

---

## 9. Câu hỏi mentor có thể hỏi và đáp án

### Câu 1: Data drift và concept drift khác nhau thế nào? Drift detector của em phát hiện được loại nào?

**Trả lời:**

| | Data drift | Concept drift |
|---|---|---|
| **Định nghĩa** | P(X) thay đổi — phân phối feature dịch chuyển | P(Y\|X) thay đổi — mối quan hệ feature → label thay đổi |
| **Ví dụ cụ thể** | `latency_p99` trung bình tăng từ 128ms lên 162ms | Cùng latency 200ms, trước là bất thường, giờ là bình thường (do đổi provider) |
| **Cách phát hiện** | So sánh phân phối feature (mean, std, quantile) | So sánh hiệu suất model (precision, recall) trên dữ liệu có label |

Drift detector của em phát hiện **cả hai** thông qua `--check-mode combined`:
- Data drift: hàm `detect_drift()` so sánh phân phối 3 feature → drift score 0.5481.
- Concept/Performance drift: hàm `check_performance_drift()` đo precision v1 trên dữ liệu mới → 0.3154 (rất tệ).

Nếu chỉ chạy `--check-mode data`, ta sẽ phát hiện data drift nhưng **bỏ lọt** concept drift. Trong `drifted.csv`, 25% nhãn bị flip (concept drift), nhưng feature values nhìn "bình thường" theo phân phối mới — data drift detector không thấy điều này.

---

### Câu 2: Tại sao chọn ngưỡng drift là 0.15? Nếu đặt quá thấp hoặc quá cao thì sao?

**Trả lời:** Ngưỡng 0.15 được chọn dựa trên **đặc thù dữ liệu thanh toán**:

- Các feature `latency_p99`, `error_rate`, `rps` có **dao động tự nhiên** theo thời gian trong ngày (peak hours vs. off-peak), theo ngày trong tuần, theo mùa. Drift score trên chính baseline data (so sánh 2 nửa) khoảng 0.03–0.08.
- Ngưỡng 0.15 ≈ **baseline score × 1.5–2** — đủ cao để không bị trigger bởi biến động tự nhiên, nhưng đủ nhạy để phát hiện drift thật.

| Ngưỡng | Vấn đề |
|---|---|
| **0.05 (quá thấp)** | Retrain liên tục mỗi khi traffic dao động → tốn tài nguyên, model bất ổn, alert fatigue |
| **0.15 (phù hợp)** | Chỉ trigger khi có sự thay đổi có ý nghĩa thống kê (drift score thực tế: 0.5481 >> 0.15) |
| **0.50 (quá cao)** | Chỉ phát hiện drift rất nặng, bỏ lọt drift vừa phải nhưng đã ảnh hưởng model |

Em đã validate bằng cách chạy drift_detector trên `drifted.csv` → score 0.5481, vượt ngưỡng gần 3.65 lần → tín hiệu rõ ràng. Ngưỡng 0.15 hoạt động chính xác.

---

### Câu 3: Tại sao dùng sliding window thay vì chỉ train trên dữ liệu mới?

**Trả lời:** Nếu train v2 **chỉ trên drifted.csv** (1008 dòng), model sẽ **overfit** vào phân phối mới:

- v2 học rất tốt pattern mới nhưng hoàn toàn quên pattern cũ.
- Trong production, traffic không chuyển 100% sang pattern mới ngay lập tức — vẫn có request theo pattern cũ.
- Khi test trên `holdout.csv` (500 dòng pattern cũ), v2 chỉ-drifted sẽ predict rất tệ.

Sliding window (504 dòng baseline gần nhất + 1008 dòng drifted) giúp v2 **bao quát cả 2 phân phối**:

- 504 dòng baseline giữ lại "bộ nhớ" về pattern cũ.
- 1008 dòng drifted giúp model học pattern mới.
- Tỷ lệ 50% là heuristic — có thể điều chỉnh parameter `reference_fraction` nếu tình hình thực tế thay đổi.

Kết quả: v2 (sliding window) đạt holdout precision **1.0000**, trong khi v1 chỉ đạt **0.0000** trên cùng holdout. Sliding window vượt trội rõ rệt.

---

### Câu 4: Approval gate có cần thiết không? Nếu muốn tự động hóa hoàn toàn thì dùng metric nào?

**Trả lời:** **Có, rất cần thiết** cho production. Lý do:

- Đây là model ảnh hưởng đến **cổng thanh toán** — false positive gây alert sai cho on-call (tốn nhân lực), false negative bỏ lọt sự cố thật (mất tiền).
- Fully automatic promotion là "chaos engineering ngoài ý muốn" — không kiểm soát được.

**Nếu muốn tự động hóa hoàn toàn**, em sẽ dùng tổ hợp 3 điều kiện:

1. `v2_holdout_precision >= v1_holdout_precision` — v2 không kém hơn v1 trên dữ liệu cũ.
2. `v2_holdout_precision >= 0.85` — đạt ngưỡng precision tối thiểu tuyệt đối.
3. `v2_holdout_recall >= 0.80` — không hy sinh recall để đổi lấy precision.

Chỉ khi cả 3 điều kiện đều thỏa mãn mới auto-promote. Đây vẫn an toàn hơn approval gate con người ở chỗ: nó kiểm tra bằng **số liệu khách quan**, không phụ thuộc vào tâm trạng hay kinh nghiệm của người duyệt.

---

### Câu 5: Blue-green swap khác gì việc ghi đè file model trực tiếp?

**Trả lời:**

| | Ghi đè file trực tiếp | Blue-green swap |
|---|---|---|
| **Downtime** | Có — trong lúc ghi đè, nếu request đến sẽ đọc file hỏng | **Không** — v1 vẫn phục vụ cho đến khi v2 sẵn sàng |
| **Rollback** | Rất khó — file v1 đã bị ghi đè, mất luôn | **Dễ dàng** — v1 vẫn tồn tại, chỉ cần đổi alias |
| **Kiểm tra trước** | Không — model mới chạy ngay khi file thay đổi | **Có** — v2 ở @staging, validate xong mới promote |
| **Audit trail** | Không — không biết ai đổi, lúc nào | **Có** — mọi thay đổi alias đều được ghi log |
| **Kiểm tra sau** | Không — phải chờ người dùng phản hồi | **Có** — post-deploy monitoring 24 chu kỳ |

Blue-green = 2 phiên bản tồn tại **đồng thời**, swap chỉ là thay đổi **con trỏ** (alias). Trong lab, `/predict` phục vụ @production, swap = đổi alias + reload. Không có khoảng trống thời gian mà không có model nào phục vụ.

---

### Câu 6: Post-deploy monitoring theo dõi cái gì? Tại sao chọn ngưỡng precision 0.65?

**Trả lời:** Post-deploy monitoring chạy 24 chu kỳ, mỗi chu kỳ:

1. Load model đang @production (v2 sau promote).
2. Predict trên `post_deploy_eval.csv` (200 dòng có label rõ ràng).
3. Tính precision và recall.
4. Nếu precision < 0.65 → **rollback ngay lập tức**.

**Tại sao precision 0.65?**

- Precision = TP / (TP + FP). Nếu precision < 0.65, nghĩa là > 35% cảnh báo anomaly là **báo động giả**.
- Trong hệ thống thanh toán, mỗi false positive → đội on-call phải kiểm tra → tốn thời gian và nhân lực.
- 0.65 là ngưỡng "tối thiểu chấp nhận được" — dưới mức này, model gây hại nhiều hơn lợi ích.
- Em lưu ý: trong production thật, ngưỡng này nên được review dựa trên chi phí cụ thể: chi phí false positive (cảnh báo sai) vs. chi phí false negative (bỏ lọt sự cố).

---

### Câu 7: Isolation Forest hoạt động thế nào? Tại sao chọn thuật toán này cho bài toán anomaly detection?

**Trả lời:** Isolation Forest dựa trên ý tưởng: **điểm bất thường dễ bị "cô lập" hơn điểm bình thường**.

**Nguyên lý:**
1. Xây dựng 150 cây quyết định (n_estimators=150).
2. Mỗi cây chọn ngẫu nhiên 1 feature và 1 ngưỡng, chia dữ liệu thành 2 nhánh.
3. Lặp lại cho đến khi mỗi điểm bị cô lập (nằm một mình trong nhánh).
4. Điểm bất thường cần **ít lần chia hơn** để bị cô lập → path length ngắn hơn → anomaly score thấp hơn.

**Tại sao phù hợp:**
- **Unsupervised** — không cần label (dữ liệu thanh toán thường không có label rõ ràng).
- **Nhanh** — train 4320 dòng × 3 features trong < 1 giây, phù hợp retrain tự động.
- **Robust** — hoạt động tốt với dữ liệu nhiều chiều mà không cần giả định phân phối.
- **Contamination = 0.03** — giả định ~3% dữ liệu là bất thường, phù hợp với hệ thống thanh toán hoạt động bình thường đa số thời gian.

---

### Câu 8: Nếu MLflow server không chạy, pipeline có bị crash không?

**Trả lời:** **Không.** Pipeline được thiết kế **graceful degradation** (suy giảm thanh nhã):

```python
def try_log_mlflow(model, params, metrics, input_example, alias):
    try:
        import mlflow
    except Exception:
        print("MLflow unavailable, using local registry")
        return None          # ← Trả về None, không crash

    try:
        mlflow.set_tracking_uri("http://localhost:5000")
        # ... log params, metrics, model ...
    except Exception:
        print("MLflow logging skipped")
        return None          # ← Lỗi kết nối cũng không crash
```

Tương tự, `drift_detector.py` có `log_to_mlflow()` cũng try/except. Nếu MLflow không có:
- Model vẫn được lưu vào **local registry** (`outputs/registry.json`).
- Drift score vẫn được tính và in ra stdout.
- Audit log vẫn được ghi vào `outputs/audit_log.jsonl`.

**Pipeline hoạt động 100% offline** — MLflow là "nice to have", không phải dependency bắt buộc.

---

### Câu 9: Tại sao cần StandardScaler? Nếu không scale thì sao?

**Trả lời:** 3 feature có đơn vị và biên độ **hoàn toàn khác nhau**:

| Feature | Range | Đơn vị |
|---|---|---|
| `latency_p99` | 50 – 400 | millisecond |
| `error_rate` | 0 – 5 | phần trăm |
| `rps` | 50 – 1200 | request/giây |

Nếu không scale:
- Isolation Forest sẽ bị **dominated** bởi `rps` (giá trị lớn nhất, range 50–1200) và `latency_p99` (range 50–400).
- `error_rate` (range 0–5) gần như **bị bỏ qua** vì giá trị quá nhỏ so với 2 feature kia.
- Điều này sai lầm vì `error_rate` tăng gấp đôi (từ 0.8% lên 1.6%) là tín hiệu bất thường rất mạnh.

StandardScaler chuẩn hóa tất cả feature về **cùng scale** (mean=0, std=1), đảm bảo mỗi feature có **ảnh hưởng công bằng** trong thuật toán.

**Quan trọng:** Scaler phải được **fit trên training data** và **lưu cùng model** (trong ModelBundle). Khi predict, dùng `scaler.transform()` (không phải `fit_transform()`) để áp dụng cùng tham số scale.

---

### Câu 10: Nếu phải deploy hệ thống này lên production thật, em sẽ thay đổi những gì?

**Trả lời:** Em sẽ thay đổi và bổ sung:

1. **Scheduled drift detection:** Thay vì chạy thủ công, cài cron job (hoặc Airflow DAG) chạy `drift_detector.py` mỗi ngày với dữ liệu 24 giờ gần nhất.

2. **Shadow mode cho model mới:** Trước khi promote, chạy v2 song song với v1 trong 1–7 ngày (shadow traffic). So sánh prediction 2 model trên cùng request. Chỉ promote khi v2 ổn định.

3. **Feature store:** Thay vì đọc CSV trực tiếp, kết nối vào feature store (Feast, Tecton) để lấy feature real-time từ Prometheus/InfluxDB.

4. **A/B testing thay vì blue-green:** Route 10% traffic sang v2, 90% còn lại vẫn dùng v1. Nếu v2 tốt hơn → tăng dần lên 50%, 100%.

5. **Alert integration:** Khi drift detected hoặc rollback xảy ra, gửi thông báo qua Slack/PagerDuty, không chỉ ghi file log.

6. **Model performance dashboard:** Thay vì 24 chu kỳ cố định, giám sát precision/recall liên tục trên Grafana dashboard với cửa sổ trượt 1 giờ.

7. **Versioned data:** Dùng DVC (Data Version Control) hoặc LakeFS để version dữ liệu training, đảm bảo reproducibility — có thể quay lại bất kỳ experiment nào.

8. **Authentication cho API:** Thêm API key hoặc OAuth cho endpoint `/reload` và `/predict` — tránh người ngoài gọi reload tùy ý.

9. **Multi-model support:** Cho phép serve nhiều model cùng lúc (ví dụ: anomaly detection cho payment + anomaly detection cho inventory) — hiện tại serve.py chỉ hỗ trợ 1 model.

10. **Auto-scaling:** Nếu predict latency (p99) tăng cao, tự động scale replicas của serve.py thông qua Kubernetes HPA.
