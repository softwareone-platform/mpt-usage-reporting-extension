import datetime as dt

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.statements import StatementSelector
from mpt_usage_reporting_extension.window import RunWindow


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


def _context(mocker, product_ids=("PRD-1",), seller_id="", *, pages=None):
    if pages is None:
        pages = [[], []]
    api_client = mocker.Mock()
    service = api_client.client.billing.statements
    filtered = service.filter.return_value
    filtered.select.return_value.iterate.side_effect = [_aiter(page) for page in pages]
    return RunContext(
        api_service=api_client,
        window=RunWindow(
            start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
            end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
        ),
        product_ids=product_ids,
        seller_id=seller_id,
    )


def _queries(ctx):
    statements = ctx.api_service.client.billing.statements
    filter_mock = statements.filter
    return [str(call.args[0]) for call in filter_mock.call_args_list]


async def test_select_filters_automated_products(mocker):
    ctx = _context(mocker, product_ids=("PRD-1", "PRD-2"))

    await StatementSelector().select(ctx)  # act

    rendered = _queries(ctx)
    assert all("eq(billingType,'Automated')" in query for query in rendered)
    assert all("in(product.id,('PRD-1','PRD-2'))" in query for query in rendered)


async def test_select_omits_seller_when_empty(mocker):
    ctx = _context(mocker, seller_id="")

    await StatementSelector().select(ctx)  # act

    assert all("seller.id" not in query for query in _queries(ctx))


async def test_select_includes_seller_when_set(mocker):
    ctx = _context(mocker, seller_id="ACC-9")

    await StatementSelector().select(ctx)  # act

    assert all("eq(seller.id,'ACC-9')" in query for query in _queries(ctx))


async def test_select_runs_issued_and_cancelled_passes(mocker):
    issued = [mocker.Mock(id="BILL-1")]
    cancelled = [mocker.Mock(id="BILL-2")]
    ctx = _context(mocker, pages=[issued, cancelled])

    await StatementSelector().select(ctx)  # act

    assert [statement.id for statement in ctx.statements] == ["BILL-1", "BILL-2"]
    queries = _queries(ctx)
    assert any("audit.issued.at" in query for query in queries)
    assert any("audit.cancelled.at" in query for query in queries)


async def test_select_renders_datetimes_unquoted(mocker):
    ctx = _context(mocker)

    await StatementSelector().select(ctx)  # act

    issued_query = _queries(ctx)[0]
    assert "ge(audit.issued.at,2026-06-01T00:00:00Z)" in issued_query
    assert "lt(audit.issued.at,2026-06-02T00:00:00Z)" in issued_query
    assert all("'2026-06-0" not in query for query in _queries(ctx))


async def test_select_filters_by_status_per_pass(mocker):
    ctx = _context(mocker)

    await StatementSelector().select(ctx)  # act

    issued_query, cancelled_query = _queries(ctx)
    assert "eq(status,'Issued')" in issued_query
    assert "eq(status,'Cancelled')" in cancelled_query


async def test_select_merges_duplicates_by_id(mocker):
    issued = [mocker.Mock(id="BILL-1")]
    cancelled = [mocker.Mock(id="BILL-1")]
    ctx = _context(mocker, pages=[issued, cancelled])

    await StatementSelector().select(ctx)  # act

    assert len(ctx.statements) == 1
