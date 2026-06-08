# Assignment W1-D2: Log Mining + Parsing + Anomaly

## 1. Screenshots
![Template Count Time Series - Anomaly Highlighted](assets\Template-count-time-series.png)

## 2. Logs & Results
### 2.1. Tuning `drain_sim_th`
```text
Tuning results:
sim_th=0.3 -> 12 templates
sim_th=0.5 -> 21 templates
sim_th=0.7 -> 43 templates
```
**Lựa chọn:** Giá trị `sim_th = 0.5` cho ra kết quả cân bằng tốt nhất (21 templates), không gộp quá nhiều log khác biệt (0.3) và không tách nhỏ ra quá mức (0.7).

### 2.2. Output Drain3 (Top 10 Templates)
Tổng số templates unique: **21** (với `sim_th = 0.5`)
**Top-10 templates:**
```text
Top 1 (Count: 314, 15.70%): <*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: <*> is added to <*> size <*>
Top 2 (Count: 311, 15.55%): <*> <*> <*> INFO dfs.DataNode$PacketResponder: PacketResponder <*> for block <*> terminating
Top 3 (Count: 292, 14.60%): <*> <*> <*> INFO dfs.DataNode$PacketResponder: Received block <*> of size <*> from <*>
Top 4 (Count: 292, 14.60%): <*> <*> <*> INFO dfs.DataNode$DataXceiver: Receiving block <*> src: <*> dest: <*>
Top 5 (Count: 263, 13.15%): <*> <*> <*> INFO dfs.FSDataset: Deleting block <*> file <*>
Top 6 (Count: 154, 7.70%): <*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.allocateBlock: <*> <*>
Top 7 (Count: 122, 6.10%): <*> <*> <*> INFO dfs.DataNode$DataXceiver: <*> Served block <*> to <*>
Top 8 (Count: 88, 4.40%): <*> <*> <*> INFO dfs.DataBlockScanner: Verification succeeded for <*>
Top 9 (Count: 52, 2.60%): <*> <*> <*> INFO dfs.DataNode$PacketResponder: PacketResponder <*> for block <*> Interrupted.
Top 10 (Count: 31, 1.55%): <*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: Redundant addStoredBlock request received for <*> on <*> size <*>
```

## 3. Reflection

### 3.1. Drain3 parse có tốt không?
Drain3 xử lý log HDFS cực kỳ hiệu quả và có tốc độ nhanh. Với cấu trúc cây tìm kiếm (parse tree) cố định, thuật toán có thể linh hoạt gom nhóm các tham số động (dynamic parameters như Block ID, IP address, Port, Timestamp, Size) thành định dạng wildcard `<*>`. Quá trình này giúp rút gọn hàng ngàn, hàng triệu dòng log biến thiên liên tục về một tập nhỏ chỉ bao gồm vài chục mẫu template tĩnh, đồng thời vẫn bảo lưu được toàn bộ nội dung ngữ nghĩa (semantic format). Đây là kỹ thuật vô cùng tối ưu cho xử lý dữ liệu log khối lượng lớn (volume).

### 3.2. Template nào cho insight?
Các template cung cấp insight cao thường là những template liên quan đến lỗi hệ thống hoặc những hành vi hiếm khi xảy ra (rare event):
* **`Redundant addStoredBlock request received for <*>` (Top 10):** Đem lại insight về việc hệ thống có thể đang gọi lại cùng một hàm (addStoredBlock) liên tục không cần thiết cho một hành động. Nguyên nhân có thể do kết nối mạng chập chờn (network timeout) hoặc lỗi thiết kế vòng lặp retry pattern chưa tối ưu.
* **`PacketResponder <*> for block <*> Interrupted.` (Top 9):** Cảnh báo cho ta biết quá trình truyền tải các block dữ liệu đang bị gián đoạn giữa các DataNodes. Nếu chỉ báo tần suất (count) của event này bị spike đột biến, ta có thể suy đoán hệ thống đang xảy ra lỗi phân mảnh mạng (network partition) hoặc lỗi phần cứng I/O trên node mạng.
* **`BLOCK* NameSystem.allocateBlock: <*> <*>` (Top 6):** Khi template đếm việc cấp phát memory block mới tăng vọt vượt mức trung bình, đó là dấu hiệu chứng tỏ hệ thống đang nhận phải một lượng dữ liệu ghi (write load) khổng lồ trong thời gian ngắn. Rất có thể do spam file rác hoặc quá tải HDFS.

### 3.3. Metric vs Log khác gì? Kết hợp 2 nguồn dữ liệu này thì được gì?
* **Metric:** Là các chuỗi giá trị định lượng dưới dạng Time Series đo lường trực tiếp sức khỏe của hệ thống (ví dụ: CPU spike 90%, Latency 1500ms, Error Rate 5%). Metric báo hiệu **"Cái gì đang hỏng?"** (Symptom/Triệu chứng) cực kỳ nhạy và nhanh chóng. Tuy nhiên, nó không thể chỉ ra lý do dẫn tới sự cố đó.
* **Log:** Là cấu trúc tập tin văn bản lưu trữ chuỗi sự kiện, hành động và bối cảnh (context) của mỗi tương tác phần mềm (ví dụ: `Timeout to primary DB 10.0.1.5` hoặc `NullPointerException at process()`). Log giúp người dùng biết được **"Tại sao lại hỏng?"** (Root cause/Nguyên nhân gốc rễ). Đánh đổi lại, việc con người phân tích cả triệu dòng log bằng mắt là điều không thể.

**Giá trị của việc kết hợp Metric + Log (Cross-Signal Analysis):**
Khi tổng hợp cả hai, ta có thể hình thành hệ thống quan trắc (Observability) và AIOps tự động toàn diện:
1. Bộ theo dõi Anomaly trên **Metric** báo động sự cố (Ví dụ: Latency của dịch vụ thanh toán bất ngờ tăng).
2. Hệ thống AIOps tự động khoanh vùng Time Window trên **Log** (Lấy cửa sổ ± 5 phút tại đúng thời điểm Latency báo động).
3. Pipeline chuyển khối lượng log khổng lồ đó vào thuật toán Drain3 để tiến hành thống kê số lượng Template (Template Count).
4. Hệ thống trích xuất và highlight được template count của hành vi bất thường như `Connection Timeout` bị spike mạnh nhất.
5. Cuối cùng, kết luận Root Cause được đưa ra chỉ trong vài giây, giảm thiểu đáng kể thời gian chẩn đoán **MTTD (Mean Time To Detect)** và chi phí xử lý so với các quy trình Manual Triage thủ công.
