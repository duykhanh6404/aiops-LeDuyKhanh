# Phân tích và So sánh Log: HDFS vs BGL

## 1. Phân tích Log HDFS (`HDFS_2k.log`)

```text
Analyzing log file: c:/DA/AI-Ops/W1/d2-Log_Mining+Parsing+Anomaly/HDFS_2k.log

--- 1. Basic Stats ---
Total lines: 2000
Total unique templates: 21

--- 2. Top-5 Templates ---
Top 1 (Count: 314, 15.70%): <*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: <*> is added to <*> size <*>
Top 2 (Count: 311, 15.55%): <*> <*> <*> INFO dfs.DataNode$PacketResponder: PacketResponder <*> for block <*> terminating
Top 3 (Count: 292, 14.60%): <*> <*> <*> INFO dfs.DataNode$PacketResponder: Received block <*> of size <*> from <*>
Top 4 (Count: 292, 14.60%): <*> <*> <*> INFO dfs.DataNode$DataXceiver: Receiving block <*> src: <*> dest: <*>
Top 5 (Count: 263, 13.15%): <*> <*> <*> INFO dfs.FSDataset: Deleting block <*> file <*>

--- 3. Template Spikes (Last 1 hour) ---
Spike 1: 081111 <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.allocateBlock: <*> <*>
  -> Past avg: 1.23/hr | Last hour: 9.00/hr
Spike 2: <*> <*> <*> INFO dfs.DataNode$DataXceiver: Receiving block <*> src: <*> dest: <*>
  -> Past avg: 6.70/hr | Last hour: 22.00/hr
Spike 3: <*> <*> <*> INFO dfs.DataNode$PacketResponder: PacketResponder <*> for block <*> terminating
  -> Past avg: 7.16/hr | Last hour: 23.00/hr
Spike 4: <*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: <*> is added to <*> size <*>
  -> Past avg: 7.21/hr | Last hour: 21.00/hr
Spike 5: <*> <*> <*> INFO dfs.DataNode$PacketResponder: Received block <*> of size <*> from <*>
  -> Past avg: 6.59/hr | Last hour: 18.00/hr

--- 4. New Templates (Appeared ONLY in the last 1 hour) ---
No new templates detected in the last hour.
```

---

## 2. Phân tích Log BGL (`BGL_2k.log`)

```text
Analyzing log file: c:/DA/AI-Ops/W1/d2-Log_Mining+Parsing+Anomaly/BGL_2k.log

--- 1. Basic Stats ---
Total lines: 2000
Total unique templates: 151

--- 2. Top-5 Templates ---
Top 1 (Count: 180, 9.00%): - <*> 2005.07.09 <*> <*> <*> RAS KERNEL INFO generating <*>
Top 2 (Count: 121, 6.05%): - <*> <*> <*> <*> <*> RAS KERNEL INFO <*> floating point alignment exceptions
Top 3 (Count: 109, 5.45%): - <*> <*> <*> <*> <*> RAS KERNEL INFO <*> double-hummer alignment exceptions
Top 4 (Count: 92, 4.60%): - <*> <*> <*> <*> <*> RAS KERNEL INFO CE sym <*> at <*> mask <*>
Top 5 (Count: 87, 4.35%): - <*> 2005.07.13 <*> <*> <*> RAS KERNEL INFO generating <*>

Could not extract timestamps from the logs. Skipping time-based analysis.
```

---

## 3. So sánh kết quả đầu ra (Output Comparison)

| Chỉ số so sánh | HDFS_2k.log | BGL_2k.log |
| :--- | :--- | :--- |
| **Tổng số dòng (Total lines)** | 2,000 dòng | 2,000 dòng |
| **Số lượng template unique** | **21 templates** | **151 templates** |

> [!NOTE]
> **Kết luận:** Tập dữ liệu BGL có số lượng template unique lớn hơn rất nhiều so với HDFS (gấp hơn 7 lần) dù cả hai có cùng số lượng dòng log là 2,000.

---

## 4. Giải thích lý do (Tại sao?)

Sự khác biệt lớn về số lượng template này xuất phát từ 3 nguyên nhân chính:

### 4.1 Quy mô và Độ phức tạp của Hệ thống (System Complexity)
* **HDFS (Hadoop Distributed File System):** Là một ứng dụng Java chạy dịch vụ lưu trữ phân tán. Các hoạt động của nó khá đơn giản và lặp đi lặp lại (như ghi block, nhận block, xóa block, kết nối client). Vì vậy, các dạng thông điệp (event types) ghi ra log rất ít.
* **BGL (BlueGene/L Supercomputer):** Là log của một hệ thống siêu máy tính cực kỳ lớn, bao gồm hàng ngàn node tính toán (compute nodes), node I/O, các phần cứng (CPU, RAM, card mạng), hệ điều hành OS Kernel và các dịch vụ quản trị hệ thống. Mỗi thành phần phần cứng và phần mềm đều ghi log riêng, dẫn đến số lượng sự kiện khác nhau là cực kỳ phong phú.

### 4.2 Sự đa dạng của các sự kiện lỗi (Event Diversity)
* **HDFS:** Chỉ xoay quanh các tác vụ filesystem cơ bản.
* **BGL:** Ghi nhận đủ mọi loại lỗi phần cứng/hệ thống phức tạp: lỗi RAM (instruction cache parity error), lỗi tràn CPU (double-hummer alignment exceptions), lỗi phân đoạn bộ nhớ (data TLB error interrupt), tiến trình bị dump core (`generating core.xxx`), hay lỗi ứng dụng ciod.

### 4.3 Đặc trưng định dạng log và tham số động (Dynamic Parameters)
Trong BGL log, có rất nhiều tham số biến động như:
* **Tên file core dump:** `core.2275`, `core.862`
* **Tọa độ Node phần cứng:** `R23-M0-NE-C:J05-U01`, `R16-M1-N2-C:J17-U01`
* **Địa chỉ bộ nhớ dạng Hex:** `0x0b85eee0`, `0x1438f9e0`

Ở cấu hình mặc định của Drain3, nếu chúng ta không cấu hình các quy luật Regex đặc biệt để lọc các chuỗi này thành `<*>`, Drain3 sẽ hiểu lầm các Node ID hay tên core dump khác nhau là các cấu trúc câu lệnh log khác nhau. Từ đó, Drain3 tách chúng thành rất nhiều template riêng biệt, làm tăng vọt số lượng template unique.