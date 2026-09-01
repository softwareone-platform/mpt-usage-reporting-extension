import enum
from calendar import Month as CalendarMonth

Year = int
Month = CalendarMonth

MIN_STORABLE_YEAR = 1000
MAX_STORABLE_YEAR = 9999


def is_storable_year(year: Year) -> bool:
    """Return whether the year fits the accumulation tables' ``year`` CHECK constraint."""
    return MIN_STORABLE_YEAR <= year <= MAX_STORABLE_YEAR


class Command(enum.StrEnum):
    """The CLI commands whose executions are tracked."""

    RUN = "run"
    RECALCULATE = "recalculate"
    CLEANUP = "cleanup"
    DELETE = "delete"


class ExecutionStatus(enum.StrEnum):
    """Lifecycle status of a tracked command execution."""

    RUNNING = "running"
    SUCCESS = "success"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class StatementStatus(enum.StrEnum):
    """Outcome of processing a single statement during a run."""

    PROCESSING = "processing"
    SUCCESS = "success"
    FAILURE = "failure"
