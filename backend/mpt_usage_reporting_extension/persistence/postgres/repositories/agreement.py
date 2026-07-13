from psycopg import AsyncConnection
from psycopg.rows import DictRow

from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.postgres.repositories.engine import (
    RETENTION_MONTHS,
    AccumulationEngine,
)
from mpt_usage_reporting_extension.types import Month, Year
from mpt_usage_reporting_extension.utils import month_ordinal  # noqa: WPS347


class AgreementAccumulationRepository:
    """PostgreSQL-backed agreement accumulation repository."""

    def __init__(
        self,
        connection: AsyncConnection[DictRow],
        table: str = "agreement_monthly_accumulation",
    ) -> None:
        self.engine = AccumulationEngine(connection=connection, table=table)

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the agreement bucket."""
        await self.engine.accumulate(
            ppx1=charge.ppx1,
            spx1=charge.spx1,
            agreement_id=charge.agreement_id,
            year=charge.year,
            month=charge.month,
        )

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        cutoff = month_ordinal(year, month) - RETENTION_MONTHS + 1
        return await self.engine.delete_before(cutoff)

    async def delete(self, *, agreement_id: str | None = None) -> int:
        """Delete agreement buckets for the given scope (no scope deletes every bucket)."""
        equals = {} if agreement_id is None else {"agreement_id": agreement_id}
        return await self.engine.delete(**equals)
