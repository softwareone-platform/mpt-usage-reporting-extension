import logging

from mpt_usage_reporting_extension.persistence.models import ExecutionRecord
from mpt_usage_reporting_extension.utils import sanitize_log_value

logger = logging.getLogger(__name__)


class StatusReport:
    """Log recent command executions, one line per execution."""

    def __init__(self, executions: list[ExecutionRecord]) -> None:
        self._executions = executions

    def render(self) -> None:
        """Log the recent executions, or a notice when there are none."""
        if not self._executions:
            logger.info("No command executions recorded yet.")
            return
        for execution in self._executions:
            logger.info(
                "command=%s started=%s completed=%s status=%s",
                sanitize_log_value(execution.command),
                sanitize_log_value(execution.started_at),
                sanitize_log_value(execution.completed_at or ""),
                sanitize_log_value(execution.status),
            )
