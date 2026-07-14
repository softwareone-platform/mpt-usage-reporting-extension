import datetime as dt
from collections.abc import AsyncIterator, Iterable
from decimal import Decimal

from psycopg import AsyncConnection, AsyncServerCursor, sql
from psycopg.rows import DictRow

RETENTION_MONTHS = 18  # cleanup keeps this many trailing months (buffer for delayed billing)


def utc_now() -> dt.datetime:
    """Return the current UTC time as an aware datetime without microseconds."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _where_clause(columns: Iterable[str]) -> sql.Composed:
    return sql.SQL(" AND ").join(
        sql.SQL("{column} = {placeholder}").format(
            column=sql.Identifier(column), placeholder=sql.Placeholder(column)
        )
        for column in columns
    )


class AccumulationEngine:  # noqa: WPS214  # one method per persistence operation on one table
    """Additive-upsert engine accumulating the ppx1/spx1 columns over a single key tuple."""

    def __init__(self, connection: AsyncConnection[DictRow], table: str) -> None:
        self.connection = connection
        self.table = table

    async def accumulate(self, *, ppx1: Decimal, spx1: Decimal, **key_fields: object) -> None:
        """Additively upsert ppx1/spx1 for the keyed bucket."""
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                self._upsert_sql(**key_fields),
                self._upsert_params(ppx1=ppx1, spx1=spx1, **key_fields),
            )

    async def get(self, **key_fields: object) -> DictRow | None:
        """Return the stored row for the keyed bucket, or None when absent."""
        select_sql = sql.SQL("SELECT * FROM {table} WHERE {where}").format(
            table=sql.Identifier(self.table), where=_where_clause(key_fields)
        )
        async with self.connection.cursor() as cursor:
            await cursor.execute(select_sql, key_fields)
            return await cursor.fetchone()

    async def delete_before(self, cutoff_ordinal: int) -> int:
        """Delete rows older than the cutoff month ordinal; return the deleted row count."""
        delete_sql = sql.SQL("DELETE FROM {table} WHERE year * 12 + month < %(cutoff)s").format(
            table=sql.Identifier(self.table)
        )
        async with self.connection.cursor() as cursor:
            await cursor.execute(delete_sql, {"cutoff": cutoff_ordinal})
            deleted: int = cursor.rowcount
        return deleted

    async def delete(self, **equals: object) -> int:
        """Delete rows matching the equals filter; with no filter, delete every row."""
        delete_sql = sql.SQL("DELETE FROM {table}").format(table=sql.Identifier(self.table))
        if equals:
            delete_sql += sql.SQL(" WHERE ") + _where_clause(equals)
        async with self.connection.cursor() as cursor:
            await cursor.execute(delete_sql, equals)
            deleted: int = cursor.rowcount
        return deleted

    async def rows_updated_on(self, day: str) -> AsyncIterator[DictRow]:
        """Yield rows whose updated_at UTC calendar day equals the ISO day, streamed."""
        select_sql = sql.SQL(
            "SELECT * FROM {table} "
            "WHERE updated_at >= %(day)s::timestamp AT TIME ZONE 'UTC' "
            "AND updated_at < (%(day)s::timestamp AT TIME ZONE 'UTC') + interval '1 day'"
        ).format(table=sql.Identifier(self.table))
        async with self._stream_cursor("rows_updated_on") as cursor:
            await cursor.execute(select_sql, {"day": day})
            async for row in cursor:
                yield row

    async def distinct(self, column: str, **equals: object) -> AsyncIterator[object]:
        """Yield each distinct value of column, optionally filtered by equals."""
        select_sql = self._distinct_sql(column, equals)
        async with self._stream_cursor("distinct") as cursor:
            await cursor.execute(select_sql, equals)
            async for row in cursor:
                yield row[column]

    def _stream_cursor(self, operation: str) -> AsyncServerCursor[DictRow]:
        """Return a server-side cursor so iteration fetches rows incrementally.

        ``withhold`` keeps the cursor usable on the autocommit connection, and the
        name is unique per table and operation so streams over different tables
        can be open concurrently.
        """
        return self.connection.cursor(f"{self.table}_{operation}", withhold=True)

    def _upsert_params(
        self,
        *,
        ppx1: Decimal,
        spx1: Decimal,
        **key_fields: object,
    ) -> dict[str, object]:
        return {**key_fields, "ppx1": ppx1, "spx1": spx1, "updated_at": utc_now()}

    def _upsert_sql(self, **key_fields: object) -> sql.Composed:
        columns = (*key_fields, "ppx1", "spx1", "updated_at")
        return sql.SQL(
            "INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
            "ON CONFLICT ({conflict}) DO UPDATE SET "
            "ppx1 = {table}.ppx1 + EXCLUDED.ppx1, "
            "spx1 = {table}.spx1 + EXCLUDED.spx1, "
            "updated_at = EXCLUDED.updated_at"
        ).format(
            table=sql.Identifier(self.table),
            columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            placeholders=sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
            conflict=sql.SQL(", ").join(sql.Identifier(column) for column in key_fields),
        )

    def _distinct_sql(self, column: str, equals: dict[str, object]) -> sql.Composed:
        select_sql = sql.SQL("SELECT DISTINCT {column} FROM {table}").format(
            column=sql.Identifier(column), table=sql.Identifier(self.table)
        )
        if equals:
            select_sql += sql.SQL(" WHERE ") + _where_clause(equals)
        return select_sql + sql.SQL(" ORDER BY {column}").format(column=sql.Identifier(column))
