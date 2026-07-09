import datetime as dt
import sqlite3
from collections.abc import AsyncIterator, Iterable
from decimal import Decimal

import aiosqlite

from mpt_usage_reporting_extension.persistence.models import (
    Charge,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.persistence.sqlite.retry import retry_on_busy
from mpt_usage_reporting_extension.types import Month, Year
from mpt_usage_reporting_extension.utils import month_ordinal  # noqa: WPS347


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with a Z suffix."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def _where_clause(columns: Iterable[str]) -> str:
    return " AND ".join(f"{column} = :{column}" for column in columns)


_RETENTION_MONTHS = 18  # cleanup keeps this many trailing months (buffer for delayed billing)


class _AccumulationEngine:  # noqa: WPS214
    """Generic additive-upsert engine over a single key tuple."""

    def __init__(self, connection: aiosqlite.Connection, table: str) -> None:
        self.connection = connection
        self.table = table

    @retry_on_busy
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

    @retry_on_busy
    async def delete_before(self, cutoff_ordinal: int) -> int:
        """Delete rows older than the cutoff month ordinal; return the deleted row count."""
        delete_sql = f"DELETE FROM {self.table} WHERE year * 12 + month < :cutoff"  # noqa: S608
        cursor = await self.connection.execute(delete_sql, {"cutoff": cutoff_ordinal})
        deleted: int = cursor.rowcount
        await cursor.close()
        return deleted

    @retry_on_busy
    async def delete(self, **equals: object) -> int:
        """Delete rows matching the equals filter; with no filter, delete every row."""
        suffix = f" WHERE {_where_clause(equals)}" if equals else ""
        delete_sql = f"DELETE FROM {self.table}{suffix}"  # noqa: S608
        cursor = await self.connection.execute(delete_sql, equals)
        deleted: int = cursor.rowcount
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

    async def distinct(self, column: str, **equals: object) -> AsyncIterator[object]:
        """Yield each distinct value of column, optionally filtered by equals."""
        clauses = [f"{name} = :{name}" for name in equals]
        select_sql = self._distinct_sql(column, clauses)
        async with self.connection.execute(select_sql, equals) as cursor:
            async for row in cursor:
                yield row[column]

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

    def _distinct_sql(self, column: str, clauses: list[str]) -> str:
        where = " AND ".join(clauses)
        suffix = f" WHERE {where}" if where else ""
        return f"SELECT DISTINCT {column} FROM {self.table}{suffix} ORDER BY {column}"  # noqa: S608


class SubscriptionAccumulationRepository:  # noqa: WPS214
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
        self, subscription_id: str, year: Year, month: Month
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        row = await self.engine.get(subscription_id=subscription_id, year=year, month=month)
        return None if row is None else self._to_bucket(row)

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        cutoff = month_ordinal(year, month) - _RETENTION_MONTHS + 1
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

    def _to_bucket(self, row: sqlite3.Row) -> SubscriptionMonthlyAccumulation:
        return SubscriptionMonthlyAccumulation(
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

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        cutoff = month_ordinal(year, month) - _RETENTION_MONTHS + 1
        return await self.engine.delete_before(cutoff)

    async def delete(self, *, agreement_id: str | None = None) -> int:
        """Delete agreement buckets for the given scope (no scope deletes every bucket)."""
        equals = {} if agreement_id is None else {"agreement_id": agreement_id}
        return await self.engine.delete(**equals)
