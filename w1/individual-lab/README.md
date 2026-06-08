# AIOps W1 Individual Lab

## 1. Chay pipeline

Mo terminal 1:

```powershell
cd C:\DA\AI-Ops\W1\student
py pipeline.py --port 8000
```

Endpoint se chay tai:

```text
http://localhost:8000/ingest
```

## 2. Chay generator

Mo terminal 2:

```powershell
cd C:\DA\AI-Ops\W1\student
py stream_generator.py --birthday 2004-04-06 --target http://localhost:8000/ingest
```

## 3. Xem ket qua

Khi phat hien anomaly, pipeline se ghi alert vao:

```text
alerts.jsonl
```

Moi dong la mot JSON alert, vi du:

```json
{"timestamp": "2026-06-05T10:23:45.000+00:00", "type": "traffic_spike", "severity": "critical", "message": "..."}
```

## 4. Ve lai architecture diagram

Neu can tao lai `architecture.png`:

```powershell
py architecture_diagram.py
```

May can cai Python package `diagrams` va Graphviz tren PATH.
