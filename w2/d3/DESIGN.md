# AIOps Serving Design

## Pipeline architecture

`serve.py` dung FastAPI de serve pipeline alert triage thanh HTTP API. Endpoint chinh la `POST /incident`. Request duoc validate bang Pydantic model `IncidentRequest`, gom list alert voi cac field `id`, `ts`, `service`, `metric`, `severity`, `value`, `threshold`, `labels`. Sau khi validate, endpoint convert alert ve plain dict va goi pipeline that: `w2/d1/correlate.py` de tao clusters, sau do `w2/d2/rca.py` de chay graph + temporal scorer, retrieval kNN va lay RCA cho tung cluster.

Service graph va incident history duoc load mot lan luc import app, roi cache trong memory: `CORRELATION_GRAPH`, `RCA_GRAPH_BUNDLE`, `HISTORY`. Day la quyet dinh co y thuc: voi dataset nho va manual graph, load-on-start nhanh va tranh doc file moi request. Trade-off la graph co the stale; neu production that, em se them reload moi 5 phut hoac endpoint reload co audit. Cau hinh correlation chon `gap_sec=120` va `max_hop=2`, giong notebook D1, vi dataset co payment cascade trong burst ngan va can gom edge/checkout/payment nhung khong mo qua rong.

## Endpoints

`GET /healthz` chi tra `{"status":"ok"}` de dung lam liveness check. `GET /readyz` check graph da load va incident history khong rong; LLM khong duoc xem la dependency bat buoc vi bai nay chay retrieval-only va default `AIOPS_USE_LLM=false`. `GET /version` tra app version, pipeline config, graph source, graph_loaded_at, node count va edge count. `POST /incident` tra `clusters`, `root_cause`, `recommended_actions`, `similar_incidents`, `rca_results`, `pipeline_version`, va `timings_ms`.

## Latency budget

Middleware gan header `X-Response-Time-Ms` cho moi response. Khi do voi 20 alert that, 20 request lien tiep tren uvicorn single worker cho p50 khoang 5.61ms va p99 khoang 8.45ms. Breakdown request cuoi: correlate 0.47ms, RCA 3.52ms, validate 0.08ms, serialize 0.05ms, total pipeline 4.12ms. RCA chiem nhieu nhat vi moi cluster phai rank graph candidates va retrieve similar incidents trong history. Neu input tang 10 lan, correlate va RCA se tang gan theo so alert/cluster; validate va serialize tang theo payload nhung nho hon. Load graph/history la fixed cost vi cache luc startup.

## Production concern

Concern chinh la concurrency va fallback. Service chay duoc voi `uvicorn serve:app --port 8000 --workers 1`, phu hop may yeu. Test 20 request voi 4 concurrent client cho p50 10.8ms va p99 15.51ms; bottleneck dau tien van la CPU-bound RCA/retrieval trong mot process. Neu production traffic cao, co the tang `--workers 4`, nhung moi worker se co cache graph/history rieng. LLM provider down khong lam endpoint fail vi default khong goi LLM; method van la `graph+retrieval-knn`, tuc fallback deterministic khong phu thuoc provider ngoai.

## Framework trade-off

Em chon FastAPI thay vi Flask vi Pydantic validation tu dong tra 422 khi input sai, co OpenAPI schema va middleware/async native tot hon. Flask nhe hon nhung phai tu viet validation va de miss case. Em khong chon BentoML vi pipeline nay khong phai model artifact can model registry/batching; no la glue service gom correlation + RCA + retrieval. FastAPI la diem can bang tot: it boilerplate, chay duoc local, va du gan voi production API.
