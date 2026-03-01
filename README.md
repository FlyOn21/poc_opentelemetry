# FastAPI + OpenTelemetry + OTel Collector + Grafana

Повністю самостійний PoC для демонстрації OpenTelemetry observability.
Запускається однією командою через Podman Compose / Docker Compose.

---

## Архітектура

```
┌──────────────┐         ┌──────────────────┐         ┌──────────┐
│   FastAPI     │──OTLP──>│  OTel Collector   │──otlp──>│  Tempo   │  (трейси)
│  OTelManager  │  :4318  │                  │         └──────────┘
│  + uvicorn    │         │  receivers:      │──prw───>┌──────────┐
└──────────────┘         │    otlp (HTTP+gRPC)│  :9090  │Prometheus│  (метрики)
                          │  processors:      │         └──────────┘
                          │    memory_limiter  │──otlp──>┌──────────┐
                          │    batch           │  :3100  │   Loki   │  (логи)
                          └──────────────────┘         └──────────┘
                                                              │
                                                         ┌──────────┐
                                                         │ Grafana  │  (UI)
                                                         │ :3000    │
                                                         └──────────┘
```

**Одна точка входу, три pipeline через OTel Collector:**

1. **Traces** — FastAPI OTelManager → OTLP/HTTP → OTel Collector → **Tempo** (otlphttp)
2. **Metrics** — FastAPI OTelManager → OTLP/HTTP → OTel Collector → **Prometheus** (prometheusremotewrite, з підтримкою histograms)
3. **Logs** — FastAPI OTel logging handler → OTLP/HTTP → OTel Collector → **Loki** (native OTLP endpoint, structured metadata)

**Grafana** підключена до всіх трьох backends з log-to-trace кореляцією (trace_id у structured metadata Loki → перехід до Tempo).

---

## Швидкий старт

### Передумови

- Podman + podman-compose **або** Docker + Docker Compose

### Запуск

```bash
cd docker-compose-demo

# Запустити весь стек (збірка + старт)
podman-compose up -d --build
# або: docker compose up -d --build

# Перевірити що все працює (всі 6 контейнерів Up)
podman ps
# або: docker compose ps
```

> **Примітка:** Loki потребує ~30 секунд для прогрівання ingester ring. Логи почнуть з'являтись після цього.

### Перевірка

| Сервіс | URL | Опис |
|--------|-----|------|
| FastAPI | http://localhost:8000 | API додаток |
| FastAPI Docs | http://localhost:8000/docs | Swagger UI |
| Grafana | http://localhost:3000 | UI (admin / admin) |
| Prometheus | http://localhost:9090 | Metrics UI |
| Tempo | http://localhost:3200 | Traces API |
| Loki | http://localhost:3100 | Logs API |

### Зупинка

```bash
# Зупинити (зберегти дані)
podman-compose down

# Зупинити та видалити дані
podman-compose down -v
```

---

## Генерація тестового трафіку

```bash
# Базові запити
for i in $(seq 1 20); do curl -s http://localhost:8000/ > /dev/null; done

# Latency розподіл (0.1-2.0 сек)
for i in $(seq 1 10); do curl -s http://localhost:8000/slow; echo; done

# Distributed tracing (chain → items)
for i in $(seq 1 10); do curl -s http://localhost:8000/chain; echo; done

# Error rate (70% OK, 15% 400, 15% 500)
for i in $(seq 1 30); do curl -s http://localhost:8000/random-error; echo; done

# Конкретні помилки
curl http://localhost:8000/items/42      # OK
curl http://localhost:8000/items/error   # 500
curl http://localhost:8000/items/abc     # 400
```

---

## Grafana Dashboard

Дашборд **"FastAPI — Golden Signals & OTel"** provisioned автоматично.

Відкрити http://localhost:3000 → **Dashboards** → **FastAPI — Golden Signals & OTel**

### Панелі дашборду

| Панель | Метрика | Опис |
|--------|---------|------|
| Request Rate (RPS) | `rate(http_server_request_count_total[1m])` | Трафік по ендпоінтах |
| Error Rate | `rate(http_server_error_count_total[1m])` | Помилки 4xx/5xx |
| Active Requests | `http_server_active_requests_total` | Поточне навантаження |
| Latency p50/p95/p99 | `histogram_quantile(http_server_request_duration_bucket)` | Розподіл latency |
| CPU & Memory | `process_cpu_utilization`, `process_memory_usage_total` | Saturation |
| Total Requests / Errors (5m) | `increase(...)` | Stat-панелі |
| Error Rate % | errors / requests | Gauge |
| Requests by Endpoint | pie chart по `http_route` | Розподіл |
| Errors by Status Code | pie chart по `http_status_code` | Розподіл помилок |
| Request Rate by Status Code | stacked bars | Трафік по кодах |
| Application Logs | `{service_name="fastapi-demo"}` | OTel логи з trace_id |

### Explore

#### Логи (Loki)

1. **Explore** → datasource **Loki**
2. Запит: `{service_name="fastapi-demo"}`
3. Побачите OTel логи з trace_id у structured metadata
4. Клік по trace_id → перехід до Tempo

#### Трейси (Tempo)

1. **Explore** → datasource **Tempo**
2. Вкладка **Search** → Service Name: `fastapi-demo`
3. Run query → побачите waterfall діаграму spans
4. Для `/chain` запитів видно parent + child spans
5. Клік "Logs for this trace" → пов'язані логи в Loki

#### Метрики (Prometheus)

1. **Explore** → datasource **Prometheus**
2. Запити:
   - `http_server_request_count_total` — кількість запитів
   - `http_server_error_count_total` — кількість помилок
   - `histogram_quantile(0.95, sum(rate(http_server_request_duration_bucket[5m])) by (le))` — p95 latency
   - `rate(http_server_request_count_total[5m])` — RPS
   - `process_cpu_utilization` — CPU
   - `process_memory_usage_total` — Memory

---

## Демо-ендпоінти

| Ендпоінт | Що робить | Що демонструє |
|----------|-----------|---------------|
| `GET /` | Hello world | Базовий trace |
| `GET /health` | Health check | — |
| `GET /items/{id}` | Отримати item | Span attributes, error traces |
| `GET /items/error` | Помилка 500 | Error tracing |
| `GET /slow` | Затримка 0.1-2.0с | Latency histogram distribution |
| `GET /chain` | Виклик items/{random} | Distributed tracing |
| `GET /random-error` | 70% OK / 15% 400 / 15% 500 | Error rate metrics |

---

## Золоті сигнали (Golden Signals)

| Сигнал | OTel метрика | Prometheus метрика | Тип |
|--------|-------------|-------------------|-----|
| Traffic | `http.server.request.count` | `http_server_request_count_total` | Counter |
| Errors | `http.server.error.count` | `http_server_error_count_total` | Counter |
| Latency | `http.server.request.duration` | `http_server_request_duration_bucket` | Histogram |
| Saturation (CPU) | `process.cpu.utilization` | `process_cpu_utilization` | Gauge |
| Saturation (Memory) | `process.memory.usage` | `process_memory_usage_total` | Counter |

---

## Структура проекту

```
docker-compose-demo/
├── docker-compose.yml              # Весь стек (6 сервісів + volumes)
├── README.md                       # Цей файл
├── fastapi-app/
│   ├── main.py                     # FastAPI + OTel + JSON logging + демо-ендпоінти
│   ├── otel_manager.py             # OTelManager (traces, metrics, logs)
│   ├── requirements.txt            # Python залежності
│   └── Dockerfile                  # python:3.12-slim + gcc
├── otel-collector/
│   └── otel-collector.yaml         # OTLP receiver → 3 pipelines (traces, metrics, logs)
├── tempo/
│   └── tempo.yaml                  # Tempo single-binary, OTLP receiver :4318
├── prometheus/
│   └── prometheus.yml              # Self-monitoring scrape + remote write receiver
├── loki/
│   └── loki.yaml                   # Loki single-instance, TSDB + OTLP + structured metadata
└── grafana/
    ├── datasources.yaml            # Auto-provisioning: Loki, Tempo, Prometheus
    ├── dashboards-provisioning.yaml# Dashboard provider config
    └── dashboards/
        └── fastapi-otel.json       # Golden Signals + Latency dashboard
```

---

## Конфігурація OTel Collector

```
                    ┌──────────────────────────────────────┐
                    │    OpenTelemetry Collector Contrib     │
                    │                                      │
  OTLP/HTTP :4318 ─>│  [receiver] otlp                     │
  OTLP/gRPC :4317 ─>│       protocols: http, grpc          │
                    │                                      │
                    │  [processor] memory_limiter           │
                    │       limit: 200 MiB                 │
                    │  [processor] batch                    │
                    │       batch_size: 1024, timeout: 5s  │
                    │                                      │
                    │  [pipeline] traces                    │
                    │       otlp → batch → otlphttp        │──> Tempo :4318
                    │                                      │
                    │  [pipeline] metrics                   │
                    │       otlp → batch → prometheusrw    │──> Prometheus :9090
                    │       resource_to_telemetry: true     │    /api/v1/write
                    │                                      │
                    │  [pipeline] logs                      │
                    │       otlp → batch → otlphttp        │──> Loki :3100
                    │       native OTLP endpoint           │    /otlp
                    └──────────────────────────────────────┘
```

### Чому OTel Collector замість Fluent Bit

| Проблема Fluent Bit 3.x | Рішення OTel Collector |
|--------------------------|----------------------|
| Histogram метрики не передаються | `prometheusremotewrite` — повна підтримка histograms |
| `opentelemetry` output надсилає всі сигнали на кожен хост (404) | Окремі pipelines з typed exporters |
| `Tag_From_URI` ламає routing | Немає workaround'ів — нативний OTLP receiver |
| Потрібен shared volume + tail input для логів | OTLP logs напряму через Collector |
| OTel log формат несумісний з loki output | `otlphttp` → Loki native OTLP → structured metadata |

---

## Типові проблеми

### Loki: "too many unhealthy instances in the ring"

Loki потребує ~30 секунд для прогрівання. Зачекайте і перевірте:

```bash
curl http://localhost:3100/ready
# Має повернути: ready
```

### Трейси не з'являються

```bash
# Перевірити OTel Collector
podman logs otel-collector | tail -20

# Перевірити Tempo
curl http://localhost:3200/ready
curl http://localhost:3200/api/search?limit=5
```

### Метрики порожні в Prometheus

```bash
# Перевірити що remote write receiver увімкнений
curl -X POST http://localhost:9090/api/v1/write
# Має повернути 400 (не 404)

# Перевірити histograms (мають бути!)
curl 'http://localhost:9090/api/v1/query?query=http_server_request_duration_bucket'
```

### Логи не з'являються в Loki

```bash
# Перевірити OTel Collector pipeline
podman logs otel-collector | grep -i error

# Перевірити що Loki готовий
curl http://localhost:3100/ready

# Перевірити логи через API (label тепер service_name, не job)
curl -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="fastapi-demo"}' \
  --data-urlencode 'limit=5'
```

### Перезапуск

```bash
# Окремий сервіс
podman restart otel-collector

# При зміні коду FastAPI потрібно перебілдити
podman-compose down && podman-compose up -d --build

# Повний reset (видалити всі дані)
podman-compose down -v && podman-compose up -d --build
```

---

## Перевірка після запуску

```bash
# Зібрати стек
podman-compose down -v && podman-compose up -d --build

# Зачекати на Loki warmup
sleep 30

# Генерація трафіку
for i in $(seq 1 20); do curl -s http://localhost:8000/ > /dev/null; done
for i in $(seq 1 10); do curl -s http://localhost:8000/slow > /dev/null; done
for i in $(seq 1 30); do curl -s http://localhost:8000/random-error > /dev/null; done

# Перевірка traces
curl 'http://localhost:3200/api/search?limit=5'

# Перевірка histograms (тепер працюють!)
curl 'http://localhost:9090/api/v1/query?query=http_server_request_duration_bucket'

# Перевірка logs (через service_name label)
curl -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="fastapi-demo"}' \
  --data-urlencode 'limit=5'

# Перевірка OTel Collector errors
podman logs otel-collector
```

---

## Resource Limits

Всі контейнери мають обмеження пам'яті для запобігання зависанню системи:

| Сервіс | mem_limit |
|--------|-----------|
| FastAPI | 256 MB |
| OTel Collector | 256 MB |
| Grafana | 256 MB |
| Tempo | 512 MB |
| Loki | 512 MB |
| Prometheus | 512 MB |
