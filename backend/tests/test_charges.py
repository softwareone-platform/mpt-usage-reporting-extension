import datetime as dt
from decimal import Decimal

from mpt_api_client.resources.billing.statement_charges import StatementCharge

from mpt_usage_reporting_extension.charges import (
    ChargeAccumulation,
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
    ChargeTotals,
)
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.window import RunWindow


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


_YEAR = 2026


def _price(ppx1, spx1):
    price = {}
    if ppx1 is not None:
        price["PPx1"] = ppx1
    if spx1 is not None:
        price["SPx1"] = spx1
    return price


def _charge(agreement_id=None, subscription_id=None, created=None, ppx1=None, spx1=None):
    payload = {}
    if agreement_id is not None:
        payload["agreement"] = {"id": agreement_id}
    if subscription_id is not None:
        payload["subscription"] = {"id": subscription_id}
    if created is not None:
        payload["audit"] = {"created": {"at": created}}
    price = _price(ppx1, spx1)
    if price:
        payload["price"] = price
    return StatementCharge(payload)


def test_stream_calls_endpoint_per_statement(mocker):
    ctx = _context(mocker, statement_ids=("BILL-1", "BILL-2"), pages=[[], []])

    list(ChargeStreamer().stream(ctx))  # act

    charges = _charges(ctx.api_client)
    assert [call.args[0] for call in charges.call_args_list] == ["BILL-1", "BILL-2"]


def test_stream_yields_charges_across_statements(mocker):
    page_one = [_charge(), _charge()]
    page_two = [_charge()]
    ctx = _context(mocker, statement_ids=("BILL-1", "BILL-2"), pages=[page_one, page_two])

    streamed = list(ChargeStreamer().stream(ctx))  # act

    assert streamed == [*page_one, *page_two]


def test_stream_is_lazy(mocker):
    ctx = _context(mocker, statement_ids=("BILL-1",), pages=[[]])

    ChargeStreamer().stream(ctx)  # act

    charges = _charges(ctx.api_client)
    assert charges.call_count == 0


def test_accumulate_sums_by_full_key():
    charges = [
        _charge("AGR-1", "SUB-1", "2026-06-01T10:00:00Z", ppx1="1.50", spx1="2.00"),
        _charge("AGR-1", "SUB-1", "2026-06-20T10:00:00Z", ppx1="0.50", spx1="1.00"),
        _charge("AGR-1", "SUB-1", "2026-07-01T10:00:00Z", ppx1="3.00", spx1="4.00"),
    ]

    totals = ChargeAccumulator().accumulate(charges)  # act

    assert totals.charge_count == 3
    june = totals.accumulations["AGR-1", "SUB-1", _YEAR, 6]
    assert june.ppx1 == Decimal("2.00")
    assert june.spx1 == Decimal("3.00")
    assert totals.accumulations["AGR-1", "SUB-1", _YEAR, 7].ppx1 == Decimal("3.00")


def test_accumulate_handles_missing_fields():
    totals = ChargeAccumulator().accumulate([_charge()])  # act

    assert totals.charge_count == 1
    bucket = totals.accumulations["-", "agreement_additional", 0, 0]
    assert bucket.ppx1 == Decimal(0)
    assert bucket.spx1 == Decimal(0)


def test_accumulate_labels_missing_subscription():
    charge = _charge("AGR-1", created="2026-06-01T10:00:00Z", ppx1="1.00")

    totals = ChargeAccumulator().accumulate([charge])  # act

    assert ("AGR-1", "agreement_additional", _YEAR, 6) in totals.accumulations


def test_accumulate_handles_unparseable_date():
    charge = _charge("AGR-1", "SUB-1", "not-a-date", ppx1="1.00")

    totals = ChargeAccumulator().accumulate([charge])  # act

    bucket = totals.accumulations["AGR-1", "SUB-1", 0, 0]
    assert bucket.ppx1 == Decimal("1.00")


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


def test_report_prints_summary_when_empty(capsys):
    ChargeReport(ChargeTotals()).render()  # act

    out = capsys.readouterr().out
    assert "Streamed 0 charge(s) into 0 accumulation(s)" in out
    assert "Agreement ID" not in out
