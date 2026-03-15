# FastAPI + OpenTelemetry + Victoria Metrics Stack

## Архітектура

```
FastAPI ──OTLP/HTTP──> OTel Collector ──otlphttp──────> VictoriaTraces    (трейси, :10428)
  :8000       :4318    │              ──prometheusrw──> VictoriaMetrics   (метрики, :8428)
                       │              ──otlphttp──────> VictoriaLogs      (логи, :9428)
                       └── processors: memory_limiter, batch

                       Grafana (:3000) — unified dashboard
```

| Сервіс | Образ | Порт | Призначення |
|--------|-------|------|-------------|
| FastAPI | python:3.12-slim + OTel SDK | 8000 | Демо-застосунок |
| OTel Collector | otel/opentelemetry-collector-contrib:0.120.0 | 4317, 4318 | OTLP receiver + router |
| VictoriaTraces | victoriametrics/victoria-traces:latest | 10428 | Distributed tracing |
| VictoriaMetrics | victoriametrics/victoria-metrics:latest | 8428 | Metrics TSDB (PromQL) |
| VictoriaLogs | victoriametrics/victoria-logs:latest | 9428 | Log aggregation |
| Grafana | grafana/grafana:11.3.0 | 3000 | Visualization |

## Запуск

```bash
cd victoria_metrics
podman-compose up -d --build
```

## Зупинка

```bash
podman-compose down -v
```

## Перевірка

```bash
# Health check
curl http://localhost:8000/health

# Генерація трейсів та метрик
curl http://localhost:8000/items/42
curl http://localhost:8000/slow
curl http://localhost:8000/chain
curl http://localhost:8000/random-error
```

## UI

- **Grafana**: http://localhost:3000 (admin/admin)
- **VictoriaMetrics**: http://localhost:8428/vmui
- **VictoriaTraces**: http://localhost:10428
- **VictoriaLogs**: http://localhost:9428

## Потік даних

1. FastAPI генерує traces, metrics, logs через OpenTelemetry SDK
2. OTel Collector приймає на `:4318` (HTTP) / `:4317` (gRPC)
3. Collector маршрутизує:
   - **Traces** → VictoriaTraces (OTLP HTTP)
   - **Metrics** → VictoriaMetrics (Prometheus Remote Write)
   - **Logs** → VictoriaLogs (OTLP HTTP)
4. Grafana візуалізує всі три datasources з cross-linking (trace ↔ logs)