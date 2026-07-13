import datetime as dt

from mpt_usage_reporting_extension.services.accumulation_cleanup import (
    AccumulationCleaner,
    do_cleanup,
)


async def test_cleanup_prunes_both_repos(mocker):
    subscription_repo = mocker.AsyncMock()
    subscription_repo.prune.return_value = 3
    agreement_repo = mocker.AsyncMock()
    agreement_repo.prune.return_value = 1

    result = await AccumulationCleaner(subscription_repo, agreement_repo).cleanup(2026, 6)  # act

    assert result.year == 2026
    assert result.month == 6
    assert result.subscription_deleted == 3
    assert result.agreement_deleted == 1
    subscription_repo.prune.assert_awaited_once_with(2026, 6)
    agreement_repo.prune.assert_awaited_once_with(2026, 6)


async def test_cleanup_reports_the_summary(mocker, capsys):
    subscription_repo = mocker.AsyncMock()
    subscription_repo.prune.return_value = 4
    agreement_repo = mocker.AsyncMock()
    agreement_repo.prune.return_value = 2

    await AccumulationCleaner(subscription_repo, agreement_repo).cleanup(2026, 6)  # act

    out = capsys.readouterr().out
    assert "Pruned 4 subscription and 2 agreement row(s)" in out
    assert "18-month" in out
    assert "2026-06" in out


async def test_cleanup_dry_run_skips_prune_calls(mocker):
    subscription_repo = mocker.AsyncMock()
    agreement_repo = mocker.AsyncMock()

    result = await AccumulationCleaner(
        subscription_repo,
        agreement_repo,
        dry_run=True,
    ).cleanup(2026, 6)  # act

    assert result.subscription_deleted == 0
    assert result.agreement_deleted == 0
    subscription_repo.prune.assert_not_called()
    agreement_repo.prune.assert_not_called()


async def test_do_cleanup_opens_store_and_prunes(mocker):
    mocker.patch("mpt_usage_reporting_extension.services.accumulation_cleanup.resolve_database_url")
    database = mocker.patch(
        "mpt_usage_reporting_extension.services.accumulation_cleanup.PostgresDatabase"
    ).return_value.__aenter__.return_value
    subscription_repo = mocker.AsyncMock()
    subscription_repo.prune.return_value = 2
    agreement_repo = mocker.AsyncMock()
    agreement_repo.prune.return_value = 1
    database.subscription_repository = mocker.Mock(return_value=subscription_repo)
    database.agreement_repository = mocker.Mock(return_value=agreement_repo)
    database.execution_repository = mocker.Mock(return_value=mocker.AsyncMock())

    result = await do_cleanup(dt.date(2026, 6, 1), {})  # act

    assert result.subscription_deleted == 2
    assert result.agreement_deleted == 1
    subscription_repo.prune.assert_awaited_once_with(2026, 6)
    agreement_repo.prune.assert_awaited_once_with(2026, 6)
