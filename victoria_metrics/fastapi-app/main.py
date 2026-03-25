import json
import os
import time
import random
import asyncio

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
# from loguru import logger
from opentelemetry import trace
from opentelemetry.trace import StatusCode
import logging as _logging

from custom_logger import create_otel_logger
from otel_loguru import LoguruOTelHandler, format_traceback, _otel_color_format
from otel_manager import OTelManager

logger = create_otel_logger(
    name="main-fastapi-demo", logging_level="DEBUG", backtrace=True, logging_diagnose=True)

# --- OpenTelemetry setup ---

otel = OTelManager(
    service_name=os.getenv("OTEL_SERVICE_NAME", "fastapi-demo"),
    service_version=os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
    otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
    enabled=os.getenv("OTEL_ENABLED", "false").lower() == "true",
)

tracer = otel.get_tracer()


otel_handler = otel.get_logging_handler(log_level=_logging.DEBUG, handler_class=LoguruOTelHandler)
if otel_handler:
    logger.add(otel_handler, level="DEBUG", format=_otel_color_format, colorize=True)

# --- FastAPI application ---

app = FastAPI(title="FastAPI OpenTelemetry Demo")

# Instrument with OTel auto-tracing and golden signals middleware
otel.instrument_fastapi(app)


MAX_BODY_ATTR = 4096  # max characters for request/response body in span attributes


@app.middleware("http")
async def log_requests(request: Request, call_next):
    span = trace.get_current_span()
    start = time.time()

    # --- Request data → span attributes ---
    span.set_attribute("http.request.headers", json.dumps(dict(request.headers), ensure_ascii=False)[:MAX_BODY_ATTR])
    if str(request.query_params):
        span.set_attribute("http.request.query", str(request.query_params)[:MAX_BODY_ATTR])
    request_body = await request.body()
    if request_body:
        span.set_attribute("http.request.body", request_body.decode("utf-8", errors="replace")[:MAX_BODY_ATTR])

    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 2)

    # --- Response data → span attributes ---
    response_body = b""
    async for chunk in response.body_iterator:
        response_body += chunk
    span.set_attribute("http.response.headers", json.dumps(dict(response.headers), ensure_ascii=False)[:MAX_BODY_ATTR])
    span.set_attribute("http.response.body", response_body.decode("utf-8", errors="replace")[:MAX_BODY_ATTR])

    # --- Logging ---
    msg = "{method} {path} → {status} ({duration:.2f}ms)"
    kwargs = dict(method=request.method, path=request.url.path, status=response.status_code, duration=duration_ms)
    if response.status_code >= 500:
        logger.error(msg, **kwargs)
    elif response.status_code >= 400:
        logger.warning(msg, **kwargs)
    else:
        logger.info(msg, **kwargs)

    return Response(
        content=response_body,
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items()
                 if k.lower() not in ("content-length", "transfer-encoding")},
    )


@app.get("/")
async def root():
    return {"message": "Hello from FastAPI + OpenTelemetry demo", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/items/{item_id}")
async def get_item(item_id: str):
    span = trace.get_current_span()
    span.set_attribute("app.item_id", item_id)

    if item_id == "error":
        logger.error("Requested item triggered an error: item_id={}", item_id)
        raise HTTPException(status_code=500, detail="Simulated error")

    if not item_id.isdigit():
        logger.warning("Non-numeric item_id received: {}", item_id)
        raise HTTPException(status_code=400, detail="item_id must be numeric or 'error'")

    logger.info("Item retrieved successfully: item_id={}", item_id)
    return {"item_id": int(item_id), "name": f"Item {item_id}", "price": 9.99}


@app.get("/slow")
async def slow_endpoint():
    """Demo endpoint: random latency (0.1-2.0s) to show histogram distribution."""
    delay = random.uniform(0.1, 2.0)
    span = trace.get_current_span()
    span.set_attribute("app.delay_seconds", round(delay, 3))

    logger.info("Slow endpoint called, sleeping {:.3f}s", delay)
    await asyncio.sleep(delay)

    return {"message": "Slow response", "delay_seconds": round(delay, 3)}


@app.post("/post_test")
async def post_test_endpoint(data: dict):
    """Demo endpoint: accepts JSON body and returns it, to show request body in spans."""
    raise HTTPException(status_code=400, detail="some problem")
    return {"message": "Received POST data", "data": data}


@app.get("/chain")
async def chain_endpoint():
    """Demo endpoint: calls /items/{random_id} to demonstrate distributed tracing."""
    import httpx

    item_id = random.choice(["1", "2", "42", "error", "abc"])
    span = trace.get_current_span()
    span.set_attribute("app.chained_item_id", item_id)

    base_url = os.getenv("CHAIN_TARGET_URL", "http://localhost:8000")
    url = f"{base_url}/items/{item_id}"

    logger.info("Chain endpoint calling {}", url)

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    return {
        "chain_target": url,
        "chain_status": response.status_code,
        "chain_body": response.json(),
    }


@app.get("/random-error")
async def random_error_endpoint():
    """Demo endpoint: randomly returns 200/400/500 to show error rate metrics."""
    outcome = random.choices(
        ["ok", "bad_request", "server_error"],
        weights=[70, 15, 15],
        k=1,
    )[0]

    span = trace.get_current_span()
    span.set_attribute("app.outcome", outcome)

    if outcome == "bad_request":
        logger.warning("Random error endpoint: bad request")
        raise HTTPException(status_code=400, detail="Random bad request")
    elif outcome == "server_error":
        logger.error("Random error endpoint: server error")
        raise HTTPException(status_code=500, detail="Random server error")

    logger.info("Random error endpoint: success")
    return {"message": "Success", "outcome": outcome}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    span = trace.get_current_span()
    span.record_exception(exc, attributes={"exception.stacktrace": format_traceback(exc)})
    span.set_status(StatusCode.ERROR, str(exc.detail))
    logger.bind(_otel_exc=exc).error(
        "HTTP {status}: {method} {path} — {detail}",
        status=exc.status_code,
        method=request.method,
        path=request.url.path,
        detail=exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    span = trace.get_current_span()
    span.record_exception(exc, attributes={"exception.stacktrace": format_traceback(exc)})
    span.set_status(StatusCode.ERROR, str(exc))
    logger.bind(_otel_exc=exc).error(
        "Unhandled exception: {method} {path}",
        method=request.method,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


@app.on_event("startup")
async def startup_event():
    """Send Grafana annotation marking that FastAPI server is ready."""
    import httpx

    grafana_url = os.getenv("GRAFANA_BASE_URL", "http://grafana:3000")
    grafana_user = os.getenv("GF_SECURITY_ADMIN_USER", "admin")
    grafana_password = os.getenv("GF_SECURITY_ADMIN_PASSWORD", "admin")

    annotation = {
        "dashboardUID": "fastapi-otel-golden",
        "text": "FastAPI server started and ready",
        "tags": ["deployment", "fastapi", "startup"],
    }

    async def _send_annotation():
        """Retry sending annotation until Grafana is available."""
        for attempt in range(10):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{grafana_url}/api/annotations",
                        json=annotation,
                        auth=(grafana_user, grafana_password),
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        logger.info("Grafana startup annotation created successfully")
                        return
                    logger.warning("Grafana annotation response: {} {}", resp.status_code, resp.text)
            except Exception as e:
                logger.debug("Grafana not ready yet (attempt {}/10): {}", attempt + 1, e)
            await asyncio.sleep(3)
        logger.warning("Could not create Grafana startup annotation after 10 attempts")

    asyncio.create_task(_send_annotation())


@app.on_event("shutdown")
async def shutdown_event():
    otel.shutdown()
