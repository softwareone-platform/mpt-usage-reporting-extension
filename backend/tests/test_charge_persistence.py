from decimal import Decimal

import pytest

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.charge_persistence import ChargePersister
from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.sqlite.database import SqliteDatabase


@pytest.fixture
async def repos(tmp_path, schema):
    async with SqliteDatabase(tmp_path / "storage.db") as database:
        for statement in schema:
            await database.connection.execute(statement)  # noqa: WPS476
        yield database.subscription_repository(), database.agreement_repository()


async def test_persist_writes_each_bucket_to_both_repos(mocker, run_context, charge_totals_factory):
    real = ChargeAccumulation("AGR-1", "SUB-1", 2026, 6, Decimal("1.50"), Decimal("2.00"))
    synthetic = ChargeAccumulation("AGR-1", "agreement_additional_AGR-1", 2026, 6, Decimal("1.00"))
    run_context.charge_totals = charge_totals_factory(real, synthetic)
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await ChargePersister().persist(run_context, subscription_repo, agreement_repo)  # act

    assert subscription_repo.accumulate.call_count == 2
    assert agreement_repo.accumulate.call_count == 2
    subscription_repo.accumulate.assert_any_call(
        Charge("SUB-1", "AGR-1", 2026, 6, Decimal("1.50"), Decimal("2.00")),
    )


async def test_persist_skips_bucket_without_month(mocker, run_context, charge_totals_factory):
    dateless = ChargeAccumulation("AGR-1", "SUB-1", None, None, Decimal("1.00"))
    run_context.charge_totals = charge_totals_factory(dateless)
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await ChargePersister().persist(run_context, subscription_repo, agreement_repo)  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


async def test_persist_ignores_missing_totals(mocker, run_context):
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    await ChargePersister().persist(run_context, subscription_repo, agreement_repo)  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


async def test_persist_additive_upsert_is_exact(repos, run_context, charge_totals_factory):
    subscription_repo, agreement_repo = repos
    seed = Charge("SUB-1", "AGR-1", 2026, 5, Decimal("0.1"), Decimal("0.1"))
    await subscription_repo.accumulate(seed)
    await agreement_repo.accumulate(seed)
    bucket = ChargeAccumulation("AGR-1", "SUB-1", 2026, 5, Decimal("0.2"), Decimal("0.2"))
    run_context.charge_totals = charge_totals_factory(bucket)

    await ChargePersister().persist(run_context, subscription_repo, agreement_repo)  # act

    stored = await subscription_repo.get(
        subscription_id="SUB-1", agreement_id="AGR-1", year=2026, month=5
    )
    assert stored.ppx1 == Decimal("0.3")
    stored = await agreement_repo.get(agreement_id="AGR-1", year=2026, month=5)
    assert stored.ppx1 == Decimal("0.3")
