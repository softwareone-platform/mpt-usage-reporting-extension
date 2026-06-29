from decimal import Decimal

import pytest

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.sqlite.database import SqliteDatabase
from mpt_usage_reporting_extension.services.charge_persistence import AccumulationPersister


@pytest.fixture
async def repos(tmp_path, schema):
    async with SqliteDatabase(tmp_path / "storage.db") as database:
        for statement in schema:
            await database.connection.execute(statement)  # noqa: WPS476
        yield database.subscription_repository(), database.agreement_repository()


async def test_persist_writes_each_bucket_to_both_repos(mocker):
    real = ChargeAccumulation("AGR-1", "SUB-1", 2026, 6, Decimal("1.50"), Decimal("2.00"))
    synthetic = ChargeAccumulation("AGR-1", "agreement_additional_AGR-1", 2026, 6, Decimal("1.00"))
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await AccumulationPersister(subscription_repo, agreement_repo).persist([real, synthetic])  # act

    assert subscription_repo.accumulate.call_count == 2
    assert agreement_repo.accumulate.call_count == 2
    subscription_repo.accumulate.assert_any_call(
        Charge("SUB-1", "AGR-1", 2026, 6, Decimal("1.50"), Decimal("2.00")),
    )


async def test_persist_skips_bucket_without_month(mocker):
    dateless = ChargeAccumulation("AGR-1", "SUB-1", None, None, Decimal("1.00"))
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await AccumulationPersister(subscription_repo, agreement_repo).persist([dateless])  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


async def test_persist_restricts_agreement_writes(mocker):
    reset = ChargeAccumulation("AGR-1", "SUB-1", 2026, 6, Decimal("1.50"))
    sibling = ChargeAccumulation("AGR-2", "SUB-2", 2026, 6, Decimal("1.00"))
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await AccumulationPersister(subscription_repo, agreement_repo).persist(  # act
        [reset, sibling], frozenset(("AGR-1",))
    )

    # both subscription buckets are written, only AGR-1's agreement bucket is
    written = agreement_repo.accumulate.call_args.args[0]
    assert subscription_repo.accumulate.call_count == 2
    assert agreement_repo.accumulate.call_count == 1
    assert written.agreement_id == "AGR-1"


async def test_persist_empty_set_skips_agreement_table(mocker):
    bucket = ChargeAccumulation("AGR-1", "SUB-1", 2026, 6, Decimal("1.50"))
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await AccumulationPersister(subscription_repo, agreement_repo).persist(  # act
        [bucket], frozenset()
    )

    subscription_repo.accumulate.assert_called_once()
    agreement_repo.accumulate.assert_not_called()


async def test_persist_does_nothing_when_empty(mocker):
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await AccumulationPersister(subscription_repo, agreement_repo).persist([])  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


async def test_persist_dry_run_skips_writes(mocker):
    bucket = ChargeAccumulation("AGR-1", "SUB-1", 2026, 6, Decimal("1.50"), Decimal("2.00"))
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await AccumulationPersister(
        subscription_repo,
        agreement_repo,
        dry_run=True,
    ).persist([bucket])  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


async def test_persist_additive_upsert_is_exact(repos):
    subscription_repo, agreement_repo = repos
    seed = Charge("SUB-1", "AGR-1", 2026, 5, Decimal("0.1"), Decimal("0.1"))
    await subscription_repo.accumulate(seed)
    await agreement_repo.accumulate(seed)
    bucket = ChargeAccumulation("AGR-1", "SUB-1", 2026, 5, Decimal("0.2"), Decimal("0.2"))

    await AccumulationPersister(subscription_repo, agreement_repo).persist([bucket])  # act

    stored = await subscription_repo.get(subscription_id="SUB-1", year=2026, month=5)
    assert stored.ppx1 == Decimal("0.3")
