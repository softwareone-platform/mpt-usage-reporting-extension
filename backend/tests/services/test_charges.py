from decimal import Decimal

import pytest
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.accumulation import StatementChargeFilter
from mpt_usage_reporting_extension.exceptions import ChargePriceError, UpstreamStatementError
from mpt_usage_reporting_extension.services.charges import (
    ChargeAccumulator,
    ChargeStreamer,
)
from mpt_usage_reporting_extension.services.execution_tracker import StatementProcessingRecorder
from mpt_usage_reporting_extension.types import StatementStatus


@pytest.fixture
def processing_repo(mocker):
    return mocker.AsyncMock()


@pytest.fixture
def recorder(processing_repo):
    return StatementProcessingRecorder(processing_repo, execution_id=7)


@pytest.fixture
def charge_stream(api_service):
    return _charges(api_service).return_value.stream


@pytest.fixture
def accumulate(mocker, recorder):
    """Accumulate the given charges, serving each charge's own statement its charges in order."""

    async def factory(charges, charge_filter=None, *, isolate_agreements=False):
        statements = []
        by_statement = {}
        for charge in charges:
            if id(charge.statement) not in by_statement:
                if "id" not in charge.statement.__dict__:
                    position = len(statements) + 1
                    charge.statement.id = f"SOM-{position}"
                statements.append(charge.statement)
                by_statement[id(charge.statement)] = []
            by_statement[id(charge.statement)].append(charge)
        streamer = mocker.create_autospec(ChargeStreamer, instance=True)
        streamer.stream.side_effect = lambda statement: _aiter(by_statement[id(statement)])
        return await ChargeAccumulator(streamer, recorder).accumulate(
            statements, charge_filter, isolate_agreements=isolate_agreements
        )

    return factory


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


async def _aiter_raises(exc):  # noqa: RUF029  # async generator that raises before yielding
    raise exc
    yield  # noqa: WPS427  # pragma: no cover  # unreachable; only marks this as a generator


async def _drain(charges):
    return [charge async for charge in charges]


def _charges(api_service):
    billing = api_service.client.billing
    return billing.statements.charges


async def test_stream_calls_the_endpoint_for_the_statement(api_service, statement_factory):
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter([])]

    await _drain(ChargeStreamer(api_service).stream(statement_factory("BILL-1")))  # act

    _charges(api_service).assert_called_once_with("BILL-1")


async def test_stream_attaches_the_statement_to_each_charge(
    api_service, statement_factory, statement_charge_factory
):
    statement = statement_factory("BILL-1")
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter([statement_charge_factory(), statement_charge_factory()])]

    result = await _drain(ChargeStreamer(api_service).stream(statement))

    assert [charge.statement.id for charge in result] == ["BILL-1", "BILL-1"]


def test_stream_is_lazy(api_service, statement_factory):
    stream = _charges(api_service).return_value.stream
    stream.side_effect = [_aiter([])]

    ChargeStreamer(api_service).stream(statement_factory("BILL-1"))  # act

    assert _charges(api_service).call_count == 0


async def test_stream_wraps_upstream_error(api_service, charge_stream, statement_factory):
    charge_stream.side_effect = [_aiter_raises(MPTError("boom"))]

    with pytest.raises(UpstreamStatementError, match="BILL-1"):
        await _drain(ChargeStreamer(api_service).stream(statement_factory("BILL-1")))


async def test_accumulate_records_each_statement(
    accumulate, api_service, recorder, charge_stream, processing_repo, statement_factory
):
    statements = [statement_factory("BILL-1"), statement_factory("BILL-2")]
    charge_stream.side_effect = [_aiter([]), _aiter([])]
    accumulator = ChargeAccumulator(ChargeStreamer(api_service), recorder)

    await accumulator.accumulate(statements)  # act

    assert [call.args for call in processing_repo.start.call_args_list] == [
        (7, "BILL-1"),
        (7, "BILL-2"),
    ]
    assert [call.args[1] for call in processing_repo.finish.call_args_list] == [
        StatementStatus.SUCCESS,
        StatementStatus.SUCCESS,
    ]


async def test_accumulate_records_a_streaming_failure_and_propagates(
    accumulate, api_service, recorder, charge_stream, processing_repo, statement_factory
):
    charge_stream.side_effect = [_aiter_raises(MPTError("boom"))]
    accumulator = ChargeAccumulator(ChargeStreamer(api_service), recorder)

    with pytest.raises(UpstreamStatementError, match="BILL-1"):
        await accumulator.accumulate([statement_factory("BILL-1")])

    processing_repo.finish.assert_awaited_once_with(
        processing_repo.start.return_value,
        StatementStatus.FAILURE,
        "Failed to stream charges for statement BILL-1",
    )


async def test_accumulate_fails_on_an_unpriceable_charge_and_records_its_statement(
    accumulate, processing_repo, statement_charge_factory, statement_factory
):
    good = statement_factory("SOM-GOOD", issued="2026-06-01T10:00:00Z")
    bad = statement_factory("SOM-BAD", issued="2026-06-01T10:00:00Z")
    after = statement_factory("SOM-AFTER", issued="2026-06-01T10:00:00Z")
    charges = [
        statement_charge_factory("AGR-1", "SUB-1", statement=good, price=("3.00", "3.30")),
        statement_charge_factory(
            "AGR-1", "SUB-1", statement=bad, price=("2.00", None), spx1="2.20", charge_id="CHG-2"
        ),
        statement_charge_factory("AGR-1", "SUB-1", statement=after, price=("1.00", "1.10")),
    ]

    with pytest.raises(ChargePriceError, match="Charge CHG-2 of statement SOM-BAD has no BSPx1"):
        await accumulate(charges)

    # the run stops at the bad statement: no totals leaving it out are returned
    assert [call.args[1] for call in processing_repo.finish.call_args_list] == [
        StatementStatus.SUCCESS,
        StatementStatus.FAILURE,
    ]


async def test_accumulate_isolating_agreements_leaves_out_only_the_failed_agreement(
    accumulate, processing_repo, statement_charge_factory, statement_factory
):
    # (statement, agreement, PPx1, BSPx1): SOM-BAD's charge has no BSPx1
    charges = _charges_of(
        statement_charge_factory,
        statement_factory,
        [
            ("SOM-OK", "AGR-1", "1.00", "1.10"),
            ("SOM-GOOD", "AGR-2", "2.00", "2.20"),
            ("SOM-BAD", "AGR-2", "3.00", None),
            ("SOM-LATER", "AGR-2", "4.00", "4.40"),
        ],
    )

    result = await accumulate(charges, isolate_agreements=True)

    # AGR-2 is left out whole, its already summed statement included, and not streamed further
    assert list(result.accumulations) == [("AGR-1", "SUB-1", 2026, 6)]
    assert result.failed_agreements == {
        "AGR-2": "Charge CHG-3 of statement SOM-BAD has no BSPx1, so it cannot be summed"
    }
    assert [call.args[1] for call in processing_repo.start.call_args_list] == [
        "SOM-OK",
        "SOM-GOOD",
        "SOM-BAD",
    ]


def _charges_of(statement_charge_factory, statement_factory, rows):
    """One June charge per row, in its own statement; AGR-n's charges belong to SUB-n."""
    return [
        statement_charge_factory(
            agreement_id,
            agreement_id.replace("AGR", "SUB"),
            statement=statement_factory(
                statement_id, issued="2026-06-01T10:00:00Z", agreement_id=agreement_id
            ),
            price=(ppx1, bspx1),
            charge_id=f"CHG-{position}",
        )
        for position, (statement_id, agreement_id, ppx1, bspx1) in enumerate(rows, start=1)
    ]


async def test_accumulate_sums_by_full_key(accumulate, statement_charge_factory, statement_factory):
    june = statement_factory(issued="2026-06-01T10:00:00Z")
    july = statement_factory(issued="2026-07-01T10:00:00Z")
    charges = [
        statement_charge_factory("AGR-1", "SUB-1", statement=june, price=("1.50", "2.00")),
        statement_charge_factory("AGR-1", "SUB-1", statement=june, price=("0.50", "1.00")),
        statement_charge_factory("AGR-1", "SUB-1", statement=july, price=("3.00", "4.00")),
    ]

    result = await accumulate(charges)

    assert result.charge_count == 3
    june_bucket = result.accumulations["AGR-1", "SUB-1", 2026, 6]
    assert june_bucket.ppx1 == Decimal("2.00")
    assert june_bucket.spx1 == Decimal("3.00")
    assert result.accumulations["AGR-1", "SUB-1", 2026, 7].ppx1 == Decimal("3.00")


async def test_accumulate_collects_purchase_currencies(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charges = [
        statement_charge_factory("AGR-1", "SUB-1", statement=statement, purchase_currency="USD"),
        statement_charge_factory("AGR-1", "SUB-1", statement=statement, purchase_currency="USD"),
        statement_charge_factory("AGR-1", "SUB-1", statement=statement),
    ]

    result = await accumulate(charges)

    assert result.accumulations["AGR-1", "SUB-1", 2026, 6].currencies == {"USD"}


async def test_accumulate_sums_bspx1_not_the_billing_currency_spx1(
    accumulate, statement_charge_factory, statement_factory
):
    # GIMO-shaped charge: BSPx1 in the authorization currency (USD), SPx1 converted to VND
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("19.1608169548", "19.784959367862143"),
        spx1="512869.51088726864",
        purchase_currency="USD",
    )

    result = await accumulate([charge])

    assert result.accumulations["AGR-1", "SUB-1", 2026, 6].spx1 == Decimal("19.784959367862143")


async def test_accumulate_sums_spx1_of_a_charge_without_bspx1_sold_in_its_purchase_currency(
    accumulate, statement_charge_factory, statement_factory
):
    # pre-BSPx1 charge sold in its purchase currency (TEST, EUR to EUR at rate 1)
    statement = statement_factory(issued="2026-01-10T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("428.2706789294851", None),
        spx1="471.09774682243363",
        purchase_currency="EUR",
        sale_currency="EUR",
    )

    result = await accumulate([charge])

    bucket = result.accumulations["AGR-1", "SUB-1", 2026, 1]
    assert (bucket.ppx1, bucket.spx1) == (
        Decimal("428.2706789294851"),
        Decimal("471.09774682243363"),
    )


@pytest.mark.parametrize(
    ("price", "spx1", "unit_pp", "currencies", "missing"),
    [
        (("428.27", None), "443.91", None, ("EUR", "CHF"), "BSPx1"),  # SPx1 in another currency
        (("309.09", None), "309.09", None, (None, None), "BSPx1"),  # currencies unknown
        (("309.09", None), "309.09", None, ("EUR", None), "BSPx1"),  # sale currency unknown
        (("428.27", None), None, None, ("EUR", "EUR"), "BSPx1 or SPx1"),  # one currency, no SPx1
        ((None, None), "471.10", None, (None, None), "BSPx1"),  # SPx1 only
        (("275.09", None), None, None, (None, None), "BSPx1"),  # PPx1 only
        ((None, None), None, "289.70", (None, None), "BSPx1"),  # oldest charges: unit prices only
        ((None, "5.00"), None, None, (None, None), "PPx1"),  # BSPx1 without PPx1
    ],
)
async def test_accumulate_fails_on_a_charge_missing_a_price(  # noqa: WPS211
    accumulate,
    processing_repo,
    statement_charge_factory,
    statement_factory,
    price,
    spx1,
    unit_pp,
    currencies,
    missing,
):
    statement = statement_factory("SOM-1", issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=price,
        spx1=spx1,
        unit_pp=unit_pp,
        charge_id="CHG-1",
        purchase_currency=currencies[0],
        sale_currency=currencies[1],
    )

    message = f"Charge CHG-1 of statement SOM-1 has no {missing}, so it cannot be summed"

    with pytest.raises(ChargePriceError) as exc_info:
        await accumulate([charge])

    assert str(exc_info.value) == message
    processing_repo.finish.assert_awaited_once_with(
        processing_repo.start.return_value, StatementStatus.FAILURE, message
    )


async def test_accumulate_handles_missing_fields(accumulate, statement_charge_factory):
    result = await accumulate([statement_charge_factory()])

    assert result.charge_count == 1
    bucket = result.accumulations["-", "agreement_additional_-", 2026, 6]
    assert bucket.ppx1 == Decimal(0)
    assert bucket.spx1 == Decimal(0)


async def test_accumulate_labels_missing_subscription(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory("AGR-1", statement=statement, price=("1.00", "1.00"))

    result = await accumulate([charge])

    assert ("AGR-1", "agreement_additional_AGR-1", 2026, 6) in result.accumulations


async def test_accumulate_handles_unparseable_date(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="not-a-date")
    charge = statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00"))

    result = await accumulate([charge])

    bucket = result.accumulations["AGR-1", "SUB-1", None, None]
    assert bucket.ppx1 == Decimal("1.00")


async def test_accumulate_prefers_charge_period_end_over_statement_dates(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-08-15T10:00:00Z", cancelled="2026-09-01T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("1.00", "1.00"),
        period_end="2026-07-31T23:59:59Z",
    )

    result = await accumulate([charge])

    assert ("AGR-1", "SUB-1", 2026, 7) in result.accumulations


async def test_accumulate_parses_offset_period_end(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-08-15T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("1.00", "1.00"),
        period_end="2025-01-31T23:59:59.0000000+00:00",
    )

    result = await accumulate([charge])

    assert ("AGR-1", "SUB-1", 2025, 1) in result.accumulations


async def test_accumulate_ignores_sentinel_period_end(
    accumulate, statement_charge_factory, statement_factory
):
    # the platform returns an absent period as the .NET DateTime.MinValue sentinel
    statement = statement_factory(issued="2026-08-15T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("1.00", "1.00"),
        period_end="0001-01-01T00:00:00.000Z",
    )

    result = await accumulate([charge])

    assert ("AGR-1", "SUB-1", 2026, 8) in result.accumulations


async def test_accumulate_ignores_sentinel_statement_dates(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="0001-01-01T00:00:00.000Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("1.00", "1.00"),
        period_end="0001-01-01T00:00:00.000Z",
    )

    result = await accumulate([charge])

    bucket = result.accumulations["AGR-1", "SUB-1", None, None]
    assert bucket.ppx1 == Decimal("1.00")


async def test_accumulate_falls_through_unparseable_period_end(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory(
        "AGR-1",
        "SUB-1",
        statement=statement,
        price=("1.00", "1.00"),
        period_end="not-a-date",
    )

    result = await accumulate([charge])

    assert ("AGR-1", "SUB-1", 2026, 6) in result.accumulations


async def test_accumulate_prefers_cancelled_over_issued(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z", cancelled="2026-07-01T10:00:00Z")
    charge = statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00"))

    result = await accumulate([charge])

    assert ("AGR-1", "SUB-1", 2026, 7) in result.accumulations


async def test_accumulate_falls_back_to_issued(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charge = statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00"))

    result = await accumulate([charge])

    assert ("AGR-1", "SUB-1", 2026, 6) in result.accumulations


async def test_accumulate_filters_by_charge_filter(
    accumulate, statement_charge_factory, statement_factory
):
    statement = statement_factory(issued="2026-06-01T10:00:00Z")
    charges = [
        statement_charge_factory("AGR-1", "SUB-1", statement=statement, price=("1.00", "1.00")),
        statement_charge_factory("AGR-1", "SUB-2", statement=statement, price=("2.00", "2.00")),
    ]

    result = await accumulate(charges, StatementChargeFilter.for_subscriptions(("SUB-1",)))

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
