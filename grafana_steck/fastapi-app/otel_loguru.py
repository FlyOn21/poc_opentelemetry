"""
OTel + Loguru integration — reusable across projects.

Provides:
- format_traceback(exc) — loguru-enhanced traceback for Tempo span events
- LoguruOTelHandler — loguru sink that exports logs via OTLP with Tempo trace URL
"""

import json
import os
from urllib.parse import quote

from loguru._better_exceptions import ExceptionFormatter
from opentelemetry import trace as _otel_trace
from opentelemetry.sdk._logs import LoggingHandler as _BaseOTelHandler

GRAFANA_BASE_URL = os.getenv("GRAFANA_BASE_URL", "http://localhost:3000")
GRAFANA_TEMPO_DS_UID = os.getenv("GRAFANA_TEMPO_DS_UID", "tempo")
_LEVEL_COLORS = {"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "red"}

_exc_formatter = ExceptionFormatter(colorize=False, backtrace=True, diagnose=True)




def _otel_color_format(record):
    """
    Formats a log record into a color-coded string based on the log level.

    This function takes a logging record and applies color formatting to it
    using predefined level colors. If a color mapping is found for the
    record's level, the formatted string will include the color. Otherwise,
    a default non-colored format will be applied.

    :param record: A dictionary representing a logging record. It must include
        the keys `level` (an object with a `name` attribute) and `message` (a string).
    :return: A string representing the formatted log record,
        optionally color-coded based on the log level.
    :rtype: str
    """
    color = _LEVEL_COLORS.get(record["level"].name, "")
    if color:
        return f"<{color}>[{{level}}] {{message}}</{color}>\n"
    return "[{level}] {message}\n"


def format_traceback(exc: BaseException) -> str:
    """
    Formats the traceback information of an exception into a string.

    Takes an exception instance and processes its traceback into a formatted
    string, making it easier to read or record for debugging purposes.

    :param exc: The exception instance whose traceback needs to be formatted.
    :type exc: BaseException
    :return: A formatted string containing the exception's traceback details.
    :rtype: str
    """
    return "".join(_exc_formatter.format_exception(type(exc), exc, exc.__traceback__))


def _build_tempo_url(trace_id_hex: str) -> str:
    """
    Constructs the URL for viewing trace information within the Grafana Tempo
    interface. This method generates a URL to explore a specific trace identified
    by its hexadecimal ID, using the specified Grafana data source and time range.

    :param trace_id_hex: A string representing the hexadecimal trace ID to be queried.
    :return: A string containing the URL for accessing the trace in Grafana Tempo.
    """
    panes = json.dumps({
        "datasource": GRAFANA_TEMPO_DS_UID,
        "queries": [{"refId": "A", "queryType": "traceql", "query": trace_id_hex}],
        "range": {"from": "now-1h", "to": "now"},
    }, separators=(",", ":"))
    return f"{GRAFANA_BASE_URL}/explore?orgId=1&left={quote(panes)}"


class LoguruOTelHandler(_BaseOTelHandler):
    """
    Handler that integrates Loguru logging with OpenTelemetry for advanced logging and tracing.

    This class customizes how Loguru log records are transformed into OpenTelemetry-compatible
    attributes. It manages exception metadata and links it to OpenTelemetry traces, providing
    a streamlined approach to log management and troubleshooting. The handler supports linking
    logs to Grafana Tempo for enhanced traceability while ensuring key exception details are
    available directly in logging systems like Loki.

    :ivar formatter: Formatter responsible for formatting log records.
    :type formatter: Callable
    :ivar level: Minimum log level to process.
    :type level: int
    """

    def _get_attributes(self, record):
        attrs = _BaseOTelHandler._get_attributes(record)

        # --- Old approach: full loguru traceback in Loki exception_stacktrace ---
        # # 1) If exc_info present (from logger.opt(exception=exc)), enhance traceback
        # if record.exc_info and record.exc_info[2] is not None:
        #     attrs["exception.stacktrace"] = "".join(
        #         _exc_formatter.format_exception(*record.exc_info)
        #     )
        #
        # # 2) Hidden exception via logger.bind(_otel_exc=exc) — no console traceback
        # extra = attrs.get("extra")
        # if isinstance(extra, dict):
        #     otel_exc = extra.pop("_otel_exc", None)
        #     if otel_exc is not None and getattr(otel_exc, "__traceback__", None) is not None:
        #         attrs["exception.type"] = type(otel_exc).__name__
        #         if hasattr(otel_exc, "args") and otel_exc.args:
        #             attrs["exception.message"] = str(otel_exc.args[0])
        #         attrs["exception.stacktrace"] = "".join(
        #             _exc_formatter.format_exception(type(otel_exc), otel_exc, otel_exc.__traceback__)
        #         )

        extra = attrs.get("extra")
        if isinstance(extra, dict):
            otel_exc = extra.pop("_otel_exc", None)
            if otel_exc is not None:
                attrs["exception.type"] = type(otel_exc).__name__
                if hasattr(otel_exc, "args") and otel_exc.args:
                    attrs["exception.message"] = str(otel_exc.args[0])
                span_ctx = _otel_trace.get_current_span().get_span_context()
                if span_ctx.trace_id:
                    trace_id_hex = format(span_ctx.trace_id, "032x")
                    attrs["exception.trace_url"] = _build_tempo_url(trace_id_hex)

        return attrs