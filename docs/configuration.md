# Конфігурація компонентів

## OTel Collector

**Файл**: `otel-collector/otel-collector.yaml`
**Образ**: `otel/opentelemetry-collector-contrib:0.120.0`

### Receivers

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: "0.0.0.0:4318"    # OTLP/HTTP (використовується FastAPI)
      grpc:
        endpoint: "0.0.0.0:4317"    # OTLP/gRPC (доступний, але не використовується)
```

Єдиний receiver `otlp` приймає traces, metrics та logs одночасно.

### Processors

```yaml
processors:
  memory_limiter:
    check_interval: 5s        # Перевірка пам'яті кожні 5 секунд
    limit_mib: 200             # Жорсткий ліміт 200 MiB
    spike_limit_mib: 50        # Дозволений spike 50 MiB
  batch:
    send_batch_size: 1024      # Надсилати batch при 1024 елементах
    timeout: 5s                # Або кожні 5 секунд
```

- **memory_limiter** — захист від OOM. Якщо пам'ять перевищує ліміт, нові дані відхиляються.
- **batch** — групує дані перед відправкою для зменшення кількості HTTP-запитів.

**Порядок процесорів**: `memory_limiter` завжди першим (рекомендація OTel).

### Exporters

```yaml
exporters:
  # Traces → Tempo
  otlphttp/tempo:
    endpoint: http://tempo:4318

  # Metrics → Prometheus
  prometheusremotewrite:
    endpoint: http://prometheus:9090/api/v1/write
    resource_to_telemetry_conversion:
      enabled: true     # resource attributes → metric labels

  # Logs → Loki
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
```

**`resource_to_telemetry_conversion: true`** — конвертує OTel resource attributes (service.name, service.version, host.name) у Prometheus metric labels. Без цього вони не доступні в PromQL.

**`otlphttp/loki`** — Loki 3.0+ підтримує native OTLP endpoint на `/otlp`. Не потрібен спеціальний loki exporter.

### Pipelines

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/tempo]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/loki]
  telemetry:
    logs:
      level: info               # Рівень внутрішніх логів Collector
```

Три незалежні пайплайни з однаковим receiver та processors, але різними exporters.

---

## Tempo

**Файл**: `tempo/tempo.yaml`
**Образ**: `grafana/tempo:2.6.1`

```yaml
server:
  http_listen_port: 3200          # Query API для Grafana

distributor:
  receivers:
    otlp:
      protocols:
        http:
          endpoint: "0.0.0.0:4318"  # Прийом трейсів від OTel Collector

storage:
  trace:
    backend: local                 # Локальне сховище (для PoC)
    local:
      path: /tmp/tempo/blocks      # Trace blocks
    wal:
      path: /tmp/tempo/wal         # Write-Ahead Log

compactor:
  compaction:
    block_retention: 1h            # Трейси зберігаються 1 годину
```

### Ключові параметри

| Параметр | Значення | Опис |
|----------|----------|------|
| `http_listen_port` | 3200 | API для Grafana (TraceQL queries) |
| `backend` | local | Зберігання на файловій системі |
| `block_retention` | 1h | Час зберігання трейсів |
| WAL path | `/tmp/tempo/wal` | Write-Ahead Log (persistence при crash) |

**Volumes**: `tempo-data:/tmp/tempo` — зберігає blocks та WAL між перезапусками.

---

## Loki

**Файл**: `loki/loki.yaml`
**Образ**: `grafana/loki:3.0.0`

```yaml
auth_enabled: false                # Без аутентифікації (PoC)

server:
  http_listen_port: 3100           # API + OTLP endpoint

common:
  path_prefix: /tmp/loki
  replication_factor: 1            # Single instance
  ring:
    kvstore:
      store: inmemory              # In-memory ring (не потрібен consul/etcd)
    instance_addr: 127.0.0.1

schema_config:
  configs:
    - from: "2024-01-01"
      store: tsdb                  # TSDB storage engine
      object_store: filesystem
      schema: v13                  # Найновіша схема
      index:
        prefix: index_
        period: 24h

storage_config:
  filesystem:
    directory: /tmp/loki/chunks    # Log chunks
  tsdb_shipper:
    active_index_directory: /tmp/loki/tsdb-index
    cache_location: /tmp/loki/tsdb-cache

compactor:
  working_directory: /tmp/loki/compactor

limits_config:
  reject_old_samples: false        # Приймати старі семпли (для тестування)
  allow_structured_metadata: true  # Дозволити structured metadata від OTLP
  otlp_config:
    resource_attributes:
      attributes_config:
        - action: index_label
          attributes:
            - service.name         # resource attribute → index label "service_name"
```

### Ключові параметри

| Параметр | Значення | Опис |
|----------|----------|------|
| `schema` | v13 | Остання схема Loki |
| `store` | tsdb | TSDB storage engine (рекомендований) |
| `allow_structured_metadata` | true | Зберігає OTel attributes як structured metadata |
| `service.name → index_label` | — | `service.name` стає Loki label `service_name` |

**OTLP config**: `service.name` конвертується в index label, що дозволяє запит `{service_name="fastapi-demo"}`. Решта OTel attributes (trace_id, span_id, severity, etc.) зберігаються як structured metadata.

**Volumes**: `loki-data:/tmp/loki` — зберігає chunks, TSDB index, cache.

---

## Prometheus

**Файл**: `prometheus/prometheus.yml`
**Образ**: `prom/prometheus:v2.54.1`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]  # Self-monitoring
```

### Командний рядок (docker-compose.yml)

```yaml
command:
  - "--config.file=/etc/prometheus/prometheus.yml"
  - "--web.enable-remote-write-receiver"    # Приймає метрики по Remote Write API
  - "--storage.tsdb.retention.time=2h"      # Зберігає метрики 2 години
```

### Ключові параметри

| Параметр | Значення | Опис |
|----------|----------|------|
| `--web.enable-remote-write-receiver` | — | Відкриває `/api/v1/write` endpoint |
| `--storage.tsdb.retention.time` | 2h | Retention метрик |
| `scrape_interval` | 15s | Self-scrape (не впливає на OTel метрики) |

**Важливо**: Prometheus тут працює як **remote write receiver**, а не як традиційний scraper. OTel Collector надсилає метрики через `prometheusremotewrite` exporter на `/api/v1/write`.

**Volumes**: `prometheus-data:/prometheus` — TSDB data.

---

## Grafana

**Образ**: `grafana/grafana:11.3.0`

### Environment Variables

```yaml
environment:
  - GF_SECURITY_ADMIN_USER=admin
  - GF_SECURITY_ADMIN_PASSWORD=admin
  - GF_AUTH_ANONYMOUS_ENABLED=true        # Дозволити анонімний доступ
  - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer     # Анонімні користувачі — Viewer
```

### datasources.yaml

**Файл**: `grafana/datasources.yaml`

Три datasources з автоматичним provisioning:

#### Loki datasource

```yaml
- name: Loki
  uid: loki
  type: loki
  url: http://loki:3100
  isDefault: true
  jsonData:
    derivedFields:
      - matcherType: label
        matcherRegex: 'trace_id'          # Поле structured metadata
        name: TraceID
        url: '/explore?orgId=1&left=...'  # URL до Tempo trace
        urlDisplayLabel: View trace in Tempo
        openInNewTab: true
```

**derivedFields** — створює клікабельну лінку "View trace in Tempo" для кожного лог-запису з `trace_id`. Параметри:

| Параметр | Значення | Опис |
|----------|----------|------|
| `matcherType` | `label` | Матчинг по label/structured metadata |
| `matcherRegex` | `trace_id` | Назва поля (НЕ `${trace_id}`) |
| `url` | Grafana Explore URL | `$$` для escape `$` в provisioning YAML |
| `openInNewTab` | `true` | Відкриває в новій вкладці |

#### Tempo datasource

```yaml
- name: Tempo
  uid: tempo
  type: tempo
  url: http://tempo:3200
  jsonData:
    tracesToLogsV2:
      datasourceUid: loki
      filterByTraceID: false
      filterBySpanID: false
      customQuery: true
      query: '{service_name="fastapi-demo"} | trace_id = "$${__trace.traceId}"'
    nodeGraph:
      enabled: true
```

**tracesToLogsV2** — зворотній лінк з Tempo в Loki. При перегляді трейсу можна перейти до логів:
- `customQuery: true` — використовує custom LogQL замість автоматичного
- `query` — фільтрує логи за trace_id з structured metadata
- `$$` — escape `$` для Grafana provisioning YAML

**nodeGraph** — візуалізація service graph у Tempo.

#### Prometheus datasource

```yaml
- name: Prometheus
  uid: prometheus
  type: prometheus
  url: http://prometheus:9090
```

### dashboards-provisioning.yaml

**Файл**: `grafana/dashboards-provisioning.yaml`

```yaml
providers:
  - name: "default"
    orgId: 1
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/dashboards       # Монтується з ./grafana/dashboards
      foldersFromFilesStructure: false
```

Автоматично імпортує всі JSON-дашборди з `./grafana/dashboards/`.

### Dashboard: fastapi-otel.json

**Файл**: `grafana/dashboards/fastapi-otel.json`
**Title**: "FastAPI — Golden Signals & OTel"
**UID**: `fastapi-otel-golden`
**Tags**: `fastapi`, `opentelemetry`, `golden-signals`
**Auto-refresh**: 10s

#### Змінні (template variables)

| Змінна | Тип | Значення | Опис |
|--------|-----|----------|------|
| `$LogLevel` | custom, multi | debug, info, warning, error, critical | Фільтр рівня логів |
| `$TextFilter` | textbox | (порожній) | Текстовий пошук у логах |

#### Панелі

**Row: Golden Signals**

| Панель | Тип | PromQL/LogQL | Опис |
|--------|-----|--------------|------|
| Request Rate (RPS) | timeseries | `sum(rate(http_server_request_count_total[1m])) by (http_route)` | Запити/сек по endpoint |
| Error Rate | timeseries | `sum(rate(http_server_error_count_total[1m])) by (http_route)` | Помилки/сек по endpoint |
| Application Logs | logs | `{service_name="fastapi-demo"} \| level=~"$LogLevel" \|= "$TextFilter"` | Логи з фільтрами |
| Active Requests | timeseries | `http_server_active_requests` | Запити в обробці |
| Latency p50/p95/p99 | timeseries | `histogram_quantile(0.50/0.95/0.99, sum(rate(http_server_request_duration_milliseconds_bucket[5m])) by (le))` | Перцентилі латентності |
| Saturation — CPU & Memory | timeseries | `process_cpu_utilization_ratio`, `process_memory_usage_bytes` | Двоосевий графік (CPU ліворуч %, Memory праворуч bytes) |

**Row: Stats (gauge/stat panels)**

| Панель | Тип | PromQL | Опис |
|--------|-----|--------|------|
| Error Rate % | gauge | `sum(rate(error_total[5m])) / sum(rate(request_total[5m]))` | Відсоток помилок (thresholds: green < 5%, yellow < 15%, red) |
| Active Requests | stat | `sum(http_server_active_requests)` | Поточна кількість |
| Total Requests (5m) | stat | `sum(increase(request_count_total[5m]))` | Всього запитів за 5 хв |
| Total Errors (5m) | stat | `sum(increase(error_count_total[5m]))` | Всього помилок за 5 хв |

**Row: Request Breakdown**

| Панель | Тип | PromQL | Опис |
|--------|-----|--------|------|
| Requests by Endpoint | piechart (donut) | `sum by (http_route) (increase(request_count_total[5m]))` | Розподіл по endpoint |
| Errors by Status Code | piechart (donut) | `sum by (http_status_code) (increase(error_count_total[5m]))` | Розподіл помилок по коду |
| Request Rate by Status Code | timeseries (bars, stacked) | `sum(rate(request_count_total[1m])) by (http_status_code)` | RPS по статус-кодах |
