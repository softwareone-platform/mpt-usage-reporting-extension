import datetime as dt
import sqlite3
from collections.abc import Iterable
from decimal import Decimal

from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    Charge,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.types import Month, Year


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with a Z suffix."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def _where_clause(columns: Iterable[str]) -> str:
    return " AND ".join(f"{column} = :{column}" for column in columns)


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


class _SubscriptionAccumulationRepository:
    """SQLite-backed subscription accumulation repository."""

    def __init__(self, engine: _AccumulationEngine) -> None:
        self.engine = engine

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


class _AgreementAccumulationRepository:
    """SQLite-backed agreement accumulation repository."""

    def __init__(self, engine: _AccumulationEngine) -> None:
        self.engine = engine

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


def subscription_repository(
    connection: sqlite3.Connection,
) -> SubscriptionAccumulationRepository:
    """Build the SQLite subscription monthly accumulation repository."""
    return _SubscriptionAccumulationRepository(
        engine=_AccumulationEngine(
            connection=connection,
            table="subscription_monthly_accumulation",
        ),
    )


def agreement_repository(
    connection: sqlite3.Connection,
) -> AgreementAccumulationRepository:
    """Build the SQLite agreement monthly accumulation repository."""
    return _AgreementAccumulationRepository(
        engine=_AccumulationEngine(
            connection=connection,
            table="agreement_monthly_accumulation",
        ),
    )
