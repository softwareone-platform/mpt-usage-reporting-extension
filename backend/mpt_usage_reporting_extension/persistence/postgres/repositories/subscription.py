import datetime as dt
from collections.abc import AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from mpt_usage_reporting_extension.persistence.models import (
    Charge,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.persistence.postgres.repositories.engine import (
    RETENTION_MONTHS,
    AccumulationEngine,
)
from mpt_usage_reporting_extension.types import Month, Year
from mpt_usage_reporting_extension.utils import month_ordinal  # noqa: WPS347


class SubscriptionAccumulationRepository:  # noqa: WPS214
    """PostgreSQL-backed subscription accumulation repository."""

    def __init__(
        self,
        connection: AsyncConnection[DictRow],
        table: str = "subscription_monthly_accumulation",
    ) -> None:
        self.engine = AccumulationEngine(connection=connection, table=table)

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the subscription bucket."""
        await self.engine.accumulate(
            ppx1=charge.ppx1,
            spx1=charge.spx1,
            subscription_id=charge.subscription_id,
            agreement_id=charge.agreement_id,
            year=charge.year,
            month=charge.month,
        )

    async def get(
        self, subscription_id: str, year: Year, month: Month
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        row = await self.engine.get(subscription_id=subscription_id, year=year, month=month)
        return None if row is None else self._to_bucket(row)

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        cutoff = month_ordinal(year, month) - RETENTION_MONTHS + 1
        return await self.engine.delete_before(cutoff)

    async def delete(
        self, *, subscription_id: str | None = None, agreement_id: str | None = None
    ) -> int:
        """Delete subscription buckets for the given scope (no scope deletes every bucket)."""
        equals: dict[str, object] = {}
        if subscription_id is not None:
            equals["subscription_id"] = subscription_id
        if agreement_id is not None:
            equals["agreement_id"] = agreement_id
        return await self.engine.delete(**equals)

    async def updated(self, updated_on: dt.date) -> AsyncIterator[SubscriptionMonthlyAccumulation]:
        """Yield the subscription buckets last written on updated_on (streamed).

        Lazily streamed; consume while the database connection is open.
        """
        async for row in self.engine.rows_updated_on(updated_on.isoformat()):
            yield self._to_bucket(row)

    async def subscriptions_by_agreement(
        self, agreement_id: str | None = None
    ) -> AsyncIterator[str]:
        """Yield each distinct subscription id currently stored, optionally for one agreement."""
        equals = {} if agreement_id is None else {"agreement_id": agreement_id}
        async for subscription_id in self.engine.distinct("subscription_id", **equals):
            yield str(subscription_id)

    async def agreements_by_subscription(self, subscription_id: str) -> AsyncIterator[str]:
        """Yield each distinct agreement id stored for one subscription."""
        async for agreement_id in self.engine.distinct(
            "agreement_id", subscription_id=subscription_id
        ):
            yield str(agreement_id)

    def _to_bucket(self, row: DictRow) -> SubscriptionMonthlyAccumulation:
        return SubscriptionMonthlyAccumulation(
            subscription_id=row["subscription_id"],
            agreement_id=row["agreement_id"],
            year=row["year"],
            month=row["month"],
            ppx1=row["ppx1"],
            spx1=row["spx1"],
            updated_at=row["updated_at"],
        )
