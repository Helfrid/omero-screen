"""Module for handling shared message and error management for OMERO utilities.

This module provides a shared console instance for logging and printing messages,
as well as a base class for all OMERO-related errors.

Available functions:

- log_connection_success(style, message, logger_instance): Log and print a success message.
- log_success(style, message, logger_instance): Log and print a success message.
- OmeroError: Base class for all OMERO-related errors.
- OmeroConnectionError: Raised when there are issues connecting to OMERO.
- PlateNotFoundError: Raised when a plate is not found.
- PlateDataError: Raised when there is an error in the plate data.
"""

import logging
import sys
import traceback
from datetime import datetime
from typing import Optional

from omero_screen.config import get_logger
from rich.console import Console
from rich.panel import Panel

# Initialize logger with the module's name
logger = get_logger(__name__)

ERROR_STYLE = "bold red"

# Global console instance
_console: Console = Console()


def get_console() -> Console:
    """Get the shared console instance."""
    return _console


def log_connection_success(
    style: str, message: str, logger_instance: logging.Logger
) -> None:
    """Log and print a success message."""
    logger_instance.info(message)
    get_console().rule(f"[{style}] {message}: {datetime.now().ctime()}")


def log_success(
    style: str, message: str, logger_instance: logging.Logger
) -> None:
    """Log and print a success message."""
    logger_instance.info(message)
    get_console().print(f"[{style}]✓ {message}")


class OmeroError(Exception):
    """Base class for all OMERO-related errors."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the OmeroError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message)
        self.logger = logger_instance
        self.original_error = original_error
        self._log_and_display_error(message)

    def _log_and_display_error(self, message: str) -> None:
        """Log the error and display it with proper formatting."""
        # Log the error message
        self.logger.error(message)

        # Get the current exception info
        exc_type, exc_value, exc_traceback = sys.exc_info()

        # If we're handling an exception, start from its traceback
        if exc_traceback:
            # Skip the last frame (this __init__ call)
            trace_str = "".join(
                traceback.format_tb(exc_traceback.tb_next or exc_traceback)
            )
        else:
            # If no current exception, get the call stack
            stack = traceback.extract_stack()[:-1]  # Exclude current frame
            trace_str = "".join(traceback.format_list(stack))

        # Use the shared console which will have appropriate settings for terminal/file
        get_console().print(
            Panel.fit(
                f"{self.__class__.__name__}:\n{message}\n\nLocation:\n{trace_str}",
                title="Error",
                border_style="red",
            )
        )
        if self.original_error:
            get_console().print(f"Original error: {self.original_error}")


class OmeroConnectionError(OmeroError):
    """Raised when there are issues connecting to OMERO."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the OmeroConnectionError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


class PlateNotFoundError(OmeroError):
    """Raised when a plate is not found."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the PlateNotFoundError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


class PlateDataError(OmeroError):
    """Raised when there is an error in the plate data."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the PlateDataError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


class ExcelParsingError(OmeroError):
    """Raised when there are issues parsing the Excel file."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the ExcelParsingError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


class ChannelAnnotationError(OmeroError):
    """Raised when there are issues with channel annotations."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the ChannelAnnotationError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


class WellAnnotationError(OmeroError):
    """Raised when there are issues with well annotations."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the WellAnnotationError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


class MetadataValidationError(OmeroError):
    """Raised when parsed data doesn't meet requirements."""

    def __init__(
        self,
        message: str,
        logger_instance: logging.Logger,
        original_error: Optional[Exception] = None,
    ):
        """Initialize the MetadataValidationError class.

        Args:
            message: The error message.
            logger_instance: The logger instance.
            original_error: The original error.
        """
        super().__init__(message, logger_instance, original_error)


# TODO: Add styles to the console
