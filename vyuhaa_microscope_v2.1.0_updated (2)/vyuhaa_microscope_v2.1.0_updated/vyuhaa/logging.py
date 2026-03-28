"""Configure logging for the Vyuhaa Server.

A module containing a custom logging handler and helper function for
the Vyuhaa server. These handle formatting logs, ensuring that logs
are logged both to a file and to the terminal (see note below!). Functionality
is also provided for retrieving logs for to send over HTTP.

Note that when running in production Vyuhaa Microscope the server is run via
``systemd``. As such, the logs that are sent to the terminal (STDERR) or any other
output to STDOUT/STDERR are captured by ``systemd``. These can be viewed by using
``journalctl`` or the ``ofm journal`` command.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

LOGGER = logging.getLogger(__name__)
Vyuhaa_LOG_FILE: Optional[str] = None


def configure_logging(log_folder: str) -> None:
    """Configure logging for the server while it is running.

    This modifies the root logger to have a rotating file handler and
    adds a custom handler that prints and stores all records except
    ``uvicorn.access`` logs.

    It is important not to let Uvicorn override these settings.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Explicitly make Vyuhaa_LOG_FILE a global so it can be updated based on log settings
    # This requires silencing PLW0603 which disallows globals.
    global Vyuhaa_LOG_FILE  # noqa: PLW0603
    Vyuhaa_LOG_FILE = os.path.join(log_folder, "vyuhaa_microscope.log")

    # Add Vyuhaa_HANDLER first so it can capture the error log if the
    # log file can't be accessed.
    ofm_format_str = "[%(asctime)s] [%(levelname)s] %(message)s"
    Vyuhaa_HANDLER.setFormatter(logging.Formatter(ofm_format_str))
    Vyuhaa_HANDLER.addFilter(UvicornAccessFilter())
    root_logger.addHandler(Vyuhaa_HANDLER)

    try:
        if not os.path.exists(log_folder):
            os.makedirs(log_folder)
        handler = RotatingFileHandler(
            filename=Vyuhaa_LOG_FILE,
            mode="a",
            maxBytes=1000000,
            backupCount=10,
        )
        format_str = "[%(asctime)s] [%(levelname)s] <%(name)s> %(message)s"
        handler.setFormatter(VyuhaaLogFileFormatter(format_str))
        handler.addFilter(UvicornAccessFilter())
        root_logger.addHandler(handler)

    except PermissionError as e:
        LOGGER.error(f"Cannot create log file at {Vyuhaa_LOG_FILE}: {e}")

    LOGGER.info("Vyuhaa server root logger has been set up at INFO level")


def retrieve_log() -> PlainTextResponse:
    """Return logs since the server started, up to a maximum of 250 messages.

    This log is the one shown in the UI and on the logging page.

    It does not include any of the ``uvicorn.access`` logs as these are emitted
    every time any API route is called.

    All logs, including ``uvicorn.access`` are logged to the Vyuhaa_LOG_FILE (see above)
    this is the best place to get logs about crashes.
    """
    return PlainTextResponse(Vyuhaa_HANDLER.log_history)


def retrieve_log_from_file() -> PlainTextResponse:
    """Return the full log from the file for downloading.

    Note this is read and then sent as otherwise it causes a RuntimeError if it
    is written to while sending through FileResponse.
    """
    if Vyuhaa_LOG_FILE is None:
        raise HTTPException(
            500, "Cannot retrieve log file as logging directory hasn't been configured."
        )
    try:
        with open(Vyuhaa_LOG_FILE, "r", encoding="utf-8") as logfile:
            full_log = logfile.read()
        return PlainTextResponse(full_log)
    except IOError as e:
        raise HTTPException(
            500, "An error occurred while trying to access the log file."
        ) from e


class VyuhaaLogFileFormatter(logging.Formatter):
    """The formatter used for the Vyuhaa Microscope Server log file."""

    def format(self, record: logging.LogRecord) -> str:
        """Adjust the logging formatting for uvicorn logs.

        uvicorn has two loggers. Each API access is ``uvicorn.access`` which we filter
        due to the noise. The other is ``uvicorn.error`` for more important messages.
        However, ``uvicorn.error`` is used for expected messages that are not errors,
        such as server start up. This can lead to people erroneously thinking that
        there is an error with their miroscope.
        """
        if record.name == "uvicorn.error" and record.levelno < logging.ERROR:
            record.name = "uvicorn"
        return super().format(record)


class VyuhaaHandler(logging.Handler):
    """A logging.Handler that stores the most recent logs for access by the server."""

    def __init__(self, level: int = logging.INFO, max_logs: int = 250) -> None:
        """Initialise the handler with a set logging level and message buffer size.

        :param level: The level of logs captured. As standard logs of INFO and above
            are captured.
        :param max_logs: The maximum number of logs to hold in memory. This sets
            how many can be returned over HTTP.
        """
        super().__init__(level=level)
        self._log: list[str] = []
        self._max_logs = max_logs

    def append_record(self, record: logging.LogRecord) -> None:
        """Format message and append it to a list of records.

        The built in formatter is used to format the record.

        Any records in excess of the maximum number of logs are removed.
        """
        self._log.append(self.format(record))
        while len(self._log) > self._max_logs:
            self._log.pop(0)

    def emit(self, record: logging.LogRecord) -> None:
        """Emit will save the logged record to the log."""
        try:
            if record.levelno >= self.level:
                self.append_record(record)
        except RecursionError as e:
            raise e
        except BaseException:
            # Never let the logger crash unless there is somehow recursion.
            # Use built in logging error handler
            self.handleError(record)

    @property
    def log_history(self) -> str:
        """Return the log history up to the maximum number of logs."""
        return "\n".join(self._log)


class UvicornAccessFilter(logging.Filter):
    """A logging filter to filter out "uvicorn.access" messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False if record is from "uvicorn.access"."""
        return not record.name.startswith("uvicorn.access")


Vyuhaa_HANDLER = VyuhaaHandler()
