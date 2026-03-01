# Архітектура та потоки даних

## Огляд

Стек складається з 6 Docker-сервісів, об'єднаних спільною мережею Docker Compose. Архітектура побудована на принципі **єдиної точки входу**: FastAPI надсилає всю телеметрію по OTLP/HTTP на OpenTelemetry Collector, який маршрутизує дані до трьох бекендів.

## Сервіси

| Сервіс | Образ | Порти | Призначення | RAM limit |
|--------|-------|-------|-------------|-----------|
| **fastapi** | `build: ./fastapi-app` (Python 3.12-slim) | 8000 | FastAPI додаток з OTel SDK | 256 MiB |
| **otel-collector** | `otel/opentelemetry-collector-contrib:0.120.0` | 4317 (gRPC), 4318 (HTTP) | OTLP приймач + маршрутизатор | 256 MiB |
| **tempo** | `grafana/tempo:2.6.1` | 3200 | Trace бекенд (зберігання та пошук) | 512 MiB |
| **loki** | `grafana/loki:3.0.0` | 3100 | Log бекенд (зберігання та пошук) | 512 MiB |
| **prometheus** | `prom/prometheus:v2.54.1` | 9090 | Metrics бекенд (TSDB) | 512 MiB |
| **grafana** | `grafana/grafana:11.3.0` | 3000 | Візуалізація (дашборди, explore) | 256 MiB |

**Загальний RAM**: ~2304 MiB (2.25 GiB)

## Три пайплайни

### 1. Traces pipeline

```
FastAPI (OTel TracerProvider)
    │
    │  OTLP/HTTP POST /v1/traces
    ▼
OTel Collector
    │  receivers: [otlp]
    │  processors: [memory_limiter, batch]
    │  exporters: [otlphttp/tempo]
    ▼
Tempo (:4318 OTLP HTTP receiver)
    │
    │  storage: local filesystem
    │  retention: 1h (compactor)
    ▼
Grafana (Tempo datasource, :3200 query API)
```

**Що потрапляє в трейс:**
- Автоматичний span на кожний HTTP-запит (FastAPIInstrumentor)
- Custom span attributes: `http.request.headers`, `http.request.body`, `http.response.body`, `http.request.query`
- Exception events з loguru-enhanced stacktrace (`format_traceback()`)
- Custom attributes: `app.item_id`, `app.delay_seconds`, `app.chained_item_id`, `app.outcome`

### 2. Metrics pipeline

```
FastAPI (OTel MeterProvider)
    │
    │  OTLP/HTTP POST /v1/metrics
    │  (кожні 15 секунд — PeriodicExportingMetricReader)
    ▼
OTel Collector
    │  receivers: [otlp]
    │  processors: [memory_limiter, batch]
    │  exporters: [prometheusremotewrite]
    ▼
Prometheus (:9090, remote write receiver)
    │
    │  --web.enable-remote-write-receiver
    │  retention: 2h
    ▼
Grafana (Prometheus datasource, PromQL)
```

**Метрики (golden signals):**

| Метрика | Тип | Prometheus name | Опис |
|---------|-----|-----------------|------|
| Traffic | Counter | `http_server_request_count_total` | Кількість HTTP-запитів |
| Errors | Counter | `http_server_error_count_total` | Кількість помилок (4xx, 5xx) |
| Latency | Histogram | `http_server_request_duration_milliseconds_bucket` | Розподіл часу відповіді (ms) |
| CPU | ObservableGauge | `process_cpu_utilization_ratio` | CPU usage (0.0-1.0) |
| Memory | ObservableUpDownCounter | `process_memory_usage_bytes` | RSS пам'ять процесу (bytes) |
| Active Requests | UpDownCounter | `http_server_active_requests` | Кількість запитів в обробці (auto-instrumentation) |

**Важливо**: `resource_to_telemetry_conversion: true` в OTel Collector конвертує resource attributes (service.name, host.name) у metric labels.

### 3. Logs pipeline

```
FastAPI (loguru → LoguruOTelHandler sink)
    │
    │  OTLP/HTTP POST /v1/logs
    │  (BatchLogRecordProcessor)
    ▼
OTel Collector
    │  receivers: [otlp]
    │  processors: [memory_limiter, batch]
    │  exporters: [otlphttp/loki]
    ▼
Loki (:3100/otlp — native OTLP endpoint)
    │
    │  schema: v13, store: TSDB
    │  label: service_name (з resource attribute service.name)
    │  structured metadata: trace_id, span_id, severity, etc.
    ▼
Grafana (Loki datasource, LogQL)
    Query: {service_name="fastapi-demo"}
```

**Що потрапляє в лог-запис:**
- Log message (loguru format)
- `trace_id`, `span_id` — автоматична кореляція з трейсами
- `severity_text` (DEBUG/INFO/WARNING/ERROR)
- `exception.type`, `exception.message` — при помилках
- `exception.trace_url` — пряме посилання на трейс у Grafana Tempo

## Docker Compose деталі

### Залежності (depends_on)

```
grafana ──depends_on──> loki, tempo, prometheus
otel-collector ──depends_on──> tempo, loki, prometheus
fastapi ──depends_on──> otel-collector
```

Порядок запуску: `tempo` + `loki` + `prometheus` → `otel-collector` → `fastapi` + `grafana`

### Volumes

| Volume | Сервіс | Призначення |
|--------|--------|-------------|
| `tempo-data` | tempo | Trace blocks + WAL (`/tmp/tempo`) |
| `loki-data` | loki | Log chunks + TSDB index (`/tmp/loki`) |
| `prometheus-data` | prometheus | Metrics TSDB (`/prometheus`) |
| `grafana-data` | grafana | Grafana DB + плагіни (`/var/lib/grafana`) |

Конфігураційні файли монтуються як `:ro` (read-only) bind mounts.

### Environment Variables (FastAPI)

| Змінна | Default | Опис |
|--------|---------|------|
| `OTEL_SERVICE_NAME` | `fastapi-demo` | Ім'я сервісу в OTel resource |
| `OTEL_SERVICE_VERSION` | `1.0.0` | Версія сервісу |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTLP endpoint |
| `OTEL_ENABLED` | `true` | Увімкнення/вимкнення OTel |
| `CHAIN_TARGET_URL` | `http://fastapi:8000` | URL для `/chain` endpoint |

### Мережа

Всі сервіси знаходяться у default мережі Docker Compose. Взаємодія відбувається за іменами контейнерів (DNS service discovery):
- `fastapi` → `otel-collector:4318`
- `otel-collector` → `tempo:4318`, `loki:3100`, `prometheus:9090`
- `grafana` → `loki:3100`, `tempo:3200`, `prometheus:9090`

## Lifecycle HTTP-запиту

Повний шлях запиту `GET /items/42` від клієнта до візуалізації в Grafana:

```
1. Client
   │
   │  GET /items/42
   ▼
2. FastAPI (:8000)
   │
   ├─ FastAPIInstrumentor створює span
   │  (trace_id, span_id генеруються автоматично)
   │
   ├─ otel_golden_signals_middleware:
   │  ├─ request_counter.add(1, attrs)
   │  ├─ duration_histogram.record(ms, attrs)
   │  └─ error_counter.add(1, attrs)  # якщо status >= 400
   │
   ├─ log_requests middleware:
   │  ├─ span.set_attribute("http.request.headers", ...)
   │  ├─ span.set_attribute("http.request.body", ...)
   │  ├─ response = await call_next(request)
   │  ├─ span.set_attribute("http.response.body", ...)
   │  └─ logger.info("GET /items/42 → 200 (5.23ms)")
   │     └─ LoguruOTelHandler sink → OTel LogRecord
   │
   ├─ get_item() endpoint:
   │  ├─ span.set_attribute("app.item_id", "42")
   │  └─ logger.info("Item retrieved successfully: item_id=42")
   │
   │  Якщо exception:
   │  ├─ http_exception_handler / unhandled_exception_handler
   │  ├─ span.record_exception(exc, stacktrace=format_traceback(exc))
   │  ├─ span.set_status(StatusCode.ERROR)
   │  └─ logger.bind(_otel_exc=exc).error(...)
   │     └─ LoguruOTelHandler → exception.trace_url (Tempo link)
   │
   ├─ Response → Client
   │
   ▼
3. OTel SDK (BatchSpanProcessor / BatchLogRecordProcessor / PeriodicExportingMetricReader)
   │
   │  Збирає дані в batch, надсилає по OTLP/HTTP
   ▼
4. OTel Collector (:4318)
   │
   ├─ processors: memory_limiter → batch
   │
   ├──► otlphttp/tempo  → Tempo (:4318)    → trace blocks на диску
   ├──► prometheusrw     → Prometheus (:9090) → TSDB
   └──► otlphttp/loki   → Loki (:3100/otlp) → TSDB chunks
   │
   ▼
5. Grafana (:3000)
   │
   ├─ Dashboard "FastAPI — Golden Signals & OTel":
   │  ├─ Request Rate (PromQL: rate(http_server_request_count_total[1m]))
   │  ├─ Error Rate (PromQL: rate(http_server_error_count_total[1m]))
   │  ├─ Latency p50/p95/p99 (histogram_quantile)
   │  ├─ CPU & Memory (process_cpu_utilization_ratio, process_memory_usage_bytes)
   │  └─ Application Logs (LogQL: {service_name="fastapi-demo"})
   │
   ├─ Loki → Tempo link:
   │  ├─ derivedFields: trace_id → View trace in Tempo
   │  └─ exception.trace_url → пряме посилання на трейс
   │
   └─ Tempo → Loki link:
      └─ tracesToLogsV2: query за trace_id в Loki
```
