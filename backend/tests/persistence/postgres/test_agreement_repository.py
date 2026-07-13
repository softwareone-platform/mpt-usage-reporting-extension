import psycopg
import pytest


async def test_invalid_month_is_rejected(agreement_repo, charge_factory, decimal_first, bad_month):
    with pytest.raises(psycopg.errors.CheckViolation):
        await agreement_repo.accumulate(
            charge_factory(decimal_first, decimal_first, agreement_id="AGR-1", month=bad_month)
        )  # act


async def test_invalid_year_is_rejected(agreement_repo, charge_factory, decimal_first, bad_year):
    with pytest.raises(psycopg.errors.CheckViolation):
        await agreement_repo.accumulate(
            charge_factory(decimal_first, decimal_first, agreement_id="AGR-1", year=bad_year)
        )  # act


async def test_accumulate_is_additive_for_repeated_keys(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_total,
    year,
    month,
):
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_second, agreement_id="AGR-1")
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_second, decimal_first, agreement_id="AGR-1")
    )

    result = await agreement_repo.engine.get(agreement_id="AGR-1", year=year, month=month)  # act

    assert result["ppx1"] == decimal_total
    assert result["spx1"] == decimal_total


async def test_prune_drops_old_agreement_rows(
    agreement_repo, charge_factory, decimal_first, decimal_zero
):
    # Anchor (2026, 6): keep the 18-month window 2025-01..2026-06; drop 2024-12 and older.
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", year=2024, month=6)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", year=2024, month=12)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", year=2025, month=1)
    )

    result = await agreement_repo.prune(2026, 6)  # act

    assert result == 2
    assert await agreement_repo.delete() == 1


async def test_prune_keeps_everything_within_window(
    agreement_repo, charge_factory, decimal_first, decimal_zero
):
    # 2025-01 is the oldest month kept by the 18-month window ending (2026, 6).
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", year=2025, month=1)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", year=2026, month=6)
    )

    result = await agreement_repo.prune(2026, 6)  # act

    assert result == 0


async def test_delete_agreement_repo(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_zero,
    other_month,
):
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1")
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", month=other_month)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-2")
    )

    result = await agreement_repo.delete(agreement_id="AGR-1")

    assert result == 2


async def test_delete_agreement_repo_all(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_zero,
):
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1")
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-2")
    )

    result = await agreement_repo.delete()

    assert result == 2
