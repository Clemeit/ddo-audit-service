from models.service import LogRequest
import services.postgres as postgres_client
from datetime import datetime, timezone


def logMessage(message: str, level: str = "info", **kwargs):
    """
    Log a message with the specified level and additional context.

    :param message: The core log message.
    :param level: The log level (e.g., "debug", "info", "warn", "error", "fatal").
    :param kwargs: Additional context to include in the log.
    """
    try:
        log_request = LogRequest(
            message=message,
            level=level,
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_internal=True,
            **kwargs,
        )
        postgres_client.persist_log(log_request)
    except Exception as e:
        print(f"Failed to create log request: {e}")
        return
