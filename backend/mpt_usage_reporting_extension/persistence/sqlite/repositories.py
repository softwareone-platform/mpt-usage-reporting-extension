import datetime as dt
import sqlite3
from collections.abc import AsyncIterator, Iterable
from decimal import Decimal

import aiosqlite

from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    Charge,
    PriceEstimate,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.types import Month, Year


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with a Z suffix."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def _where_clause(columns: Iterable[str]) -> str:
    return " AND ".join(f"{column} = :{column}" for column in columns)


_ROLLING_MONTHS = 12  # estimate window (trailing yearly PPxY/SPxY) — not the retention window
_RETENTION_MONTHS = 18  # cleanup keeps this many trailing months (buffer for delayed billing)


def _month_ordinal(year: Year, month: Month) -> int:
    """Map a (year, month) pair to a single comparable month ordinal."""
    return year * 12 + month  # noqa: WPS432


class _AccumulationEngine:  # noqa: WPS214
    """Generic additive-upsert engine over a single key tuple."""

    def __init__(self, connection: aiosqlite.Connection, table: str) -> None:
        self.connection = connection
        self.table = table

    async def accumulate(self, *, ppx1: Decimal, spx1: Decimal, **key_fields: object) -> None:
        """Additively upsert ppx1/spx1 for the keyed bucket."""
        cursor = await self.connection.execute(
            self._upsert_sql(**key_fields),
            self._upsert_params(ppx1=ppx1, spx1=spx1, **key_fields),
        )
        await cursor.close()

    async def get(self, **key_fields: object) -> sqlite3.Row | None:
        """Return the stored row for the keyed bucket, or None when absent."""
        select_sql = f"SELECT * FROM {self.table} WHERE {_where_clause(key_fields)}"  # noqa: S608
        async with self.connection.execute(select_sql, key_fields) as cursor:
            row: sqlite3.Row | None = await cursor.fetchone()
        return row

    async def estimate(self, key_column: str, key_value: str, anchor_ordinal: int) -> PriceEstimate:
        """Sum the anchor month (PPxM/SPxM) and the trailing window (PPxY/SPxY) in one query."""
        select_sql = (
            f"SELECT ppx1, spx1, year * 12 + month AS ordinal FROM {self.table} "  # noqa: S608
            f"WHERE {key_column} = :key_value "
            "AND year * 12 + month BETWEEN :start AND :anchor"
        )
        bindings = {
            "key_value": key_value,
            "start": anchor_ordinal - _ROLLING_MONTHS + 1,
            "anchor": anchor_ordinal,
        }
        async with self.connection.execute(select_sql, bindings) as cursor:
            rows = await cursor.fetchall()
        monthly = [row for row in rows if row["ordinal"] == anchor_ordinal]
        return PriceEstimate(
            ppxm=sum((Decimal(row["ppx1"]) for row in monthly), Decimal(0)),
            spxm=sum((Decimal(row["spx1"]) for row in monthly), Decimal(0)),
            ppxy=sum((Decimal(row["ppx1"]) for row in rows), Decimal(0)),
            spxy=sum((Decimal(row["spx1"]) for row in rows), Decimal(0)),
        )

    async def delete_before(self, cutoff_ordinal: int) -> int:
        """Delete rows older than the cutoff month ordinal; return the deleted row count."""
        delete_sql = f"DELETE FROM {self.table} WHERE year * 12 + month < :cutoff"  # noqa: S608
        cursor = await self.connection.execute(delete_sql, {"cutoff": cutoff_ordinal})
        deleted = cursor.rowcount
        await cursor.close()
        return deleted

    async def rows_updated_on(self, day: str) -> AsyncIterator[sqlite3.Row]:
        """Yield rows whose updated_at calendar day equals the ISO day (YYYY-MM-DD), streamed."""
        select_sql = (
            f"SELECT * FROM {self.table} "  # noqa: S608
            "WHERE substr(updated_at, 1, 10) = :day"
        )
        async with self.connection.execute(select_sql, {"day": day}) as cursor:
            async for row in cursor:
                yield row

    def _upsert_params(
        self,
        *,
        ppx1: Decimal,
        spx1: Decimal,
        **key_fields: object,
    ) -> dict[str, object]:
        return {**key_fields, "ppx1": ppx1, "spx1": spx1, "updated_at": utc_now_iso()}

    def _upsert_sql(self, **key_fields: object) -> str:
        columns = (*key_fields, "ppx1", "spx1", "updated_at")
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{column}" for column in columns)
        conflict = ", ".join(key_fields)
        return (
            f"INSERT INTO {self.table} ({column_list}) "  # noqa: S608
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET "
            "ppx1 = decimal_add(ppx1, excluded.ppx1), "
            "spx1 = decimal_add(spx1, excluded.spx1), "
            "updated_at = excluded.updated_at"
        )


class SubscriptionAccumulationRepository:
    """SQLite-backed subscription accumulation repository."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        table: str = "subscription_monthly_accumulation",
    ) -> None:
        self.engine = _AccumulationEngine(connection=connection, table=table)

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
        self,
        *,
        subscription_id: str,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        row = await self.engine.get(
            subscription_id=subscription_id,
            agreement_id=agreement_id,
            year=year,
            month=month,
        )
        if row is None:
            return None
        return SubscriptionMonthlyAccumulation(
            subscription_id=row["subscription_id"],
            agreement_id=row["agreement_id"],
            year=row["year"],
            month=row["month"],
            ppx1=row["ppx1"],
            spx1=row["spx1"],
            updated_at=dt.datetime.fromisoformat(row["updated_at"]),
        )

    async def estimate(self, subscription_id: str, year: Year, month: Month) -> PriceEstimate:
        """Current-month (PPxM/SPxM) and trailing-12-month (PPxY/SPxY) sums for the subscription."""
        anchor = _month_ordinal(year, month)
        return await self.engine.estimate("subscription_id", subscription_id, anchor)

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        cutoff = _month_ordinal(year, month) - _RETENTION_MONTHS + 1
        return await self.engine.delete_before(cutoff)

    async def updated(self, updated_on: dt.date) -> AsyncIterator[SubscriptionMonthlyAccumulation]:
        """Yield the subscription buckets last written on updated_on (streamed).

        Lazily streamed; consume while the database connection is open.
        """
        async for row in self.engine.rows_updated_on(updated_on.isoformat()):
            yield SubscriptionMonthlyAccumulation(
                subscription_id=row["subscription_id"],
                agreement_id=row["agreement_id"],
                year=row["year"],
                month=row["month"],
                ppx1=row["ppx1"],
                spx1=row["spx1"],
                updated_at=dt.datetime.fromisoformat(row["updated_at"]),
            )


class AgreementAccumulationRepository:
    """SQLite-backed agreement accumulation repository."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        table: str = "agreement_monthly_accumulation",
    ) -> None:
        self.engine = _AccumulationEngine(connection=connection, table=table)

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the agreement bucket."""
        await self.engine.accumulate(
            ppx1=charge.ppx1,
            spx1=charge.spx1,
            agreement_id=charge.agreement_id,
            year=charge.year,
            month=charge.month,
        )

    async def get(
        self,
        *,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> AgreementMonthlyAccumulation | None:
        """Return the stored agreement bucket, or None when absent."""
        row = await self.engine.get(agreement_id=agreement_id, year=year, month=month)
        if row is None:
            return None
        return AgreementMonthlyAccumulation(
            agreement_id=row["agreement_id"],
            year=row["year"],
            month=row["month"],
            ppx1=row["ppx1"],
            spx1=row["spx1"],
            updated_at=dt.datetime.fromisoformat(row["updated_at"]),
        )

    async def estimate(self, agreement_id: str, year: Year, month: Month) -> PriceEstimate:
        """Current-month (PPxM/SPxM) and trailing-12-month (PPxY/SPxY) sums for the agreement."""
        anchor = _month_ordinal(year, month)
        return await self.engine.estimate("agreement_id", agreement_id, anchor)

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        cutoff = _month_ordinal(year, month) - _RETENTION_MONTHS + 1
        return await self.engine.delete_before(cutoff)

    async def updated(self, updated_on: dt.date) -> AsyncIterator[AgreementMonthlyAccumulation]:
        """Yield the agreement buckets last written on updated_on (streamed).

        Lazily streamed; consume while the database connection is open.
        """
        async for row in self.engine.rows_updated_on(updated_on.isoformat()):
            yield AgreementMonthlyAccumulation(
                agreement_id=row["agreement_id"],
                year=row["year"],
                month=row["month"],
                ppx1=row["ppx1"],
                spx1=row["spx1"],
                updated_at=dt.datetime.fromisoformat(row["updated_at"]),
            )
