"""
OTelManager — unified OpenTelemetry setup for FastAPI.

Configures TracerProvider, MeterProvider, LoggerProvider with OTLP/HTTP exporters.
Provides golden signal instruments (latency, traffic, errors, saturation).
"""

import os
import time
import socket
from dataclasses import dataclass, field
from typing import Optional

import psutil
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.semconv.attributes import service_attributes
from opentelemetry.sdk.metrics import Counter, Histogram, ObservableGauge, UpDownCounter
from opentelemetry._logs import set_logger_provider
import logging


@dataclass
class OTelManager:
    """
    Manages OpenTelemetry instrumentation for an application, including setup for
    traces, metrics, and logs.

    The class facilitates the configuration of observability mechanisms for an
    application by interacting with OpenTelemetry tooling. It provides methods for
    instrumenting services, creating essential metrics for golden signal monitoring,
    and integrating with frameworks such as FastAPI.

    :ivar service_name: The name of the service to associate with OpenTelemetry data.
    :type service_name: str
    :ivar service_version: The version of the service to associate with OpenTelemetry data.
    :type service_version: str
    :ivar otlp_endpoint: The endpoint for OTLP (OpenTelemetry Protocol) data export.
    :type otlp_endpoint: str
    :ivar enabled: Flag to indicate whether instrumentation is enabled for the instance.
    :type enabled: bool
    """

    service_name: str = "fastapi-demo"
    service_version: str = "1.0.0"
    otlp_endpoint: str = "http://localhost:4318"
    enabled: bool = True

    # Providers (set in __post_init__)
    _tracer_provider: TracerProvider | None = field(default=None, init=False, repr=False)
    _meter_provider: MeterProvider | None = field(default=None, init=False, repr=False)
    _logger_provider: LoggerProvider | None = field(default=None, init=False, repr=False)

    # Golden signal instruments
    _request_counter: Counter | None = field(default=None, init=False, repr=False)
    _error_counter: Counter | None = field(default=None, init=False, repr=False)
    _duration_histogram: Histogram | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """
        Initializes the post-creation setup for the instance, configuring services for
        traces, metrics, logs, and instrumentation if the instance is enabled.

        The method performs the following actions:
        1. Creates a resource object with service details such as service name,
           service version, and host name.
        2. Sets up tracing, metrics, and logging mechanisms based on the resource.
        3. Creates instruments to track golden signals for the service.

        The setup is executed only if the `enabled` flag is set to True.

        :param self: Instance of the class to which the post-initialization is applied.
        :return: None
        """
        if not self.enabled:
            return

        resource = Resource.create({
            service_attributes.SERVICE_NAME: self.service_name,
            service_attributes.SERVICE_VERSION: self.service_version,
            "host.name": socket.gethostname(),
        })

        self._setup_traces(resource)
        self._setup_metrics(resource)
        self._setup_logs(resource)
        self._create_golden_signal_instruments()

    def _setup_traces(self, resource: Resource):
        """
        Configures tracing for the application by setting up a TracerProvider and span
        processing pipeline. This method initializes an OTLP span exporter and a
        batch span processor, associates them with the TracerProvider, and registers
        the TracerProvider globally.

        :param resource: The OpenTelemetry resource object to associate with the
            TracerProvider.
        :type resource: Resource
        :return: None
        """
        exporter = OTLPSpanExporter(
            endpoint=f"{self.otlp_endpoint}/v1/traces",
        )
        self._tracer_provider = TracerProvider(resource=resource)
        self._tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(self._tracer_provider)

    def _setup_metrics(self, resource: Resource):
        """
        Configures the metrics pipeline for the application using the provided resource.

        The method initializes a metrics exporter, associates it with a periodic metric
        reader, and links the reader to a meter provider. A specific view is also added
        to customize the aggregation of a metric. Finally, the meter provider is set
        globally to enable metric collection and export.

        :param resource: The resource instance that encapsulates information such as
            service name, version, and other key attributes to associate with the
            metrics.
        :type resource: Resource
        :return: None
        """
        exporter = OTLPMetricExporter(
            endpoint=f"{self.otlp_endpoint}/v1/metrics",
        )
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=15000,
        )

        duration_view = View(
            instrument_name="http.server.request.duration",
            aggregation=None,  # use default (ExplicitBucketHistogramAggregation)
        )

        self._meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
            views=[duration_view],
        )
        metrics.set_meter_provider(self._meter_provider)

    def _setup_logs(self, resource: Resource):
        """
        Configures the logging system using the OpenTelemetry framework. This method
        sets up a logger provider with the given resource and associates it with a
        batch log record processor. Logs are exported using the OTLP exporter to
        the defined endpoint.

        :param resource: The OpenTelemetry Resource to be associated with the logger provider.
        :type resource: Resource
        """
        exporter = OTLPLogExporter(
            endpoint=f"{self.otlp_endpoint}/v1/logs",
        )
        self._logger_provider = LoggerProvider(resource=resource)
        self._logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(self._logger_provider)

    def _create_golden_signal_instruments(self):
        """
        Initializes and registers golden signal monitoring instruments for HTTP servers.

        These instruments include metrics for tracking traffic, errors, latency,
        CPU utilization, and memory usage. The metrics are collected using
        the specific instruments provided by the associated meter.

        :raises Exception: If any error occurs during the metric initialization.

        :return: None
        """
        meter = self.get_meter()

        # Traffic
        self._request_counter = meter.create_counter(
            name="http.server.request.count",
            description="Total number of HTTP requests",
            unit="1",
        )

        # Errors
        self._error_counter = meter.create_counter(
            name="http.server.error.count",
            description="Total number of HTTP errors (4xx, 5xx)",
            unit="1",
        )

        # Latency
        self._duration_histogram = meter.create_histogram(
            name="http.server.request.duration",
            description="HTTP request duration",
            unit="ms",
        )

        # Saturation — CPU
        def cpu_callback(_options):
            yield metrics.Observation(
                psutil.cpu_percent(interval=None) / 100.0,
                {"host.name": socket.gethostname()},
            )

        meter.create_observable_gauge(
            name="process.cpu.utilization",
            description="Process CPU utilization (0.0-1.0)",
            unit="1",
            callbacks=[cpu_callback],
        )

        # Saturation — Memory
        def memory_callback(_options):
            process = psutil.Process(os.getpid())
            yield metrics.Observation(
                process.memory_info().rss,
                {"host.name": socket.gethostname()},
            )

        meter.create_observable_up_down_counter(
            name="process.memory.usage",
            description="Process resident memory usage in bytes",
            unit="By",
            callbacks=[memory_callback],
        )

    def instrument_fastapi(self, app):
        """
        Instrument a FastAPI application with observability features, such as tracking
        HTTP request counters, durations, and errors, using OpenTelemetry.

        :param app: The FastAPI application instance to be instrumented (of type
            ``FastAPI``). This instance will be enhanced with middleware for
            collecting observability metrics.
        :return: None. This function does not return any value.

        """
        if not self.enabled:
            return

        FastAPIInstrumentor.instrument_app(app)

        @app.middleware("http")
        async def otel_golden_signals_middleware(request, call_next):
            start = time.time()
            response = await call_next(request)
            duration_ms = (time.time() - start) * 1000

            route = request.scope.get("route")
            http_route = route.path if route else request.url.path

            attrs = {
                "http.method": request.method,
                "http.route": http_route,
                "http.status_code": response.status_code,
            }

            self._request_counter.add(1, attrs)
            self._duration_histogram.record(duration_ms, attrs)

            if response.status_code >= 400:
                self._error_counter.add(1, attrs)

            return response

    def get_tracer(self, name: str = None) -> trace.Tracer:
        """
        Retrieves a tracer instance to record tracing information. The tracer can either
        be provided by an external tracer provider if enabled, or falls back to a default
        tracer using the given tracer name or the service name.

        :param name: Optional name of the tracer. If not provided, the service name
            will be used as the tracer name.
        :type name: str, optional
        :return: A tracer instance from the tracer provider if enabled, or a default
            tracer otherwise.
        :rtype: trace.Tracer
        """
        tracer_name = name or self.service_name
        if not self.enabled or self._tracer_provider is None:
            return trace.get_tracer(tracer_name)
        return self._tracer_provider.get_tracer(tracer_name)

    def get_meter(self, name: str = None) -> metrics.Meter:
        """
        Retrieves a meter instance associated with the given name, or defaults to the
        service name if no name is provided. A meter is used to collect and manage
        metrics. If the meter provider is disabled or not available, a default meter
        is returned.

        :param name: The name of the meter to retrieve. If not provided, the service
            name will be used as the default.
        :type name: str, optional

        :return: The meter instance associated with the specified name, either
            provided by the meter provider or a default meter.
        :rtype: metrics.Meter
        """
        meter_name = name or self.service_name
        if not self.enabled or self._meter_provider is None:
            return metrics.get_meter(meter_name)
        return self._meter_provider.get_meter(meter_name)

    def get_logging_handler(self, log_level: int = logging.INFO, handler_class=None) -> Optional[logging.Handler]:
        """
        Retrieves a logging handler instance configured with the specified log level and handler
        class. If the logging subsystem is disabled or the logger provider is not defined, the method
        will return None. This function provides an easy way to integrate a logging handler into
        the OpenTelemetry SDK's logging system.

        :param log_level: The logging level at which the handler should operate. Defaults to
            `logging.INFO` if not specified.
        :type log_level: int
        :param handler_class: The class of the handler to be used. If not provided, defaults to
            `opentelemetry.sdk._logs.LoggingHandler`.
        :type handler_class: Optional[Type[logging.Handler]]
        :return: An instance of the specified logging handler class if the logging subsystem is
            enabled and logger provider is present. Returns None otherwise.
        :rtype: Optional[logging.Handler]
        """
        if not self.enabled or self._logger_provider is None:
            return None
        if handler_class is None:
            from opentelemetry.sdk._logs import LoggingHandler
            handler_class = LoggingHandler
        return handler_class(
            level=log_level,
            logger_provider=self._logger_provider,
        )

    def shutdown(self):
        """Gracefully flush and shut down all providers."""
        if self._tracer_provider:
            self._tracer_provider.shutdown()
        if self._meter_provider:
            self._meter_provider.shutdown()
        if self._logger_provider:
            self._logger_provider.shutdown()
