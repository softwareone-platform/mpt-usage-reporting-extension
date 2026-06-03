"""Repositories implementing additive-upsert accumulation."""

import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from mpt_usage_reporting_extension.persistence.database import utc_now_iso
from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    SubscriptionMonthlyAccumulation,
)

_ZERO = Decimal(0)


def _where_clause(key_columns: tuple[str, ...]) -> str:
    return " AND ".join(f"{column} = ?" for column in key_columns)


@dataclass(frozen=True, slots=True)
class _AccumulationRepository[ModelT]:
    """Generic additive-upsert repository over a single key tuple."""

    connection: sqlite3.Connection
    table: str
    key_columns: tuple[str, ...]
    to_model: Callable[[sqlite3.Row], ModelT]

    def accumulate(self, key: Sequence[object], ppx1: Decimal, spx1: Decimal) -> None:
        """Additively upsert ppx1/spx1 for the given key inside one transaction."""
        cursor = self.connection.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            self._apply(cursor, key, ppx1, spx1)
        except sqlite3.Error:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def get(self, key: Sequence[object]) -> ModelT | None:
        """Return the stored row for the key as a model, or None when absent."""
        cursor = self.connection.execute(self._select_sql(), tuple(key))
        row = cursor.fetchone()
        if row is None:
            return None
        return self.to_model(row)

    def _apply(
        self,
        cursor: sqlite3.Cursor,
        key: Sequence[object],
        ppx1: Decimal,
        spx1: Decimal,
    ) -> None:
        current = self._current_totals(cursor, key)
        cursor.execute(self._upsert_sql(), self._upsert_params(key, current, ppx1, spx1))

    def _current_totals(
        self,
        cursor: sqlite3.Cursor,
        key: Sequence[object],
    ) -> tuple[Decimal, Decimal]:
        cursor.execute(
            f"SELECT ppx1, spx1 FROM {self.table} WHERE {_where_clause(self.key_columns)}",  # noqa: S608
            tuple(key),
        )
        row = cursor.fetchone()
        if row is None:
            return _ZERO, _ZERO
        return row["ppx1"], row["spx1"]

    def _upsert_params(
        self,
        key: Sequence[object],
        current: tuple[Decimal, Decimal],
        ppx1: Decimal,
        spx1: Decimal,
    ) -> dict[str, object]:
        bindings: dict[str, object] = {
            "ppx1": current[0] + ppx1,
            "spx1": current[1] + spx1,
            "updated_at": utc_now_iso(),
        }
        for index, column_value in enumerate(key):
            bindings[f"k{index}"] = column_value
        return bindings

    def _select_sql(self) -> str:
        return f"SELECT * FROM {self.table} WHERE {_where_clause(self.key_columns)}"  # noqa: S608

    def _upsert_sql(self) -> str:
        indices = range(len(self.key_columns))
        columns = ", ".join((*self.key_columns, "ppx1", "spx1", "updated_at"))
        key_placeholders = ", ".join(f":k{index}" for index in indices)
        conflict = ", ".join(self.key_columns)
        return (
            f"INSERT INTO {self.table} ({columns}) "  # noqa: S608
            f"VALUES ({key_placeholders}, :ppx1, :spx1, :updated_at) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET "
            "ppx1 = :ppx1, spx1 = :spx1, updated_at = :updated_at"
        )


def _to_subscription(row: sqlite3.Row) -> SubscriptionMonthlyAccumulation:
    return SubscriptionMonthlyAccumulation(
        subscription_id=row["subscription_id"],
        agreement_id=row["agreement_id"],
        year=row["year"],
        month=row["month"],
        ppx1=row["ppx1"],
        spx1=row["spx1"],
        updated_at=dt.datetime.fromisoformat(row["updated_at"]),
    )


def _to_agreement(row: sqlite3.Row) -> AgreementMonthlyAccumulation:
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
) -> _AccumulationRepository[SubscriptionMonthlyAccumulation]:
    """Build the subscription monthly accumulation repository."""
    return _AccumulationRepository(
        connection=connection,
        table="subscription_monthly_accumulation",
        key_columns=("subscription_id", "agreement_id", "year", "month"),
        to_model=_to_subscription,
    )


def agreement_repository(
    connection: sqlite3.Connection,
) -> _AccumulationRepository[AgreementMonthlyAccumulation]:
    """Build the agreement monthly accumulation repository."""
    return _AccumulationRepository(
        connection=connection,
        table="agreement_monthly_accumulation",
        key_columns=("agreement_id", "year", "month"),
        to_model=_to_agreement,
    )
