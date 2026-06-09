import datetime as dt
import sqlite3
from collections.abc import Iterable, Iterator
from decimal import Decimal

from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    Charge,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.types import Month, Year


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with a Z suffix."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def _where_clause(columns: Iterable[str]) -> str:
    return " AND ".join(f"{column} = :{column}" for column in columns)


_ROLLING_MONTHS = 12


def _month_ordinal(year: Year, month: Month) -> int:
    """Map a (year, month) pair to a single comparable month ordinal."""
    return year * 12 + month  # noqa: WPS432


def _row_to_charge(row: sqlite3.Row, subscription_id: str) -> Charge:
    return Charge(
        subscription_id=subscription_id,
        agreement_id=row["agreement_id"],
        year=row["year"],
        month=row["month"],
        ppx1=row["ppx1"],
        spx1=row["spx1"],
    )


class _AccumulationEngine:
    """Generic additive-upsert engine over a single key tuple."""

    def __init__(self, connection: sqlite3.Connection, table: str) -> None:
        self.connection = connection
        self.table = table

    def accumulate(self, *, ppx1: Decimal, spx1: Decimal, **key_fields: object) -> None:
        """Additively upsert ppx1/spx1 for the keyed bucket."""
        self.connection.execute(
            self._upsert_sql(**key_fields),
            self._upsert_params(ppx1=ppx1, spx1=spx1, **key_fields),
        )

    def get(self, **key_fields: object) -> sqlite3.Row | None:
        """Return the stored row for the keyed bucket, or None when absent."""
        select_sql = f"SELECT * FROM {self.table} WHERE {_where_clause(key_fields)}"  # noqa: S608
        cursor = self.connection.execute(select_sql, key_fields)
        row: sqlite3.Row | None = cursor.fetchone()
        return row

    def sum_ppx1(
        self,
        key_column: str,
        key_value: str,
        start_ordinal: int,
        end_ordinal: int,
    ) -> Decimal:
        """Sum ppx1 over rows whose month-ordinal falls in [start_ordinal, end_ordinal]."""
        select_sql = (
            f"SELECT ppx1 FROM {self.table} "  # noqa: S608
            f"WHERE {key_column} = :key_value "
            "AND year * 12 + month BETWEEN :start AND :end"
        )
        bindings = {"key_value": key_value, "start": start_ordinal, "end": end_ordinal}
        rows = self.connection.execute(select_sql, bindings).fetchall()
        return sum((Decimal(row["ppx1"]) for row in rows), Decimal(0))

    def rows_updated_on(self, day: str) -> Iterator[sqlite3.Row]:
        """Yield rows whose updated_at calendar day equals the ISO day (YYYY-MM-DD), streamed."""
        select_sql = (
            f"SELECT * FROM {self.table} "  # noqa: S608
            "WHERE substr(updated_at, 1, 10) = :day"
        )
        yield from self.connection.execute(select_sql, {"day": day})

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
        connection: sqlite3.Connection,
        table: str = "subscription_monthly_accumulation",
    ) -> None:
        self.engine = _AccumulationEngine(connection=connection, table=table)

    def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the subscription bucket."""
        self.engine.accumulate(
            ppx1=charge.ppx1,
            spx1=charge.spx1,
            subscription_id=charge.subscription_id,
            agreement_id=charge.agreement_id,
            year=charge.year,
            month=charge.month,
        )

    def get(
        self,
        *,
        subscription_id: str,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        row = self.engine.get(
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

    def monthly_estimate(self, subscription_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 for the subscription's bucket in the single (year, month)."""
        ordinal = _month_ordinal(year, month)
        return self.engine.sum_ppx1("subscription_id", subscription_id, ordinal, ordinal)

    def yearly_estimate(self, subscription_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 across the subscription's trailing 12 months ending at (year, month)."""
        end = _month_ordinal(year, month)
        return self.engine.sum_ppx1(
            "subscription_id", subscription_id, end - _ROLLING_MONTHS + 1, end
        )

    def updated(self, updated_on: dt.date) -> Iterator[Charge]:
        """Yield the subscription buckets last written on updated_on, as Charges (streamed).

        Lazily streamed; consume while the database connection is open.
        """
        for row in self.engine.rows_updated_on(updated_on.isoformat()):
            yield _row_to_charge(row, row["subscription_id"])


class AgreementAccumulationRepository:
    """SQLite-backed agreement accumulation repository."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        table: str = "agreement_monthly_accumulation",
    ) -> None:
        self.engine = _AccumulationEngine(connection=connection, table=table)

    def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the agreement bucket."""
        self.engine.accumulate(
            ppx1=charge.ppx1,
            spx1=charge.spx1,
            agreement_id=charge.agreement_id,
            year=charge.year,
            month=charge.month,
        )

    def get(
        self,
        *,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> AgreementMonthlyAccumulation | None:
        """Return the stored agreement bucket, or None when absent."""
        row = self.engine.get(agreement_id=agreement_id, year=year, month=month)
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

    def monthly_estimate(self, agreement_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 for the agreement's bucket in the single (year, month)."""
        ordinal = _month_ordinal(year, month)
        return self.engine.sum_ppx1("agreement_id", agreement_id, ordinal, ordinal)

    def yearly_estimate(self, agreement_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 across the agreement's trailing 12 months ending at (year, month)."""
        end = _month_ordinal(year, month)
        return self.engine.sum_ppx1("agreement_id", agreement_id, end - _ROLLING_MONTHS + 1, end)

    def updated(self, updated_on: dt.date) -> Iterator[Charge]:
        """Yield the agreement buckets last written on updated_on, as Charges (streamed).

        Lazily streamed; consume while the database connection is open.
        """
        for row in self.engine.rows_updated_on(updated_on.isoformat()):
            yield _row_to_charge(row, "")
