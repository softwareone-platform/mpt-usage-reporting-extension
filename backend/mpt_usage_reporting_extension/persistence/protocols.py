import datetime as dt
from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from mpt_usage_reporting_extension.persistence.models import (
    Charge,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.types import (
    Command,
    ExecutionStatus,
    Month,
    StatementStatus,
    Year,
)


class SubscriptionAccumulationRepository(Protocol):  # noqa: WPS214
    """Read and write monthly accumulation totals per subscription bucket."""

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the subscription bucket."""
        ...

    async def get(
        self, subscription_id: str, year: Year, month: Month
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        ...

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        ...

    async def delete(
        self, *, subscription_id: str | None = None, agreement_id: str | None = None
    ) -> int:
        """Delete subscription buckets for the given scope (no scope deletes every bucket)."""
        ...

    def updated(self, updated_on: dt.date) -> AsyncIterator[SubscriptionMonthlyAccumulation]:
        """Yield the subscription buckets last written on updated_on (streamed)."""
        ...

    def subscriptions_by_agreement(self, agreement_id: str | None = None) -> AsyncIterator[str]:
        """Yield each distinct subscription id currently stored, optionally for one agreement."""
        ...


class AgreementAccumulationRepository(Protocol):
    """Persist monthly accumulation totals per agreement bucket."""

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the agreement bucket."""
        ...

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        ...

    async def delete(self, *, agreement_id: str | None = None) -> int:
        """Delete agreement buckets for the given scope (no scope deletes every bucket)."""
        ...


class ExecutionRepository(Protocol):
    """Persist command-execution insight rows."""

    async def start(self, command: Command, parameters: Mapping[str, object]) -> int:
        """Insert a running execution row and return its id."""
        ...

    async def finish(
        self, execution_id: int, status: ExecutionStatus, result: Mapping[str, object]
    ) -> None:
        """Stamp completed_at, final status, and the JSON result on the execution row."""
        ...


class StatementProcessingRepository(Protocol):
    """Persist per-statement processing insight rows."""

    async def start(self, execution_id: int, statement_id: str) -> int:
        """Insert a processing row and return its id."""
        ...

    async def finish(
        self, processing_id: int, status: StatementStatus, failure_message: str | None = None
    ) -> None:
        """Stamp ended_at, final status, and optional failure message."""
        ...
