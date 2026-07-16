import datetime as dt

import pytest
from microsoft_teams.cards import FactSet, TextBlock

from mpt_usage_reporting_extension.services import execution_notifier
from mpt_usage_reporting_extension.settings import ExtensionSettings


@pytest.fixture
def teams(mocker):
    return mocker.AsyncMock()


@pytest.fixture
def execution():
    return execution_notifier.ExecutionSummary(
        name="run",
        command="mpt-billing-subscription-usage run --date 2026-07-01",
        started_at=dt.datetime(2026, 7, 1, 2, 0, 0, tzinfo=dt.UTC),
        duration=dt.timedelta(hours=1, minutes=31, seconds=16),
    )


def _sent_card(teams):
    teams.send_card.assert_called_once()
    return teams.send_card.call_args.args[0]


def _fact_entries(card):
    fact_set = next(element for element in card.body if isinstance(element, FactSet))
    return {fact.title: fact.value for fact in fact_set.facts}


async def test_notify_success_sends_success_card(teams, execution):
    report = {"statements": 3, "estimates_failed": 0}

    await execution_notifier.ExecutionNotifier(teams).notify_success(execution, report)  # act

    card = _sent_card(teams)
    assert card.body[0].text == "✅ Command run succeeded"
    assert card.body[0].color == "Good"
    text = card.body[-1].text
    assert "--- Report ---" in text
    assert "- statements: 3" in text
    assert "- estimates_failed: 0" in text


async def test_notify_success_renders_facts_above_the_report(teams, execution):
    await execution_notifier.ExecutionNotifier(teams).notify_success(
        execution, {"statements": 3}
    )  # act

    card = _sent_card(teams)
    _heading, facts_title, facts, report = card.body
    assert facts_title.text == "Execution"
    assert isinstance(facts, FactSet)
    assert isinstance(report, TextBlock)


async def test_notify_success_includes_execution_facts(teams, execution):
    await execution_notifier.ExecutionNotifier(teams).notify_success(execution, {})  # act

    entries = _fact_entries(_sent_card(teams))
    assert entries["Started"] == "2026-07-01 02:00:00+00:00"
    assert entries["Duration"] == "1h 31min 16 seconds"
    assert entries["Command"] == execution.command


async def test_notify_failure_sends_error_card_with_stacktrace(teams, execution):
    await execution_notifier.ExecutionNotifier(teams).notify_failure(
        execution, "boom", "Traceback: boom"
    )  # act

    card = _sent_card(teams)
    assert card.body[0].text == "💣 Command run failed"
    assert card.body[0].color == "Attention"
    text = card.body[-1].text
    assert "--- Stacktrace ---" in text
    assert "Traceback: boom" in text


async def test_notify_failure_includes_error_fact(teams, execution):
    await execution_notifier.ExecutionNotifier(teams).notify_failure(execution, "boom")  # act

    entries = _fact_entries(_sent_card(teams))
    assert entries["Error message"] == "boom"
    assert entries["Command"] == execution.command


async def test_notify_failure_omits_stacktrace_section_when_empty(teams, execution):
    await execution_notifier.ExecutionNotifier(teams).notify_failure(
        execution, "completed with errors"
    )  # act

    card = _sent_card(teams)
    assert isinstance(card.body[-1], FactSet)


def test_build_execution_notifier_enables_when_webhook_configured(mocker):
    teams_cls = mocker.patch.object(execution_notifier, "AsyncTeamsNotifications")
    settings = ExtensionSettings(
        product_ids=("PRD-1",),
        database_url="",
        teams_webhook_url="https://hooks.example.com/workflow",
        teams_notifications_enabled=True,
    )

    result = execution_notifier.build_execution_notifier(settings)  # act

    teams_cls.assert_called_once_with(
        webhook_url="https://hooks.example.com/workflow", enabled=True
    )
    assert isinstance(result, execution_notifier.ExecutionNotifier)


def test_build_execution_notifier_disables_when_webhook_missing(mocker):
    teams_cls = mocker.patch.object(execution_notifier, "AsyncTeamsNotifications")
    settings = ExtensionSettings(product_ids=("PRD-1",), database_url="")

    execution_notifier.build_execution_notifier(settings)  # act

    teams_cls.assert_called_once_with(webhook_url="", enabled=False)


def test_build_execution_notifier_respects_disabled_flag(mocker):
    teams_cls = mocker.patch.object(execution_notifier, "AsyncTeamsNotifications")
    settings = ExtensionSettings(
        product_ids=("PRD-1",),
        database_url="",
        teams_webhook_url="https://hooks.example.com/workflow",
        teams_notifications_enabled=False,
    )

    execution_notifier.build_execution_notifier(settings)  # act

    teams_cls.assert_called_once_with(
        webhook_url="https://hooks.example.com/workflow", enabled=False
    )
