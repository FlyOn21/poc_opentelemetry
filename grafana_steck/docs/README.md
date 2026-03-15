# FastAPI + OpenTelemetry + Grafana Stack

PoC-проект для збору **логів**, **трейсів** та **метрик** з FastAPI додатку через OpenTelemetry Collector з візуалізацією в Grafana.

## Що це

Повний observability-стек для Python/FastAPI додатків:

- **Traces** — розподілений трейсинг HTTP-запитів (Tempo)
- **Metrics** — golden signals: traffic, errors, latency, saturation (Prometheus)
- **Logs** — структуровані логи з прив'язкою до trace_id (Loki)
- **Visualization** — єдиний дашборд з усіма сигналами + кросс-лінки між Loki та Tempo (Grafana)

## Архітектура

```
                          ┌──────────────┐
                          │   Grafana    │
                          │   :3000      │
                          └──────┬───────┘
                                 │ query
                  ┌──────────────┼──────────────┐
                  │              │              │
           ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
           │   Tempo     │ │   Loki   │ │ Prometheus  │
           │   :3200     │ │   :3100  │ │   :9090     │
           └──────▲──────┘ └────▲─────┘ └──────▲──────┘
                  │              │              │
                  │ otlphttp     │ otlphttp     │ prometheusrw
                  │              │              │
           ┌──────┴──────────────┴──────────────┴──────┐
           │          OTel Collector                    │
           │          :4317 (gRPC)  :4318 (HTTP)       │
           │   processors: memory_limiter, batch       │
           └─────────────────▲─────────────────────────┘
                             │ OTLP/HTTP
                    ┌────────┴────────┐
                    │    FastAPI      │
                    │    :8000        │
                    │  (OTel SDK)     │
                    └─────────────────┘
```

**Єдина точка входу**: FastAPI надсилає всю телеметрію (traces, metrics, logs) по OTLP/HTTP на OTel Collector, який маршрутизує дані до відповідних бекендів.

## Quick Start

### Запуск

```bash
cd docker-compose-demo
docker-compose up -d --build
# або
podman-compose up -d --build
```

### Перевірка

| Сервіс     | URL                              | Опис                    |
|------------|----------------------------------|-------------------------|
| FastAPI    | http://localhost:8000            | API додаток             |
| Grafana    | http://localhost:3000            | Дашборди (admin/admin)  |
| Prometheus | http://localhost:9090            | Метрики UI              |
| Tempo      | http://localhost:3200            | Trace query API         |
| Loki       | http://localhost:3100            | Log query API           |

### Генерація тестових даних

```bash
# Базовий запит
curl http://localhost:8000/

# Отримати item
curl http://localhost:8000/items/42

# Спровокувати помилку
curl http://localhost:8000/items/error

# Повільний запит (0.1-2.0s)
curl http://localhost:8000/slow

# Ланцюговий запит (distributed tracing)
curl http://localhost:8000/chain

# Випадкова помилка (70% ok / 15% 400 / 15% 500)
curl http://localhost:8000/random-error
```

### Зупинка

```bash
docker-compose down -v
```

## Структура проекту

```
docker-compose-demo/
├── docker-compose.yml                  # 6 сервісів
├── fastapi-app/
│   ├── main.py                         # FastAPI endpoints, middleware, exception handlers
│   ├── otel_manager.py                 # OTelManager — traces, metrics, logs providers
│   ├── custom_logger.py                # Loguru logger, InterceptHandler, stdlib integration
│   ├── otel_loguru.py                  # LoguruOTelHandler, format_traceback, Tempo URL builder
│   ├── Dockerfile                      # Python 3.12-slim
│   └── requirements.txt                # Залежності
├── otel-collector/
│   └── otel-collector.yaml             # OTLP receiver → 3 pipelines
├── tempo/
│   └── tempo.yaml                      # Trace storage (local)
├── loki/
│   └── loki.yaml                       # Log storage (TSDB + structured metadata)
├── prometheus/
│   └── prometheus.yml                  # Metrics (remote write receiver)
└── grafana/
    ├── datasources.yaml                # Loki, Tempo, Prometheus + derivedFields
    ├── dashboards-provisioning.yaml    # Dashboard auto-provisioning
    └── dashboards/
        └── fastapi-otel.json           # Golden Signals & OTel dashboard
```

## Документація

| Документ | Опис |
|----------|------|
| [architecture.md](architecture.md) | Архітектура, 6 сервісів, 3 пайплайни, lifecycle HTTP-запиту |
| [python-modules.md](python-modules.md) | Python код: main.py, otel_manager.py, custom_logger.py, otel_loguru.py |
| [configuration.md](configuration.md) | Конфігурація кожного компонента: OTel Collector, Tempo, Loki, Prometheus, Grafana |
| [troubleshooting.md](troubleshooting.md) | Вирішення типових проблем |

## Технології

| Компонент | Версія |
|-----------|--------|
| Python | 3.12 |
| FastAPI | 0.115.6 |
| OpenTelemetry SDK | >= 1.27.0 |
| OTel Collector (contrib) | 0.120.0 |
| Grafana | 11.3.0 |
| Tempo | 2.6.1 |
| Loki | 3.0.0 |
| Prometheus | 2.54.1 |
| loguru | >= 0.7.0 |
