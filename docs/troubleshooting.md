# Вирішення проблем (Troubleshooting)

## Таблиця портів

| Порт | Сервіс | Протокол | Перевірка |
|------|--------|----------|-----------|
| 8000 | FastAPI | HTTP | `curl http://localhost:8000/health` |
| 4317 | OTel Collector | gRPC | `grpcurl -plaintext localhost:4317 list` |
| 4318 | OTel Collector | HTTP | `curl http://localhost:4318/v1/traces` (405 = alive) |
| 3200 | Tempo | HTTP | `curl http://localhost:3200/ready` |
| 3100 | Loki | HTTP | `curl http://localhost:3100/ready` |
| 9090 | Prometheus | HTTP | `curl http://localhost:9090/-/ready` |
| 3000 | Grafana | HTTP | `curl http://localhost:3000/api/health` |

---

## Сервіс не стартує

### Перевірка статусу всіх контейнерів

```bash
docker-compose ps
# або
podman-compose ps
```

Всі сервіси повинні мати статус `Up`. Якщо щось `Exited` або `Restarting`:

```bash
# Логи конкретного сервісу
docker logs fastapi-demo
docker logs otel-collector
docker logs tempo
docker logs loki
docker logs prometheus
docker logs grafana
```

### Порт зайнятий

```bash
# Знайти процес на порту
sudo lsof -i :8000
# або
sudo ss -tulnp | grep 8000
```

Рішення: зупинити процес або змінити порт в `docker-compose.yml`.

### Залежності не готові

OTel Collector залежить від Tempo, Loki, Prometheus. Якщо бекенди ще не стартували:

```bash
# Перезапустити тільки OTel Collector
docker-compose restart otel-collector

# Або перезапустити все
docker-compose down && docker-compose up -d
```

---

## Немає метрик в Prometheus

### 1. Перевірити що FastAPI надсилає метрики

```bash
# Логи FastAPI — шукати помилки відправки
docker logs fastapi-demo 2>&1 | grep -i "metric\|export\|error"
```

### 2. Перевірити OTel Collector → Prometheus

```bash
# Логи OTel Collector
docker logs otel-collector 2>&1 | grep -i "prometheus\|error\|dropped"
```

Типові помилки:
- `connection refused` — Prometheus не стартував або remote write не увімкнений
- `400 Bad Request` — невалідні метрики

### 3. Перевірити що remote write receiver увімкнений

Prometheus повинен стартувати з прапором `--web.enable-remote-write-receiver`. Перевірити:

```bash
docker logs prometheus 2>&1 | grep "remote"
```

### 4. Перевірити метрики в Prometheus UI

Відкрити http://localhost:9090 і виконати запит:

```promql
# Перевірити наявність метрик
http_server_request_count_total
http_server_request_duration_milliseconds_bucket
process_cpu_utilization_ratio
process_memory_usage_bytes
http_server_active_requests
```

### 5. Правильні назви метрик

OTel SDK конвертує назви метрик при експорті в Prometheus:

| OTel name | Prometheus name |
|-----------|-----------------|
| `http.server.request.count` | `http_server_request_count_total` |
| `http.server.error.count` | `http_server_error_count_total` |
| `http.server.request.duration` (ms) | `http_server_request_duration_milliseconds_bucket` |
| `process.cpu.utilization` | `process_cpu_utilization_ratio` |
| `process.memory.usage` (By) | `process_memory_usage_bytes` |
| `http.server.active_requests` (auto) | `http_server_active_requests` |

---

## Немає логів в Loki

### 1. Перевірити LoguruOTelHandler

В логах FastAPI має бути відсутнє повідомлення "OTel logging handler is None" (це означає що handler створився):

```bash
docker logs fastapi-demo 2>&1 | head -20
```

### 2. Перевірити OTel Collector → Loki

```bash
docker logs otel-collector 2>&1 | grep -i "loki\|log\|error"
```

Типові помилки:
- `connection refused` — Loki не стартував
- `429 Too Many Requests` — rate limiting в Loki

### 3. Перевірити Loki API напряму

```bash
# Всі labels
curl -s http://localhost:3100/loki/api/v1/labels | python3 -m json.tool

# Запит логів
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="fastapi-demo"}' \
  --data-urlencode 'limit=5' | python3 -m json.tool
```

Якщо `service_name` label не з'являється, перевірити `otlp_config` в `loki.yaml`:

```yaml
limits_config:
  allow_structured_metadata: true
  otlp_config:
    resource_attributes:
      attributes_config:
        - action: index_label
          attributes:
            - service.name
```

### 4. Згенерувати тестові дані

```bash
# Кілька запитів для генерації логів
for i in $(seq 1 10); do curl -s http://localhost:8000/random-error; done
```

---

## Немає трейсів в Tempo

### 1. Перевірити OTel Collector → Tempo

```bash
docker logs otel-collector 2>&1 | grep -i "tempo\|trace\|error"
```

### 2. Перевірити Tempo API

```bash
# Tempo ready?
curl http://localhost:3200/ready

# Пошук трейсів (потрібен trace_id)
curl http://localhost:3200/api/traces/{trace_id}
```

### 3. Знайти trace_id

trace_id видно в логах Loki (structured metadata). Або в логах FastAPI:

```bash
# Зробити запит і подивитися логи
curl http://localhost:8000/items/42
docker logs fastapi-demo --tail 5
```

### 4. Перевірити retention

Tempo зберігає трейси 1 годину (`block_retention: 1h`). Старіші трейси видаляються.

---

## Grafana: datasource не працює

### 1. Перевірити datasources

Grafana → Configuration → Data Sources. Всі три datasources повинні мати зелений індикатор "Data source is working".

### 2. Тест з'єднання

```bash
# Grafana API — список datasources
curl -s http://admin:admin@localhost:3000/api/datasources | python3 -m json.tool
```

### 3. Provisioning не застосувався

Якщо datasources не з'являються:

```bash
# Перевірити логи Grafana
docker logs grafana 2>&1 | grep -i "provisioning\|error\|datasource"

# Перевірити що файл монтується
docker exec grafana ls -la /etc/grafana/provisioning/datasources/
docker exec grafana cat /etc/grafana/provisioning/datasources/datasources.yaml
```

---

## Grafana: дашборд не з'являється

### 1. Перевірити provisioning

```bash
docker logs grafana 2>&1 | grep -i "dashboard\|provision"
```

### 2. Перевірити файли

```bash
docker exec grafana ls -la /etc/grafana/dashboards/
```

Повинен бути файл `fastapi-otel.json`.

### 3. JSON syntax error

```bash
# Валідація JSON
python3 -m json.tool grafana/dashboards/fastapi-otel.json > /dev/null
```

---

## Grafana: derivedFields (Loki → Tempo) не працює

Клікабельна лінка "View trace in Tempo" не з'являється в логах.

### 1. Перевірити що trace_id є в логах

В Grafana Explore → Loki → `{service_name="fastapi-demo"}` → розгорнути лог-запис → перевірити наявність поля `trace_id` в Detected fields або Labels.

### 2. Перевірити derivedFields конфігурацію

В `grafana/datasources.yaml`:

```yaml
derivedFields:
  - matcherType: label
    matcherRegex: 'trace_id'          # ПРОСТО назва поля, НЕ ${trace_id}
    name: TraceID
    url: '...'
    openInNewTab: true
```

**Часта помилка**: `matcherRegex: '${trace_id}'` — НЕ працює. Має бути просто `trace_id`.

### 3. Перевірити URL

URL повинен містити `$$` для escape `$` в YAML provisioning:

```yaml
url: '/explore?orgId=1&left=...%22$${__value.raw}%22...'
```

`$$` в provisioning YAML → `$` в Grafana → `${__value.raw}` → підставляється trace_id.

### 4. Перезапустити Grafana

Після зміни `datasources.yaml`:

```bash
docker-compose restart grafana
```

---

## Grafana: Tempo → Loki лінка не працює

При перегляді трейсу в Tempo немає кнопки "Logs for this trace".

### 1. Перевірити tracesToLogsV2

В `grafana/datasources.yaml` для Tempo datasource:

```yaml
jsonData:
  tracesToLogsV2:
    datasourceUid: loki
    customQuery: true
    query: '{service_name="fastapi-demo"} | trace_id = "$${__trace.traceId}"'
```

### 2. Перевірити що trace_id є в Loki

```bash
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="fastapi-demo"} | trace_id = "ВСТАВИТИ_TRACE_ID"' \
  --data-urlencode 'limit=5' | python3 -m json.tool
```

---

## Docker: контейнер OOM (Out of Memory)

### Симптоми

```bash
docker-compose ps
# Контейнер в статусі "Exited (137)" — killed by OOM
```

### Рішення

Збільшити `mem_limit` в `docker-compose.yml`:

```yaml
# Поточні ліміти:
fastapi:        256m
otel-collector: 256m
tempo:          512m
loki:           512m
prometheus:     512m
grafana:        256m
```

Для Tempo та Loki при великих обсягах даних може знадобитися 1024m+.

### Перевірити використання пам'яті

```bash
docker stats --no-stream
```

---

## Docker: rebuild після змін коду

Після зміни Python коду (`main.py`, `otel_manager.py`, etc.):

```bash
# Rebuild тільки FastAPI контейнер
docker-compose up -d --build fastapi

# Rebuild всього
docker-compose up -d --build
```

**Важливо**: зміни конфігураційних файлів (yaml) не потребують rebuild, тільки restart:

```bash
docker-compose restart otel-collector
docker-compose restart tempo
# і т.д.
```

---

## Повний reset

Якщо нічого не допомагає — повний reset з видаленням volumes:

```bash
docker-compose down -v
docker-compose up -d --build
```

**Увага**: `-v` видаляє всі volumes (метрики, логи, трейси, налаштування Grafana).
