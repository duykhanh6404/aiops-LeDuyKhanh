# AIOps W2-D1 Submit

## Tham số đã chọn

Em chọn `gap_sec = 120` vì dataset là một burst alert trong khoảng 6 phút, các alert trong cùng incident xuất hiện cách nhau dưới 2 phút. Mức 120 giây đủ lớn để không cắt payment incident thành nhiều mảnh nhỏ, nhưng vẫn nhỏ hơn 600 giây nên giảm nguy cơ gom các sự cố độc lập vào cùng cluster. Trade-off là nếu incident thật sự có silent gap dài hơn 2 phút thì code có thể tách thành nhiều session.

Em chọn `max_hop = 2` vì payment-svc, checkout-svc và edge-lb có liên hệ gần trên topology. Khi payment bị chậm, checkout và edge có thể cùng alert do propagation. Nếu chọn 1 hop thì sẽ bỏ sót edge-lb; nếu chọn quá lớn thì dễ gom nhầm recommender-svc/search-svc qua các đường catalog/edge.

Alert `a-0013` là alert bị miss theo nghĩa không match vào cluster chính. Lý do là label note ghi concurrent batch retrain/unrelated, service recommender-svc cũng không nằm trong payment critical path cần correlation cho sự cố này. Code không drop alert; nó tạo cluster riêng size 1 để đảm bảo 0 orphan bị mất dữ liệu. `a-0016` cũng tương tự vì note nói independent slow query.

Nếu có 10000 alert thay vì 20, code chậm nhất ở `topology_group`: nó so sánh từng cặp alert trong một session, gần O(n^2), và mỗi cặp có thể gọi BFS tính shortest hop. Cần tối ưu bằng cache distance theo cặp service, group trước theo service/fingerprint, và có thể precompute all-pairs shortest path cho service graph nhỏ.

## EOD Checkpoint

1. Fingerprint không include timestamp hay value vì hai field này thay đổi mỗi lần alert fire. Ví dụ `a-0003`, `a-0008`, `a-0015` đều là `payment-svc|latency_p99_ms|crit`, nhưng timestamp khác nhau. Nếu đưa timestamp vào fingerprint, ba alert này sẽ thành ba duplicate khác nhau và dedup gần như vô dụng. Nếu đưa value vào fingerprint, cùng một vấn đề latency nhưng value dao động sẽ bị tách thành nhiều alert riêng.

2. Duplicate alert là các alert gần như cùng một loại vấn đề lặp lại, ví dụ `payment-svc|latency_p99_ms|crit` xuất hiện ở `a-0003`, `a-0008`, `a-0015`. Correlated alert là alert khác metric hoặc khác service nhưng có khả năng cùng incident, ví dụ payment latency/error rate liên quan checkout downstream payment error và edge upstream 5xx. Duplicate dùng fingerprint; correlated cần thêm time-window và topology.

3. `gap_sec = 30` sẽ tạo nhiều session nhỏ hơn và có thể cắt payment incident thành các cluster rời rạc. `gap_sec = 600` sẽ gom rất rộng, dễ kéo recommender batch retrain và search slow query vào cùng cửa sổ thời gian nếu không có guardrail khác.

4. Trong scenario payment pool exhaustion, correlator không gom recommender-svc vào cluster chính. Mặc dù recommender cũng alert trong cùng time window và topology có đường đi gần qua catalog/edge, label note cho thấy nó là batch retrain độc lập. Đây là điểm quan trọng: time gần và topology gần chỉ là tín hiệu, không phải bằng chứng root cause. Code ưu tiên note `unrelated/independent/noise` để tách ra cluster riêng.

5. Limitation lớn nhất của topology grouping là đồ thị service chỉ nói "có kết nối", không nói dependency có đang active, async hay sync, traffic có ảnh hưởng thật không. Vì vậy max_hop có thể gom nhầm các service gần nhau nhưng khác sự cố. Cách khắc phục là thêm edge weight/type, metric semantic similarity, recent trace/log evidence, và cache correlation score thay vì chỉ dùng hop distance.
