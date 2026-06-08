from decimal import Decimal

import pytest

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.charge_persistence import ChargePersister
from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.sqlite.database import SqliteDatabase

_YEAR = 2026
_ONE = Decimal("0.1")
_TWO = Decimal("0.2")
_SUM = Decimal("0.3")

_SCHEMA = (
    """
    CREATE TABLE subscription_monthly_accumulation (
        subscription_id TEXT NOT NULL,
        agreement_id TEXT NOT NULL,
        year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
        month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
        ppx1 DECIMAL NOT NULL DEFAULT '0',
        spx1 DECIMAL NOT NULL DEFAULT '0',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (subscription_id, agreement_id, year, month)
    )
    """,
    """
    CREATE TABLE agreement_monthly_accumulation (
        agreement_id TEXT NOT NULL,
        year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
        month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
        ppx1 DECIMAL NOT NULL DEFAULT '0',
        spx1 DECIMAL NOT NULL DEFAULT '0',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (agreement_id, year, month)
    )
    """,
)


@pytest.fixture
def repos(tmp_path):
    with SqliteDatabase(tmp_path / "storage.db") as database:
        for statement in _SCHEMA:
            database.connection.execute(statement)
        yield database.subscription_repository(), database.agreement_repository()


def test_persist_writes_each_bucket_to_both_repos(
    mocker, run_context_factory, charge_totals_factory
):
    real = ChargeAccumulation("AGR-1", "SUB-1", _YEAR, 6, Decimal("1.50"), Decimal("2.00"))
    synthetic = ChargeAccumulation("AGR-1", "agreement_additional_AGR-1", _YEAR, 6, Decimal("1.00"))
    ctx = run_context_factory(charge_totals_factory(real, synthetic))
    subscription_repo = mocker.Mock()
    agreement_repo = mocker.Mock()

    ChargePersister().persist(ctx, subscription_repo, agreement_repo)  # act

    assert subscription_repo.accumulate.call_count == 2
    assert agreement_repo.accumulate.call_count == 2
    subscription_repo.accumulate.assert_any_call(
        Charge("SUB-1", "AGR-1", _YEAR, 6, Decimal("1.50"), Decimal("2.00")),
    )


def test_persist_skips_bucket_without_month(mocker, run_context_factory, charge_totals_factory):
    dateless = ChargeAccumulation("AGR-1", "SUB-1", None, None, Decimal("1.00"))
    ctx = run_context_factory(charge_totals_factory(dateless))
    subscription_repo = mocker.Mock()
    agreement_repo = mocker.Mock()

    ChargePersister().persist(ctx, subscription_repo, agreement_repo)  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


def test_persist_ignores_missing_totals(mocker, run_context_factory):
    ctx = run_context_factory()
    subscription_repo = mocker.Mock()
    agreement_repo = mocker.Mock()

    ChargePersister().persist(ctx, subscription_repo, agreement_repo)  # act

    subscription_repo.accumulate.assert_not_called()
    agreement_repo.accumulate.assert_not_called()


def test_persist_additive_upsert_is_exact(repos, run_context_factory, charge_totals_factory):
    subscription_repo, agreement_repo = repos
    subscription_repo.accumulate(Charge("SUB-1", "AGR-1", _YEAR, 5, _ONE, _ONE))
    agreement_repo.accumulate(Charge("SUB-1", "AGR-1", _YEAR, 5, _ONE, _ONE))
    bucket = ChargeAccumulation("AGR-1", "SUB-1", _YEAR, 5, _TWO, _TWO)
    ctx = run_context_factory(charge_totals_factory(bucket))

    ChargePersister().persist(ctx, subscription_repo, agreement_repo)  # act

    stored = subscription_repo.get(
        subscription_id="SUB-1", agreement_id="AGR-1", year=_YEAR, month=5
    )
    assert stored.ppx1 == _SUM
    assert agreement_repo.get(agreement_id="AGR-1", year=_YEAR, month=5).ppx1 == _SUM
