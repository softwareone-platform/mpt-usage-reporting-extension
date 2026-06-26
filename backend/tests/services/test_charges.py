from decimal import Decimal

from mpt_usage_reporting_extension.accumulation import (
    ChargeAccumulation,
    ChargeTotals,
    StatementChargeFilter,
)
from mpt_usage_reporting_extension.services.charges import (
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
)


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


async def _drain(charges):
    return [charge async for charge in charges]


def _charges(api_service):
    billing = api_service.client.billing
    return billing.statements.charges


async def test_stream_calls_endpoint_per_statement(api_service, statement_factory):
    statements = [statement_factory("BILL-1"), statement_factory("BILL-2")]
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter([]), _aiter([])]

    await _drain(ChargeStreamer(api_service).stream(statements))  # act

    charges = _charges(api_service)
    assert [call.args[0] for call in charges.call_args_list] == ["BILL-1", "BILL-2"]


async def test_stream_attaches_statement_to_each_charge(
    api_service, statement_factory, statement_charge_factory
):
    statements = [statement_factory("BILL-1"), statement_factory("BILL-2")]
    pages = [[statement_charge_factory(), statement_charge_factory()], [statement_charge_factory()]]
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter(page) for page in pages]

    result = await _drain(ChargeStreamer(api_service).stream(statements))

    assert [charge.statement.id for charge in result] == ["BILL-1", "BILL-1", "BILL-2"]


async def test_stream_yields_charge_objects(
    api_service, statement_factory, statement_charge_factory
):
    statements = [statement_factory("BILL-1")]
    charges = [statement_charge_factory(), statement_charge_factory()]
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter(charges)]

    result = await _drain(ChargeStreamer(api_service).stream(statements))

    assert result == charges


def test_stream_is_lazy(api_service, statement_factory):
    statements = [statement_factory("BILL-1")]
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter([])]

    ChargeStreamer(api_service).stream(statements)  # act

    charges = _charges(api_service)
    assert charges.call_count == 0


async def test_accumulate_sums_by_full_key(statement_charge_factory, statement_factory):
    june = statement_factory(issued="2026-06-01T10:00:00Z")
    july = statement_factory(issued="2026-07-01T10:00:00Z")
    charges = [
        statement_charge_factory("AGR-1", "SUB-1", statement=june, price=("1.50", "2.00")),
        statement_charge_factory("AGR-1", "SUB-1", statement=june, price=("0.50", "1.00")),
        statement_charge_factory("AGR-1", "SUB-1", statement=july, price=("3.00", "4.00")),
    ]

    result = await ChargeAccumulator().accumulate(_aiter(charges))

    assert result.charge_count == 3
    june_bucket = result.accumulations["AGR-1", "SUB-1", 2026, 6]
    assert june_bucket.ppx1 == Decimal("2.00")
    assert june_bucket.spx1 == Decimal("3.00")
    assert result.accumulations["AGR-1", "SUB-1", 2026, 7].ppx1 == Decimal("3.00")


async def test_accumulate_handles_missing_fields(statement_charge_factory):
    result = await ChargeAccumulator().accumulate(_aiter([statement_charge_factory()]))

    assert result.charge_count == 1
    bucket = result.accumulations["-", "agreement_additional_-", 2026, 6]
    assert bucket.ppx1 == Decimal(0)
    assert bucket.spx1 == Decimal(0)


async def test_accumulate_labels_missing_subscription(statement_charge_factory, statement_factory):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory("AGR-1", statement=statement, price=("1.00", "1.00"))

    result = await ChargeAccumulator().accumulate(_aiter([charge]))

    assert ("AGR-1", "agreement_additional_AGR-1", 2026, 6) in result.accumulations


async def test_accumulate_handles_unparseable_date(statement_charge_factory, statement_factory):
    statement = statement_factory(issued="not-a-date")
    charge = statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00"))

    result = await ChargeAccumulator().accumulate(_aiter([charge]))

    bucket = result.accumulations["AGR-1", "SUB-1", None, None]
    assert bucket.ppx1 == Decimal("1.00")


async def test_accumulate_prefers_cancelled_over_issued(
    statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z", cancelled="2026-07-01T10:00:00Z")
    charge = statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00"))

    result = await ChargeAccumulator().accumulate(_aiter([charge]))

    assert ("AGR-1", "SUB-1", 2026, 7) in result.accumulations


async def test_accumulate_falls_back_to_issued(statement_charge_factory, statement_factory):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00"))

    result = await ChargeAccumulator().accumulate(_aiter([charge]))

    assert ("AGR-1", "SUB-1", 2026, 6) in result.accumulations


async def test_accumulate_filters_by_charge_filter(statement_charge_factory, statement_factory):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charges = [
        statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00")),
        statement_charge_factory("AGR-1", "SUB-2", statement=statement, price=("2.00", "2.00")),
    ]

    result = await ChargeAccumulator().accumulate(
        _aiter(charges), StatementChargeFilter.for_subscriptions(("SUB-1",))
    )

    assert result.charge_count == 1
    assert list(result.accumulations) == [("AGR-1", "SUB-1", 2026, 6)]


def test_filter_matches_selected_subscription(statement_charge_factory):
    charge_filter = StatementChargeFilter(("SUB-1",))
    selected = statement_charge_factory("AGR-1", "SUB-1")
    other = statement_charge_factory("AGR-1", "SUB-2")

    result = (charge_filter.matches(selected), charge_filter.matches(other))

    assert result == (True, False)


def test_filter_is_none_when_no_subscriptions():
    result = StatementChargeFilter.for_subscriptions(())

    assert result is None


def test_report_prints_summary_and_table(capsys):
    accumulation = ChargeAccumulation("AGR-1", "SUB-1", 2026, 6, ppx1=Decimal("2.00"))
    totals = ChargeTotals(
        charge_count=2,
        accumulations={("AGR-1", "SUB-1", 2026, 6): accumulation},
    )

    ChargeReport(totals).render()  # act

    out = capsys.readouterr().out
    assert "Streamed 2 charge(s) into 1 accumulation(s)" in out
    assert "AGR-1" in out
    assert "SUB-1" in out


def test_report_renders_none_for_missing_month(capsys):
    accumulation = ChargeAccumulation("AGR-9", "SUB-9", None, None, ppx1=Decimal("1.00"))
    totals = ChargeTotals(
        charge_count=1,
        accumulations={("AGR-9", "SUB-9", None, None): accumulation},
    )

    ChargeReport(totals).render()  # act

    assert "None" in capsys.readouterr().out


def test_report_prints_summary_when_empty(capsys):
    ChargeReport(ChargeTotals()).render()  # act

    out = capsys.readouterr().out
    assert "Streamed 0 charge(s) into 0 accumulation(s)" in out
    assert "Agreement ID" not in out
