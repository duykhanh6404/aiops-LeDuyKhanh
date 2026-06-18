# W3-D1 Design

## 1. SLI choice cho frontend

Em chọn **network error rate** từ RUM làm SLI availability cho frontend: good event là một page-view không có `network_error`. Đây là tín hiệu đo ở phía người dùng và lỗi mạng thường làm tài nguyên hoặc API không tải được, nên quan hệ với user pain rõ hơn các lựa chọn còn lại. Trong 518,400 RUM events, network error chiếm 0.469%; tôi đặt SLO 99% để có buffer so với mức đo hiện tại. Baseline composite của script chỉ đạt 98.6103%, chủ yếu vì nó gộp thêm JS error và ngưỡng DOM-ready.

Tôi không chọn page load time vì log không có field đó, nên không thể đo trực tiếp. DOM-ready phù hợp làm latency SLI phụ; p99 hiện tại là 1,430 ms và chỉ 0.025% event vượt 3 giây. JS error rate là 0.903%, nhưng một lỗi JavaScript không nhất thiết phá hỏng tác vụ chính. Tôi cũng không dùng composite làm SLI chính vì khi nó giảm sẽ khó biết availability mạng hay chất lượng rendering đang gây lỗi.

## 2. SLO target cho API

Em chọn API availability SLO **99.9% trong 30 ngày**. Với 691,260 requests/ngày, ước tính tháng có 20,737,800 requests và budget 20,738 failures, tương đương khoảng 43 phút nếu toàn bộ traffic cùng thất bại. `baseline.json` ghi success rate 97.6318%, nhưng phép tính đó coi 4xx thường và request chậm là không-good trong khi vẫn giữ chúng ở mẫu số. Khi loại 4xx do client, availability toàn bộ replay là 99.6440%; ngoài ba incident API, mức bình thường là 99.8500%. Vì vậy 99.9% là mục tiêu cải thiện có chủ đích, không phải mô tả rằng hệ thống đã đạt sẵn.

Mức 99% cho phép 207,378 failures/tháng và quá lỏng với API thương mại điện tử. Mức 99.99% chỉ cho phép khoảng 2,074 failures, trong khi baseline bình thường vẫn thấp hơn mục tiêu này; nó sẽ đòi hỏi multi-AZ, failover và on-call chặt hơn. 99.9% phù hợp hơn với kiến trúc bốn FastAPI instances, load balancer và database primary/replica hiện tại.

## 3. Latency threshold p99

Em chọn **200 ms** làm ngưỡng latency cho API và theo dõi nó như một latency SLI song song với availability. Phân phối từ 2,073,780 access-log events của pack 3 ngày như sau:

| Percentile | Latency |
|---|---:|
| p50 | 45 ms |
| p90 | 86 ms |
| p95 | 104 ms |
| p99 | 156 ms |
| p99.9 | 394 ms |

Ngưỡng 200 ms nằm trên p99 hiện tại và 99.5681% requests hoàn thành dưới ngưỡng, nên nó bảo vệ tail latency nhưng vẫn để lại error budget có ý nghĩa. Chọn 500 ms sẽ làm 99.9405% request được tính good và có thể che mất một regression lớn hơn ba lần p99 hiện tại. Ngưỡng 1 giây còn lỏng hơn và chỉ phản ứng khi trải nghiệm đã rất tệ. Ngược lại, 100 ms chỉ bao phủ 94.0369%, khiến SLO bị đốt trong trạng thái bình thường. Vì generator thực tế tạo 3 ngày dữ liệu, bảng này không được gọi nhầm là phân phối 7 ngày.

## 4. Loại 4xx khỏi error count

Em loại các 4xx thông thường khỏi error count vì chúng phần lớn biểu diễn request không hợp lệ, thiếu quyền hoặc tài nguyên không tồn tại do client, bot hay scraper. Nếu page on-call vì các lỗi đó, alert không phản ánh lỗi mà đội vận hành có thể sửa ở server. Riêng 429 vẫn là bad event vì hệ thống chủ động từ chối một request hợp lệ do giới hạn capacity. Trong toàn bộ log, fail rate 5xx cộng 429 là 0.3488%; đây là phần được dùng cho burn rate.

Không endpoint nào có 4xx không tính 429 vượt 5%. Tỷ lệ lần lượt là `/api/cart` 2.038%, `/api/products` 2.016%, `/api/orders` 2.015%, `/api/checkout` 2.011%, và `/api/user` 1.976%. Phân bố gần đều quanh 2% khớp với traffic bot/scraper được generator mô phỏng, không chỉ ra một endpoint server đang hỏng. Nếu tính toàn bộ số này là failure, chúng sẽ áp đảo budget 0.1% của SLO 99.9% và tạo cảnh báo liên tục dù user hợp lệ vẫn được phục vụ.

## 5. MWMBR tuning

Em giữ Google default cho Tier 2 (`6`, cửa sổ 6h/30m) và Tier 3 (`1`, cửa sổ 3d/6h), nhưng tune Tier 1 của API từ `14.4` xuống **13** với cửa sổ 1h/5m. Lần chạy default đạt noise reduction 86.4%, không có false negative, nhưng MTTD delta đúng 60 giây, nằm sát và không thỏa cách viết chặt `< 60s` trong checklist. Sau khi giảm ngưỡng, report cuối vẫn chỉ fire 3 lần cho 3 incident API, có 3 TP, 0 FP, 0 FN, đồng thời MTTD p50 giảm từ 60 giây xuống 0 giây.

So với static baseline fire 22 lần gồm 19 FP, cấu hình cuối giảm nhiễu 86.4% và MTTD delta là 0 giây. Tôi không hạ Tier 2 vì thử nghiệm không cải thiện recall; ngưỡng quá thấp còn tăng nguy cơ page vì lỗi vừa phải kéo dài. Thay đổi nhỏ ở Tier 1 là trade-off có dữ liệu hỗ trợ và vẫn giữ cấu trúc multi-window AND để alert phục hồi nhanh.
