# AIOps W2-D3 Submit

## 1. Latency endpoint

Em chay uvicorn voi single worker va gui 20 request lien tiep bang dataset 20 alert that. Header `X-Response-Time-Ms` cho p50 khoang `5.61ms`, p99 khoang `8.45ms`. Breakdown cua request cuoi: `correlate=0.47ms`, `rca=3.52ms`, `validate=0.08ms`, `serialize=0.05ms`, `total_pipeline=4.12ms`. Phase lon nhat la RCA vi no chay graph scorer cho tung cluster va retrieve similar incidents tu history. Neu input tang 10x, correlate se tang theo so alert, RCA tang theo so cluster va so service trong cluster. Validate/serialize cung tang theo payload nhung nho hon. Load graph/history la fixed cost vi da cache luc startup.

## 2. LLM down hoac 4 request dong thoi

Endpoint default `AIOPS_USE_LLM=false`, nen LLM provider down khong lam `/incident` fail. Fallback thuc te la graph + retrieval kNN; response method la `graph+retrieval-knn`, tuc khong phu thuoc provider ngoai. Em test concurrency bang `ThreadPoolExecutor(max_workers=4)` voi 20 request: p50 khoang `10.8ms`, p99/max khoang `15.51ms`. Bottleneck dau tien quan sat duoc la CPU-bound RCA/retrieval trong single process, khong phai network. Neu can scale, em se tang uvicorn workers, nhung phai chap nhan moi worker co cache graph/history rieng.

## 3. /healthz va /readyz

`/healthz` chi check process con song va tra `{"status":"ok"}`. No nen that nhe de load balancer biet app chua crash. `/readyz` check app da san sang nhan traffic that: graph da load va incident history khong rong. Em tach hai endpoint vi mot process co the con song nhung chua ready, vi du load graph fail thi `/healthz` van ok nhung `/readyz` phai 503 de bi remove khoi rotation. Khi LLM API down, `/readyz` van pass trong version nay vi LLM khong phai dependency bat buoc; pipeline co fallback retrieval-only va default khong goi provider ngoai.
