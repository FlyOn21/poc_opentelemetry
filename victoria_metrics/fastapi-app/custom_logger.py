import logging
import sys
import uuid
from types import FrameType
from typing import Optional, Union, cast

from colorama import Fore, Style
from colorama import init as colorama_init
from loguru import logger
from loguru._logger import Logger

DEFAULT_LOGGING_LEVEL = "INFO"
DEFAULT_FORMAT = "<level>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {module}:{function}:{line} - {message}</level>"
INITIAL_FRAME_DEPTH = 2

colorama_init(autoreset=True)


class InterceptHandler(logging.Handler):
    """Efficiently redirect Python logging to loguru with colored output."""

    __slots__ = ("force_green_info",)

    def __init__(self, force_green_info: bool = True):
        """Initialize handler with green INFO configuration.

        :param force_green_info: Whether to force INFO messages to be green
        """
        super().__init__()
        self.force_green_info = force_green_info

    def emit(self, record: logging.LogRecord) -> None:
        """Redirect logging record to loguru with optimized frame detection.

        :param record: Original Python log record.
        """
        # Get loguru level, fallback to numeric level if unknown
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        frame = self._get_caller_frame()
        depth = self._calculate_depth(frame)
        message = self._format_message(record, level)
        logger.opt(depth=depth, exception=record.exc_info).log(level, message)

    @staticmethod
    def _get_caller_frame() -> Optional[FrameType]:
        """Get the current frame efficiently."""
        return logging.currentframe()

    @staticmethod
    def _calculate_depth(frame: Optional[FrameType]) -> int:
        """Calculate call stack depth, skipping logging module frames."""
        if not frame:
            return INITIAL_FRAME_DEPTH

        depth = INITIAL_FRAME_DEPTH
        current_frame = frame
        while current_frame and current_frame.f_code.co_filename == logging.__file__:
            current_frame = cast(FrameType, current_frame.f_back)
            depth += 1

        return depth

    def _format_message(self, record: logging.LogRecord, level: Union[str, int]) -> str:
        """Format message with color for INFO level if enabled."""
        message = record.getMessage()
        if level == "INFO" and self.force_green_info:
            return f"{Fore.GREEN}{message}{Style.RESET_ALL}"
        elif level == "WARNING":
            return f"{Fore.YELLOW}{message}{Style.RESET_ALL}"
        elif level in ("ERROR", "CRITICAL"):
            return f"{Fore.RED}{message}{Style.RESET_ALL}"
        elif level == "DEBUG":
            return f"{Fore.CYAN}{message}{Style.RESET_ALL}"

        return message


def create_otel_logger(
        name: str = str(uuid.uuid4()),
        logging_level: str = DEFAULT_LOGGING_LEVEL,
        logging_format: str = DEFAULT_FORMAT,
        logging_diagnose: bool = True,
        sink=sys.stderr,
        colorize: bool = True,
        backtrace: bool = True,
        enqueue: bool = True,
        force_green_info: bool = True,
) -> Logger:
    """Create optimized unified loguru logger with standard logging integration.

    :param name: Logger name displayed in output (e.g. "fastapi-demo")
    :param logging_level: Minimum logging level (default: INFO)
    :param logging_format: Log message format string
    :param logging_diagnose: Enable detailed error diagnosis
    :param sink: Output destination for logs
    :param colorize: Enable colored output
    :param backtrace: Enable backtrace on exceptions
    :param enqueue: Enable async logging for thread safety
    :param force_green_info: Force INFO messages to always be green (default: True)
    :return: Configured loguru logger instance
    """
    level = logging_level.upper()
    logger.remove()
    logger.configure(extra={"name": name})

    name_part = f" | {{extra[name]}}" if name else ""

    if force_green_info:

        def formatter(record):
            if record["level"].name == "INFO":
                format_str = (
                    "<green><level>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8}"
                    + name_part
                    + " | {module}:{function}:{line} - </level>"
                    "{message}</green>\n"
                )
            elif record["level"].name == "WARNING":
                format_str = (
                    "<yellow><level>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8}"
                    + name_part
                    + " | {module}:{function}:{line} - </level>"
                    "{message}</yellow>\n"
                )
            elif record["level"].name in ("ERROR", "CRITICAL"):
                format_str = (
                    "<red><level>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8}"
                    + name_part
                    + " | {module}:{function}:{line} - </level>"
                    "{message}</red>\n"
                )
            elif record["level"].name == "DEBUG":
                format_str = (
                    "<cyan><level>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8}"
                    + name_part
                    + " | {module}:{function}:{line} - </level>"
                    "{message}</cyan>\n"
                )
            else:
                format_str = logging_format.replace(
                    " | {module}:", name_part + " | {module}:"
                ) + "\n"
            return format_str

        logger.add(
            sink=sink,
            level=level,
            format=formatter,
            colorize=colorize,
            backtrace=backtrace,
            diagnose=logging_diagnose,
            enqueue=enqueue,
        )
    else:
        logger.add(
            sink=sink,
            level=level,
            format=logging_format,
            colorize=colorize,
            backtrace=backtrace,
            diagnose=logging_diagnose,
            enqueue=enqueue,
        )

    _setup_standard_logging_integration(force_green_info)

    return logger.bind(request_id=None, method=None)


def _setup_standard_logging_integration(force_green_info: bool = True) -> None:
    """Configure standard Python logging to use loguru via InterceptHandler."""
    intercept_handler = InterceptHandler(force_green_info=force_green_info)

    logging.basicConfig(handlers=[intercept_handler], level=0, force=True)

    existing_loggers = list(logging.root.manager.loggerDict.keys())

    for logger_name in existing_loggers:
        mod_logger = logging.getLogger(logger_name)
        mod_logger.handlers = [intercept_handler]
        mod_logger.propagate = False
