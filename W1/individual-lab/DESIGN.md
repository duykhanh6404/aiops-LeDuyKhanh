# Detection Approach - DESIGN.md

## Approach toi dung

Rule-based streaming detector voi rolling state nho trong memory. Pipeline nhan tung payload o `/ingest`, doc metrics va logs, sau do so sanh voi cac nguong bao thu duoc suy ra tu bang normal range trong de bai.

## Tai sao chon approach nay

Lab co baseline ro rang va chi co ba fault can phan loai: `memory_leak`, `traffic_spike`, `dependency_timeout`. Rule-based detection phu hop streaming vi xu ly tung event ngay khi den, khong can train model, it phu thuoc thu vien, va de giai thich evidence trong alert.

## Cach hoat dong

Moi POST duoc validate JSON, luu vao rolling window 120 diem gan nhat, roi chay cac rule theo thu tu root cause. Dependency timeout duoc uu tien truoc vi retry co the lam tang traffic va queue; traffic spike dung RPS, queue depth va latency; memory leak dung memory utilization, GC pause va log signal. Khi rule match, pipeline ghi mot dong JSON vao `alerts.jsonl`. Alert duoc deduplicate theo `type`; neu cung mot type da co warning thi chi ghi them khi severity tang len critical.

## Parameters 

- `memory_leak`: memory utilization >= 70% va GC pause >= 45ms, hoac log co dau hieu OOM/GC pause. Critical khi utilization >= 80%, GC pause >= 100ms, hoac co ERROR/FATAL log.
- `traffic_spike`: RPS >= 320, queue depth >= 40, p99 latency >= 180ms. Critical khi RPS >= 500, queue depth >= 120, hoac 5xx >= 10%.
- `dependency_timeout`: upstream timeout >= 5%, 5xx >= 2%, p99 latency >= 180ms. Critical khi upstream timeout >= 20% hoac 5xx >= 10%.
- Rolling history size = 120 datapoints de giu context gan day cho debug/stats ma khong ton bo nho.

Cac nguong tren nam xa vung normal trong de bai: memory binh thuong khoang 40%, RPS 80-160, queue 2-10, upstream timeout 0-0.4%. Vi vay detector tranh alert som truoc khi fault that su xay ra.

## Pipeline / Architecture

Source ve diagram nam o `architecture_diagram.py` va dung Python `diagrams` library. Khi may co Graphviz tren PATH, chay:

```bash
python architecture_diagram.py
```

Script se tao `architecture.png`. Neu thieu Graphviz executable `dot`, script giu lai `architecture.dot` de render sau.

## Cai thien neu co them thoi gian

- Them adaptive baseline theo moving median/MAD cho moi metric.
- Ghi metric raw va alert vao SQLite hoac DuckDB de replay/debug.
- Them cooldown theo thoi gian thay vi chi deduplicate theo severity.
- Viet test end-to-end voi generator o che do speed cao va fault injection ngan hon.
