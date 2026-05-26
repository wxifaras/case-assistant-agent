"""Logging service with Application Insights integration.

Provides a centralized ``Logger`` class that can be injected into any service
via dependency injection. All telemetry is shipped to Application Insights
through OpenTelemetry when ``APPINSIGHTS_CONNECTION_STRING`` is set.
"""

import logging
import os
import time
from contextlib import contextmanager
from typing import Any

from agent_framework.observability import enable_instrumentation
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode


class Logger:
    """Centralised logging service with Application Insights integration.

    Provides structured logging with automatic telemetry to Application
    Insights when configured.

    Features:
        - Console logging with formatted output.
        - Application Insights integration via OpenTelemetry.
        - Structured logging with extra context fields.
        - Automatic exception tracking.
        - OpenTelemetry span helpers for non-MAF operations.

    Example::

        # Via dependency injection
        def __init__(self, logger: Logger):
            self.logger = logger

        # Usage
        self.logger.info("Processing started", extra={"blob_name": "test.pdf"})
        self.logger.error("Processing failed", exc_info=True)
    """

    _app_insights_configured: bool = False

    def __init__(self, name: str = "case-assistant-agent") -> None:
        """Initialise the logging service.

        Args:
            name: Logger name used for the underlying ``logging.Logger``.
        """
        self.logger: logging.Logger = logging.getLogger(name)
        self._configure_app_insights_once()
        self.tracer = trace.get_tracer(name)

    @classmethod
    def _configure_app_insights_once(cls) -> None:
        """Configure Application Insights via OpenTelemetry (called at most once).

        Uses a class-level flag so ``configure_azure_monitor`` is only invoked
        once regardless of how many ``Logger`` instances are created.
        """
        if cls._app_insights_configured:
            return

        # Configure console logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            force=True,  # Override any existing configuration.
        )
        # Configure Application Insights if connection string is available
        conn_str = os.getenv("APPINSIGHTS_CONNECTION_STRING")
        if conn_str:
            try:
                # Create Resource with service metadata
                resource = Resource.create(
                    {
                        "service.name": "case-assistant-agent",
                        "service.version": "1.0.0",
                        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
                    }
                )

                # Set up TracerProvider with Resource
                tracer_provider = TracerProvider(resource=resource)
                trace.set_tracer_provider(tracer_provider)

                # Configure Azure Monitor with OpenTelemetry
                configure_azure_monitor(connection_string=conn_str, enable_live_metrics=True, resource=resource)

                # Enable MAF instrumentation for ChatAgent and tools
                enable_instrumentation(
                    enable_sensitive_data=os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() == "true"
                )

                logging.getLogger(__name__).info("✓ Application Insights OpenTelemetry configured successfully")

            except Exception as e:
                logging.getLogger(__name__).error(f"✗ Failed to configure Application Insights: {str(e)}")
                logging.getLogger(__name__).warning("Continuing without Application Insights telemetry")
        else:
            logging.getLogger(__name__).warning(
                "⚠ APPINSIGHTS_CONNECTION_STRING not set - "
                "Application Insights telemetry disabled (console logging only)"
            )

        cls._app_insights_configured = True

    def info(self, message: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log an info message.

        Args:
            message: Log message.
            extra: Optional structured data forwarded to Application Insights.
            **kwargs: Additional ``logging`` parameters (e.g. ``exc_info``).
        """
        self.logger.info(message, **({"extra": extra} if extra else {}), **kwargs)

    def warning(self, message: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log a warning message.

        Args:
            message: Log message.
            extra: Optional structured data forwarded to Application Insights.
            **kwargs: Additional ``logging`` parameters.
        """
        self.logger.warning(message, **({"extra": extra} if extra else {}), **kwargs)

    def error(
        self,
        message: str,
        extra: dict[str, Any] | None = None,
        exc_info: bool = True,
        **kwargs: Any,
    ) -> None:
        """Log an error message.

        Args:
            message: Log message.
            extra: Optional structured data forwarded to Application Insights.
            exc_info: Include current exception information (default: ``True``).
            **kwargs: Additional ``logging`` parameters.
        """
        self.logger.error(message, **({"extra": extra} if extra else {}), exc_info=exc_info, **kwargs)

    def exception(self, message: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log an exception with a full stack trace.

        Args:
            message: Log message.
            extra: Optional structured data forwarded to Application Insights.
            **kwargs: Additional ``logging`` parameters.
        """
        self.logger.exception(message, **({"extra": extra} if extra else {}), **kwargs)

    def debug(self, message: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log a debug message.

        Args:
            message: Log message.
            extra: Optional structured data forwarded to Application Insights.
            **kwargs: Additional ``logging`` parameters.
        """
        self.logger.debug(message, **({"extra": extra} if extra else {}), **kwargs)

    def critical(self, message: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log a critical message.

        Args:
            message: Log message.
            extra: Optional structured data forwarded to Application Insights.
            **kwargs: Additional ``logging`` parameters.
        """
        self.logger.critical(message, **({"extra": extra} if extra else {}), **kwargs)

    def add_span_attributes(self, **attributes: Any) -> None:
        """Add custom attributes to the current OpenTelemetry span.

        Enriches MAF-generated spans with business context such as user IDs,
        query characteristics, or document counts.

        Args:
            **attributes: Key-value pairs to set as span attributes.

        Example::

            logger.add_span_attributes(
                user_id="user123",
                query_length=45,
                document_count=5,
            )
        """
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            for key, value in attributes.items():
                current_span.set_attribute(key, value)

    @contextmanager
    def trace_operation(self, operation_name: str, **attributes: Any):
        """Context manager for tracing non-MAF operations.

        MAF automatically instruments ``agent.run()`` calls.  Use this for
        external service calls (Azure Search, Cosmos DB, etc.) that need
        explicit span tracing.

        Args:
            operation_name: Span name (e.g. ``"azure_search_query"``,
                ``"cosmos_upsert"``).
            **attributes: Additional span attributes set before the span starts.

        Yields:
            The active ``opentelemetry.trace.Span``.

        Example::

            with logger.trace_operation("azure_search", index="docs", query_type="vector"):
                results = await search_client.search(query)
        """
        with self.tracer.start_as_current_span(operation_name) as span:
            # Add custom attributes
            for key, value in attributes.items():
                span.set_attribute(key, value)

            start_time = time.time()
            try:
                yield span
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise
            finally:
                duration = time.time() - start_time
                span.set_attribute("duration_ms", duration * 1000)

    def log_operation_start(self, operation: str, **context: Any) -> None:
        """Log the start of an operation with structured context.

        Args:
            operation: Operation name.
            **context: Additional key-value context pairs.
        """
        extra: dict[str, Any] = {"operation": operation, "status": "started", **context}
        self.info(f"Starting operation: {operation}", extra=extra)

    def log_operation_complete(self, operation: str, **context: Any) -> None:
        """Log the successful completion of an operation.

        Args:
            operation: Operation name.
            **context: Additional key-value context pairs.
        """
        extra: dict[str, Any] = {"operation": operation, "status": "completed", **context}
        self.info(f"Completed operation: {operation}", extra=extra)

    def log_operation_failed(self, operation: str, error: Exception, **context: Any) -> None:
        """Log a failed operation with exception details.

        Args:
            operation: Operation name.
            error: The exception that caused the failure.
            **context: Additional key-value context pairs.
        """
        extra: dict[str, Any] = {
            "operation": operation,
            "status": "failed",
            "error_type": type(error).__name__,
            "error_message": str(error),
            **context,
        }
        self.error(f"Failed operation: {operation}", extra=extra, exc_info=True)


# Factory function for creating logger instances with custom names
def create_logger(name: str) -> Logger:
    """Create a ``Logger`` instance with a custom name.

    Args:
        name: Logger name (typically the module or service name).

    Returns:
        Configured ``Logger`` instance.
    """
    return Logger(name)
