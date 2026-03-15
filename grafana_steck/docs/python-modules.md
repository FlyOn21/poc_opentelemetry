# Python модулі (FastAPI додаток)

## Огляд

FastAPI додаток складається з 4 Python модулів:

| Файл | Призначення |
|------|-------------|
| `main.py` | FastAPI app, endpoints, middleware, exception handlers |
| `otel_manager.py` | OTelManager — unified setup для traces, metrics, logs |
| `custom_logger.py` | Loguru logger, InterceptHandler, stdlib integration |
| `otel_loguru.py` | LoguruOTelHandler sink, format_traceback, Tempo URL |

---

## main.py

Головний файл FastAPI додатку. Ініціалізує OTel, логування, визначає endpoints та middleware.

### Ініціалізація

```python
# 1. Створення loguru logger
logger = create_unified_logger(
    name="main-fastapi-demo", logging_level="DEBUG",
    backtrace=True, logging_diagnose=True
)

# 2. Ініціалізація OTelManager (з env vars)
otel = OTelManager(
    service_name=os.getenv("OTEL_SERVICE_NAME", "fastapi-demo"),
    service_version=os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
    otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
    enabled=os.getenv("OTEL_ENABLED", "false").lower() == "true",
)

# 3. Додавання OTel sink до loguru (для відправки логів в Loki)
otel_handler = otel.get_logging_handler(log_level=logging.DEBUG, handler_class=LoguruOTelHandler)
if otel_handler:
    logger.add(otel_handler, level="DEBUG", format=_otel_color_format, colorize=True)

# 4. Інструментація FastAPI (auto-tracing + golden signals middleware)
otel.instrument_fastapi(app)
```

### Endpoints

| Endpoint | Метод | Опис | Особливості |
|----------|-------|------|-------------|
| `/` | GET | Hello message | Базовий health-check |
| `/health` | GET | Health status | `{"status": "healthy"}` |
| `/items/{item_id}` | GET | Отримати item | `item_id="error"` → 500, не-число → 400 |
| `/slow` | GET | Повільний запит | Випадкова затримка 0.1-2.0s (демонстрація histogram) |
| `/chain` | GET | Ланцюговий запит | Викликає `/items/{random}` через httpx (distributed tracing) |
| `/random-error` | GET | Випадкова помилка | 70% ok / 15% 400 / 15% 500 (демонстрація error rate) |

### Middleware: log_requests

HTTP middleware, що перехоплює кожний запит та:

1. **Request phase:**
   - Зберігає `http.request.headers`, `http.request.query`, `http.request.body` як span attributes
   - Ліміт: `MAX_BODY_ATTR = 4096` символів

2. **Response phase:**
   - Зберігає `http.response.headers`, `http.response.body` як span attributes
   - Логує результат з часом виконання: `"GET /items/42 → 200 (5.23ms)"`
   - Рівень логу залежить від статус-коду: `>= 500` → error, `>= 400` → warning, інше → info

3. **Повертає** новий `Response` з прочитаним body (бо `body_iterator` consumable)

### Exception Handlers

**`http_exception_handler`** (для `HTTPException`):
```python
span.record_exception(exc, attributes={"exception.stacktrace": format_traceback(exc)})
span.set_status(StatusCode.ERROR, str(exc.detail))
logger.bind(_otel_exc=exc).error("HTTP {status}: {method} {path} — {detail}", ...)
```

**`unhandled_exception_handler`** (для всіх інших `Exception`):
- Аналогічна логіка, але повертає `500 Internal Server Error`
- Loguru-enhanced stacktrace зберігається в span event через `format_traceback()`

**Ключовий патерн**: `logger.bind(_otel_exc=exc).error(...)` — передає exception в `LoguruOTelHandler` через bind, без виведення traceback в консоль.

### Shutdown

```python
@app.on_event("shutdown")
async def shutdown_event():
    otel.shutdown()  # flush + shutdown всіх providers
```

---

## otel_manager.py

Dataclass для централізованого налаштування OpenTelemetry. Створює та конфігурує TracerProvider, MeterProvider, LoggerProvider.

### OTelManager

```python
@dataclass
class OTelManager:
    service_name: str = "fastapi-demo"
    service_version: str = "1.0.0"
    otlp_endpoint: str = "http://localhost:4318"
    enabled: bool = True
```

### `__post_init__()` — автоматична ініціалізація

При створенні об'єкта (якщо `enabled=True`):

1. Створює `Resource` з атрибутами:
   - `service.name` (SERVICE_NAME)
   - `service.version` (SERVICE_VERSION)
   - `host.name` (socket.gethostname())

2. Викликає:
   - `_setup_traces(resource)` — TracerProvider + BatchSpanProcessor + OTLPSpanExporter
   - `_setup_metrics(resource)` — MeterProvider + PeriodicExportingMetricReader + OTLPMetricExporter
   - `_setup_logs(resource)` — LoggerProvider + BatchLogRecordProcessor + OTLPLogExporter
   - `_create_golden_signal_instruments()` — створення метрик

### Traces setup

```
OTLPSpanExporter(endpoint="{otlp_endpoint}/v1/traces")
    ▼
BatchSpanProcessor (default batch settings)
    ▼
TracerProvider (resource)
    ▼
trace.set_tracer_provider()  # глобальна реєстрація
```

### Metrics setup

```
OTLPMetricExporter(endpoint="{otlp_endpoint}/v1/metrics")
    ▼
PeriodicExportingMetricReader(export_interval_millis=15000)  # кожні 15 секунд
    ▼
MeterProvider(resource, views=[duration_view])
    ▼
metrics.set_meter_provider()  # глобальна реєстрація
```

`duration_view` — View для `http.server.request.duration` (використовує default ExplicitBucketHistogramAggregation).

### Logs setup

```
OTLPLogExporter(endpoint="{otlp_endpoint}/v1/logs")
    ▼
BatchLogRecordProcessor
    ▼
LoggerProvider(resource)
    ▼
set_logger_provider()  # глобальна реєстрація
```

### Golden Signal Instruments

Створюються через `meter = self.get_meter()`:

| Інструмент | OTel тип | Name | Unit |
|------------|----------|------|------|
| Request counter | Counter | `http.server.request.count` | `1` |
| Error counter | Counter | `http.server.error.count` | `1` |
| Duration | Histogram | `http.server.request.duration` | `ms` |
| CPU utilization | ObservableGauge | `process.cpu.utilization` | `1` |
| Memory usage | ObservableUpDownCounter | `process.memory.usage` | `By` |

CPU та Memory використовують **psutil** для зчитування поточних значень через callbacks.

### `instrument_fastapi(app)`

1. Викликає `FastAPIInstrumentor.instrument_app(app)` — автоматичний span на кожний запит
2. Додає middleware `otel_golden_signals_middleware`:
   - Записує `request_counter`, `duration_histogram` для кожного запиту
   - Записує `error_counter` якщо `status_code >= 400`
   - Attributes: `http.method`, `http.route`, `http.status_code`

### Публічні методи

| Метод | Повертає | Опис |
|-------|----------|------|
| `get_tracer(name=None)` | `trace.Tracer` | Tracer з provider або no-op |
| `get_meter(name=None)` | `metrics.Meter` | Meter з provider або no-op |
| `get_logging_handler(log_level, handler_class)` | `logging.Handler \| None` | OTel handler (або custom class) |
| `shutdown()` | `None` | Flush + shutdown всіх providers |

`get_logging_handler()` приймає `handler_class` — дозволяє підставити `LoguruOTelHandler` замість стандартного `LoggingHandler`.

---

## custom_logger.py

Централізований модуль логування. **Спільний для кількох додатків** — не змінювати публічний API.

### Константи

```python
DEFAULT_LOGGING_LEVEL = "INFO"
DEFAULT_FORMAT = "<level>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {module}:{function}:{line} - {message}</level>"
INITIAL_FRAME_DEPTH = 2
```

### InterceptHandler

Перехоплює повідомлення зі стандартного `logging` (Python stdlib) і перенаправляє їх в loguru.

**Як працює:**
1. `emit(record)` — отримує `logging.LogRecord`
2. Конвертує level name в loguru level
3. Визначає глибину стеку (`_calculate_depth`) — пропускає фрейми модуля `logging`
4. Форматує повідомлення з кольором (`_format_message`):
   - INFO → зелений (Fore.GREEN)
   - WARNING → жовтий (Fore.YELLOW)
   - ERROR/CRITICAL → червоний (Fore.RED)
   - DEBUG → блакитний (Fore.CYAN)
5. `logger.opt(depth=depth, exception=record.exc_info).log(level, message)`

**Навіщо**: uvicorn, httpx та інші бібліотеки використовують stdlib `logging`. `InterceptHandler` перенаправляє їх логи через loguru для єдиного форматування.

### `create_unified_logger()`

Створює та конфігурує loguru logger:

```python
def create_unified_logger(
    name: str = str(uuid.uuid4()),    # ім'я логгера (відображається у форматі)
    logging_level: str = "INFO",       # мінімальний рівень
    logging_format: str = DEFAULT_FORMAT,
    logging_diagnose: bool = True,     # loguru diagnose (показує значення змінних)
    sink=sys.stderr,                   # вивід (console)
    colorize: bool = True,             # кольоровий вивід
    backtrace: bool = True,            # loguru backtrace
    enqueue: bool = True,              # async logging (thread-safe)
    force_green_info: bool = True,     # INFO завжди зелений
) -> Logger:
```

**Що робить:**
1. `logger.remove()` — видаляє default sink
2. `logger.configure(extra={"name": name})` — зберігає ім'я в extra
3. Створює динамічний `formatter` з кольорами per-level (якщо `force_green_info=True`)
4. `logger.add(sink, ...)` — додає console sink з backtrace + diagnose
5. `_setup_standard_logging_integration()` — підключає InterceptHandler
6. Повертає `logger.bind(request_id=None, method=None)`

### `_setup_standard_logging_integration()`

Налаштовує перехоплення stdlib logging:

```python
def _setup_standard_logging_integration(force_green_info=True):
    intercept_handler = InterceptHandler(force_green_info=force_green_info)
    logging.basicConfig(handlers=[intercept_handler], level=0, force=True)
    for logger_name in logging.root.manager.loggerDict.keys():
        mod_logger = logging.getLogger(logger_name)
        mod_logger.handlers = [intercept_handler]
        mod_logger.propagate = False
```

Після цього ВСІ stdlib loggers (uvicorn, httpx, opentelemetry, etc.) відправляють логи через loguru.

---

## otel_loguru.py

Модуль інтеграції loguru з OpenTelemetry. Забезпечує відправку логів через OTLP та генерацію Tempo URL для кросс-лінків.

### Константи та глобальні об'єкти

```python
GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "http://localhost:3000")
GRAFANA_TEMPO_DS_UID = os.getenv("GRAFANA_TEMPO_DS_UID", "tempo")
_LEVEL_COLORS = {"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "red"}

# ExceptionFormatter з loguru — для enhanced traceback
_exc_formatter = ExceptionFormatter(colorize=False, backtrace=True, diagnose=True)
```

### `_otel_color_format(record)`

Формат для loguru sink (OTel handler). Спрощений формат тільки для OTel — `[{level}] {message}` з кольорами:

```python
def _otel_color_format(record):
    color = _LEVEL_COLORS.get(record["level"].name, "")
    if color:
        return f"<{color}>[{{level}}] {{message}}</{color}>\n"
    return "[{level}] {message}\n"
```

### `format_traceback(exc)`

Форматує traceback exception за допомогою loguru `ExceptionFormatter`:

```python
def format_traceback(exc: BaseException) -> str:
    return "".join(_exc_formatter.format_exception(type(exc), exc, exc.__traceback__))
```

**Використовується в:**
- `main.py: http_exception_handler` → `span.record_exception(exc, attributes={"exception.stacktrace": format_traceback(exc)})` — зберігає loguru-enhanced traceback в Tempo span event
- `main.py: unhandled_exception_handler` — аналогічно

**Параметри ExceptionFormatter:**
- `colorize=False` — без ANSI кольорів (для зберігання в Tempo/Loki)
- `backtrace=True` — повний ланцюжок викликів
- `diagnose=True` — значення локальних змінних

### `_build_tempo_url(trace_id_hex)`

Генерує URL для Grafana Explore з Tempo datasource:

```python
def _build_tempo_url(trace_id_hex: str) -> str:
    panes = json.dumps({
        "datasource": GRAFANA_TEMPO_DS_UID,
        "queries": [{"refId": "A", "queryType": "traceql", "query": trace_id_hex}],
        "range": {"from": "now-1h", "to": "now"},
    }, separators=(",", ":"))
    return f"{GRAFANA_BASE_URL}/explore?orgId=1&left={quote(panes)}"
```

**Приклад результату**: `http://localhost:3000/explore?orgId=1&left=%7B%22datasource%22%3A%22tempo%22...%7D`

### LoguruOTelHandler

Наслідує `opentelemetry.sdk._logs.LoggingHandler`. Перевизначає `_get_attributes(record)` для додавання exception metadata та Tempo URL.

**Потік даних:**

```
loguru.error("...", _otel_exc=exc)  ← logger.bind(_otel_exc=exc).error(...)
    │
    ▼
LoguruOTelHandler.emit(record)     ← стандартний OTel LoggingHandler
    │
    ▼
LoguruOTelHandler._get_attributes(record)
    │
    ├─ attrs = super()._get_attributes(record)  # базові OTel атрибути
    │
    ├─ extra = attrs.get("extra")
    │  └─ otel_exc = extra.pop("_otel_exc", None)
    │
    ├─ Якщо otel_exc:
    │  ├─ attrs["exception.type"] = type(otel_exc).__name__
    │  ├─ attrs["exception.message"] = str(otel_exc.args[0])
    │  └─ attrs["exception.trace_url"] = _build_tempo_url(trace_id_hex)
    │
    └─ return attrs → OTel LogRecord → OTLP → Loki
```

**Ключовий момент**: `exception.trace_url` замість повного traceback в Loki. Повний traceback зберігається тільки в Tempo (через `span.record_exception()` в exception handlers).

### Закоментований код

В `_get_attributes()` є закоментований "старий підхід" — повний loguru traceback в Loki (`exception.stacktrace`). Замінений на Tempo URL підхід для зменшення розміру логів в Loki.

---

## Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py otel_manager.py custom_logger.py otel_loguru.py .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
```

- **Base image**: `python:3.12-slim`
- **gcc**: потрібен для компіляції native-модулів (psutil)
- **Порт**: 8000

## requirements.txt

| Пакет | Версія | Призначення |
|-------|--------|-------------|
| fastapi | 0.115.6 | Web framework |
| uvicorn | 0.34.0 | ASGI server |
| python-json-logger | 3.2.1 | JSON formatting (legacy, не використовується) |
| opentelemetry-api | >= 1.27.0 | OTel API |
| opentelemetry-sdk | >= 1.27.0 | OTel SDK |
| opentelemetry-exporter-otlp-proto-http | >= 1.27.0 | OTLP/HTTP exporter |
| opentelemetry-instrumentation-fastapi | >= 0.48b0 | Авто-інструментація FastAPI |
| opentelemetry-semantic-conventions | >= 0.48b0 | Семантичні конвенції |
| psutil | >= 5.9.0 | CPU/Memory метрики |
| httpx | >= 0.27.0 | Async HTTP client (для /chain) |
| loguru | >= 0.7.0 | Логування |
| colorama | >= 0.4.6 | Кольоровий вивід (Windows + InterceptHandler) |
