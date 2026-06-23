import datetime as dt

import pytest

from mpt_usage_reporting_extension.services.statements import (
    StatementFilterBuilder,
    StatementReport,
    StatementSelector,
)
from mpt_usage_reporting_extension.window import RunWindow


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


def _queries(api_service):
    filter_mock = api_service.client.billing.statements.filter
    return [str(call.args[0]) for call in filter_mock.call_args_list]


@pytest.fixture
def window():
    return RunWindow(
        start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
    )


@pytest.fixture
def statements_api(mocker):
    def factory(pages=None):
        if pages is None:
            pages = [[], []]
        api_service = mocker.Mock()
        service = api_service.client.billing.statements
        filtered = service.filter.return_value
        filtered.select.return_value.iterate.side_effect = [_aiter(page) for page in pages]
        return api_service

    return factory


async def test_select_filters_automated_products(statements_api, window):
    api_service = statements_api()

    await StatementSelector(api_service).select(window, ("PRD-1", "PRD-2"), "")  # act

    rendered = _queries(api_service)
    assert all("eq(billingType,'Automated')" in query for query in rendered)
    assert all("in(product.id,('PRD-1','PRD-2'))" in query for query in rendered)


async def test_select_omits_seller_when_empty(statements_api, window):
    api_service = statements_api()

    await StatementSelector(api_service).select(window, ("PRD-1",), "")  # act

    assert all("seller.id" not in query for query in _queries(api_service))


async def test_select_includes_seller_when_set(statements_api, window):
    api_service = statements_api()

    await StatementSelector(api_service).select(window, ("PRD-1",), "ACC-9")  # act

    assert all("eq(seller.id,'ACC-9')" in query for query in _queries(api_service))


async def test_select_runs_issued_and_cancelled_passes(mocker, statements_api, window):
    issued = [mocker.Mock(id="BILL-1")]
    cancelled = [mocker.Mock(id="BILL-2")]
    api_service = statements_api(pages=[issued, cancelled])

    statements = await StatementSelector(api_service).select(window, ("PRD-1",), "")  # act

    assert [statement.id for statement in statements] == ["BILL-1", "BILL-2"]
    queries = _queries(api_service)
    assert any("audit.issued.at" in query for query in queries)
    assert any("audit.cancelled.at" in query for query in queries)


async def test_select_renders_datetimes_unquoted(statements_api, window):
    api_service = statements_api()

    await StatementSelector(api_service).select(window, ("PRD-1",), "")  # act

    issued_query = _queries(api_service)[0]
    assert "ge(audit.issued.at,2026-06-01T00:00:00Z)" in issued_query
    assert "lt(audit.issued.at,2026-06-02T00:00:00Z)" in issued_query
    assert all("'2026-06-0" not in query for query in _queries(api_service))


async def test_select_filters_by_status_per_pass(statements_api, window):
    api_service = statements_api()

    await StatementSelector(api_service).select(window, ("PRD-1",), "")  # act

    issued_query, cancelled_query = _queries(api_service)
    assert "eq(status,'Issued')" in issued_query
    assert "eq(status,'Cancelled')" in cancelled_query


async def test_select_merges_duplicates_by_id(mocker, statements_api, window):
    issued = [mocker.Mock(id="BILL-1")]
    cancelled = [mocker.Mock(id="BILL-1")]
    api_service = statements_api(pages=[issued, cancelled])

    statements = await StatementSelector(api_service).select(window, ("PRD-1",), "")  # act

    assert len(statements) == 1


def test_filter_omits_window_when_none():
    builder = StatementFilterBuilder()

    result = builder.build(("PRD-9",), "", None, "audit.issued.at", "Issued")

    assert "audit.issued.at" not in str(result)
    assert "eq(status,'Issued')" in str(result)


def test_report_renders_without_window(capsys):
    StatementReport([], None).render()  # act

    assert capsys.readouterr().out.strip() == "Selected 0 statement(s)"
