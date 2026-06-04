"""Billing statement selection for the usage reporting ``run`` command."""

import datetime as dt
import logging
from collections.abc import Iterable
from typing import Any

import typer
from mpt_api_client import RQLQuery
from mpt_api_client.resources.billing.statements import Statement
from rich.console import Console
from rich.table import Table

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.window import RunWindow

logger = logging.getLogger(__name__)

_RQL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_ID = "id"
_STATUS = "status"
_PRODUCT_ID = "product.id"
_AGREEMENT_ID = "agreement.id"
_AUDIT_ISSUED_AT = "audit.issued.at"
_AUDIT_CANCELLED_AT = "audit.cancelled.at"
_AUDIT_CREATED_AT = "audit.created.at"

_PASSES = (
    (_AUDIT_ISSUED_AT, "Issued"),
    (_AUDIT_CANCELLED_AT, "Cancelled"),
)

_REPORT_HEADERS = (
    "Statement ID",
    "Status",
    "Created At",
    "Cancelled At",
    "Agreement ID",
    "Product ID",
    "PP",
)
_REPORT_PATHS = (
    _ID,
    _STATUS,
    _AUDIT_CREATED_AT,
    _AUDIT_CANCELLED_AT,
    _AGREEMENT_ID,
    _PRODUCT_ID,
    "price.total_pp",
)

_SELECT_FIELDS = (
    _ID,
    _STATUS,
    _AUDIT_CREATED_AT,
    _AUDIT_CANCELLED_AT,
    _AGREEMENT_ID,
    _PRODUCT_ID,
    "price.totalPP",
)


class StatementSelector:
    """Select automated billing statements for a run window.

    Runs two RQL passes (``audit.issued.at`` and ``audit.cancelled.at``), merges the
    results by ``statement.id`` so a statement matched by both passes appears once, and
    stores them on the :class:`RunContext`.
    """

    def __init__(self, filter_builder: "StatementFilterBuilder | None" = None) -> None:
        self._filter_builder = filter_builder or StatementFilterBuilder()

    def select(self, ctx: RunContext) -> None:
        """Select statements issued or cancelled within the window and save them on ``ctx``."""
        merged: dict[str, Statement] = {}
        for audit_field, status in _PASSES:
            query = self._filter_builder.build(
                ctx.product_ids,
                ctx.seller_id,
                ctx.window,
                audit_field,
                status,
            )
            merged.update({
                statement.id: statement
                for statement in ctx.api_client.billing.statements
                .filter(query)
                .select(*_SELECT_FIELDS)
                .iterate()
            })
        ctx.statements = list(merged.values())


class StatementFilterBuilder:
    """Build RQL statement filters used by the selector pass loop."""

    def build(
        self,
        product_ids: Iterable[str],
        seller_id: str,
        window: RunWindow,
        audit_field: str,
        status: str,
    ) -> RQLQuery:
        """Build the full statement filter for a single audit/status pass."""
        query = (
            self._base_statement_filter(product_ids, seller_id)
            & self._status_filter(status)
            & self._field_window(
                audit_field,
                window,
            )
        )
        logger.info("Selecting %s statements by %s with RQL: %s", status, audit_field, query)
        return query

    def _base_statement_filter(self, product_ids: Iterable[str], seller_id: str) -> RQLQuery:
        """Build the shared RQL filter for automated statements of the given products."""
        automated = RQLQuery().n("billingType").eq("Automated")
        products = RQLQuery().n(_PRODUCT_ID).in_(list(product_ids))
        rql = automated & products
        if seller_id:
            rql &= RQLQuery().n("seller.id").eq(seller_id)
        return rql

    def _status_filter(self, status: str) -> RQLQuery:
        """Build the per-pass statement status filter."""
        return RQLQuery().n(_STATUS).eq(status)

    def _field_window(self, audit_field: str, window: RunWindow) -> RQLQuery:
        """Build the half-open ``[start, end)`` RQL filter for an audit datetime field.

        Uses ``from_string`` so the datetimes render unquoted: a quoted datetime is treated
        as a string by the API and matches nothing.
        """
        start = self._rql_datetime(window.start)
        end = self._rql_datetime(window.end)
        after_start = RQLQuery.from_string(f"ge({audit_field},{start})")
        before_end = RQLQuery.from_string(f"lt({audit_field},{end})")
        return after_start & before_end

    def _rql_datetime(self, moment: dt.datetime) -> str:
        """Render a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ`` for RQL comparisons."""
        return moment.astimezone(dt.UTC).strftime(_RQL_DATETIME_FORMAT)


class StatementReport:
    """Render the statements selected by a run as a console table."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def render(self) -> None:
        """Print the run summary line and, when statements were selected, the table."""
        ctx = self._ctx
        start = ctx.window.start.strftime("%Y-%m-%d")
        end = ctx.window.end.strftime("%Y-%m-%d")
        count = len(ctx.statements)
        typer.echo(f"Selected {count} statement(s) for {start}..{end}")
        if ctx.statements:
            Console().print(self._table())

    def _table(self) -> Table:
        table = Table(*_REPORT_HEADERS)
        for statement in self._ctx.statements:
            table.add_row(*(self._field(statement, path) for path in _REPORT_PATHS))
        return table

    def _field(self, statement: Statement, path: str) -> str:
        current: Any = statement
        for attr in path.split("."):
            current = getattr(current, attr, None)
            if current is None:
                return "-"
        return str(current)
