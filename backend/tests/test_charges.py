import datetime as dt
from decimal import Decimal

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation, ChargeTotals
from mpt_usage_reporting_extension.charges import (
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
)
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.window import RunWindow

_YEAR = 2026


def _context(mocker, statement_ids=("BILL-1",), *, pages=None):
    api_client = mocker.Mock()
    if pages is not None:
        _charges(api_client).return_value.stream.side_effect = [iter(page) for page in pages]
    return RunContext(
        api_client=api_client,
        window=RunWindow(
            start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
            end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
        ),
        product_ids=("PRD-1",),
        statements=[mocker.Mock(id=statement_id) for statement_id in statement_ids],
    )


def _charges(api_client):
    billing = api_client.billing
    return billing.statements.charges


def test_stream_calls_endpoint_per_statement(mocker):
    ctx = _context(mocker, statement_ids=("BILL-1", "BILL-2"), pages=[[], []])

    list(ChargeStreamer().stream(ctx))  # act

    charges = _charges(ctx.api_client)
    assert [call.args[0] for call in charges.call_args_list] == ["BILL-1", "BILL-2"]


def test_stream_attaches_statement_to_each_charge(mocker, charge_factory):
    pages = [[charge_factory(), charge_factory()], [charge_factory()]]
    ctx = _context(mocker, statement_ids=("BILL-1", "BILL-2"), pages=pages)

    result = list(ChargeStreamer().stream(ctx))

    assert [charge.statement.id for charge in result] == ["BILL-1", "BILL-1", "BILL-2"]


def test_stream_yields_charge_objects(mocker, charge_factory):
    charges = [charge_factory(), charge_factory()]
    ctx = _context(mocker, statement_ids=("BILL-1",), pages=[charges])

    result = list(ChargeStreamer().stream(ctx))

    assert result == charges


def test_stream_is_lazy(mocker):
    ctx = _context(mocker, statement_ids=("BILL-1",), pages=[[]])

    ChargeStreamer().stream(ctx)  # act

    charges = _charges(ctx.api_client)
    assert charges.call_count == 0


def test_accumulate_sums_by_full_key(charge_factory, statement_factory):
    june = statement_factory(issued="2026-06-01T10:00:00Z")
    july = statement_factory(issued="2026-07-01T10:00:00Z")
    charges = [
        charge_factory("AGR-1", "SUB-1", statement=june, price=("1.50", "2.00")),
        charge_factory("AGR-1", "SUB-1", statement=june, price=("0.50", "1.00")),
        charge_factory("AGR-1", "SUB-1", statement=july, price=("3.00", "4.00")),
    ]

    result = ChargeAccumulator().accumulate(charges)

    assert result.charge_count == 3
    june_bucket = result.accumulations["AGR-1", "SUB-1", _YEAR, 6]
    assert june_bucket.ppx1 == Decimal("2.00")
    assert june_bucket.spx1 == Decimal("3.00")
    assert result.accumulations["AGR-1", "SUB-1", _YEAR, 7].ppx1 == Decimal("3.00")


def test_accumulate_handles_missing_fields(charge_factory):
    result = ChargeAccumulator().accumulate([charge_factory()])

    assert result.charge_count == 1
    bucket = result.accumulations["-", "agreement_additional_-", None, None]
    assert bucket.ppx1 == Decimal(0)
    assert bucket.spx1 == Decimal(0)


def test_accumulate_labels_missing_subscription(charge_factory, statement_factory):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = charge_factory("AGR-1", statement=statement, price=("1.00", None))

    result = ChargeAccumulator().accumulate([charge])

    assert ("AGR-1", "agreement_additional_AGR-1", _YEAR, 6) in result.accumulations


def test_accumulate_handles_unparseable_date(charge_factory, statement_factory):
    statement = statement_factory(issued="not-a-date")
    charge = charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", None))

    result = ChargeAccumulator().accumulate([charge])

    bucket = result.accumulations["AGR-1", "SUB-1", None, None]
    assert bucket.ppx1 == Decimal("1.00")


def test_accumulate_prefers_cancelled_over_issued(charge_factory, statement_factory):
    statement = statement_factory(issued="2026-06-01T10:00:00Z", cancelled="2026-07-01T10:00:00Z")
    charge = charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", None))

    result = ChargeAccumulator().accumulate([charge])

    assert ("AGR-1", "SUB-1", _YEAR, 7) in result.accumulations


def test_accumulate_falls_back_to_issued(charge_factory, statement_factory):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", None))

    result = ChargeAccumulator().accumulate([charge])

    assert ("AGR-1", "SUB-1", _YEAR, 6) in result.accumulations


def test_report_prints_summary_and_table(capsys):
    accumulation = ChargeAccumulation("AGR-1", "SUB-1", _YEAR, 6, ppx1=Decimal("2.00"))
    totals = ChargeTotals(
        charge_count=2,
        accumulations={("AGR-1", "SUB-1", _YEAR, 6): accumulation},
    )

    ChargeReport(totals).render()  # act

    out = capsys.readouterr().out
    assert "Streamed 2 charge(s) into 1 accumulation(s)" in out
    assert "AGR-1" in out
    assert "SUB-1" in out


def test_report_renders_dash_for_missing_month(capsys):
    accumulation = ChargeAccumulation("AGR-9", "SUB-9", None, None, ppx1=Decimal("1.00"))
    totals = ChargeTotals(
        charge_count=1,
        accumulations={("AGR-9", "SUB-9", None, None): accumulation},
    )

    ChargeReport(totals).render()  # act

    assert "None" not in capsys.readouterr().out


def test_report_prints_summary_when_empty(capsys):
    ChargeReport(ChargeTotals()).render()  # act

    out = capsys.readouterr().out
    assert "Streamed 0 charge(s) into 0 accumulation(s)" in out
    assert "Agreement ID" not in out
