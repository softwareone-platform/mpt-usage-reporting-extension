import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import typer
from mpt_api_client import RQLQuery
from mpt_api_client.resources.billing.statements import Statement
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService
from rich.console import Console
from rich.table import Table

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
    _AUDIT_ISSUED_AT,
    _AUDIT_CANCELLED_AT,
    _AGREEMENT_ID,
    _PRODUCT_ID,
    "price.totalPP",
)


@dataclass(frozen=True, slots=True)
class StatementScope:
    """The entity scope a statement selection is confined to.

    With ``agreement_ids`` the scope is those agreements; otherwise the products (and seller).
    """

    product_ids: tuple[str, ...]
    seller_id: str
    agreement_ids: tuple[str, ...] = field(default=())


class StatementSelector:
    """Select automated billing statements for a run window.

    Runs two RQL passes (``audit.issued.at`` and ``audit.cancelled.at``), merges the
    results by ``statement.id`` so a statement matched by both passes appears once, and
    returns them.
    """

    def __init__(
        self,
        api_service: MPTAPIService,
        filter_builder: "StatementFilterBuilder | None" = None,
    ) -> None:
        self._api_service = api_service
        self._filter_builder = filter_builder or StatementFilterBuilder()

    async def select(
        self,
        window: RunWindow | None,
        product_ids: tuple[str, ...],
        seller_id: str,
        agreement_ids: tuple[str, ...] = (),
    ) -> list[Statement]:
        """Select statements issued or cancelled within the window, merged by id.

        A ``None`` window drops the date filter and selects every issued/cancelled statement
        for the scope (used by a full ``recalculate`` rebuild). When ``agreement_ids`` is given the
        scope is narrowed to those agreements instead of the product/seller filter (used by a
        scoped ``recalculate``).
        """
        scope = StatementScope(product_ids, seller_id, agreement_ids)
        merged: dict[str, Statement] = {}
        selected_passes = await asyncio.gather(
            *(self._select_pass(scope, window, audit_field, status) for audit_field, status in _PASSES)
        )
        for selected in selected_passes:
            merged.update(selected)
        return list(merged.values())

    async def _select_pass(
        self, scope: StatementScope, window: RunWindow | None, audit_field: str, status: str
    ) -> dict[str, Statement]:
        """Select the statements of one audit/status pass, keyed by statement id."""
        query = self._filter_builder.build(scope, window, audit_field, status)
        statements = self._api_service.client.billing.statements
        return {
            statement.id: statement
            async for statement in statements.filter(query).select(*_SELECT_FIELDS).iterate()
        }


class StatementFilterBuilder:
    """Build RQL statement filters used by the selector pass loop."""

    def build(
        self,
        scope: StatementScope,
        window: RunWindow | None,
        audit_field: str,
        status: str,
    ) -> RQLQuery:
        """Build the full statement filter for a single audit/status pass."""
        query = self._base_statement_filter(scope) & self._status_filter(status)
        if window is not None:
            query &= self._field_window(audit_field, window)
        logger.info("Selecting %s statements by %s with RQL: %s", status, audit_field, query)
        return query

    def _base_statement_filter(self, scope: StatementScope) -> RQLQuery:
        """Build the shared RQL filter for automated statements of the scope's agreements/products.

        With ``agreement_ids`` the scope is those agreements; otherwise the products (and seller).
        """
        automated = RQLQuery().n("billingType").eq("Automated")
        if scope.agreement_ids:
            agreements = RQLQuery().n(_AGREEMENT_ID).in_(list(scope.agreement_ids))
            return automated & agreements
        products = RQLQuery().n(_PRODUCT_ID).in_(list(scope.product_ids))
        rql = automated & products
        if scope.seller_id:
            rql &= RQLQuery().n("seller.id").eq(scope.seller_id)
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

    def __init__(self, statements: list[Statement], window: RunWindow | None) -> None:
        self._statements = statements
        self._window = window

    def render(self) -> None:
        """Print the run summary line and, when statements were selected, the table."""
        count = len(self._statements)
        typer.echo(f"Selected {count} statement(s){self._span()}")
        if self._statements:
            Console().print(self._table())

    def _span(self) -> str:
        if self._window is None:
            return ""
        start = self._window.start.strftime("%Y-%m-%d")
        end = self._window.end.strftime("%Y-%m-%d")
        return f" for {start}..{end}"

    def _table(self) -> Table:
        table = Table(*_REPORT_HEADERS)
        for statement in self._statements:
            table.add_row(*(self._field(statement, path) for path in _REPORT_PATHS))
        return table

    def _field(self, statement: Statement, path: str) -> str:
        current: Any = statement
        for attr in path.split("."):
            current = getattr(current, attr, None)
            if current is None:
                return "-"
        return str(current)
