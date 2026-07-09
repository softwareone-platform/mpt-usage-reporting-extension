import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

from microsoft_teams.cards import AdaptiveCard, CardElement, TextBlock
from mpt_extension_contrib.custom_notifications.channels.teams_async import (
    AsyncTeamsNotifications,
    AsyncTeamsNotifier,
)
from mpt_extension_contrib.custom_notifications.channels.teams_cards import (
    Color,
    FactsSection,
    facts_blocks,
)

from mpt_usage_reporting_extension.settings import ExtensionSettings
from mpt_usage_reporting_extension.utils import format_duration


@dataclass(frozen=True)
class ExecutionSummary:
    """One tracked execution to report to Teams."""

    name: str
    command: str
    started_at: dt.datetime
    duration: dt.timedelta


class ExecutionNotifier:
    """Report execution outcomes to MS Teams as success or error cards.

    Builds the Adaptive Cards directly (``send_card``) instead of using
    ``send_success``/``send_error``, so the execution facts always render at
    the top of the card, right under the title.
    """

    def __init__(self, teams: AsyncTeamsNotifier) -> None:
        self._teams = teams

    async def notify_success(
        self, execution: ExecutionSummary, report: Mapping[str, object]
    ) -> None:
        """Send a success card with the execution facts and the run report."""
        lines = [f"- {name}: {count}" for name, count in report.items()]
        report_lines = "\n".join(lines)
        await self._teams.send_card(
            self._card(
                f"✅ Command {execution.name} succeeded",
                "Good",
                f"--- Report ---\n{report_lines}",
                self._facts(execution),
            )
        )

    async def notify_failure(
        self, execution: ExecutionSummary, error: str, stacktrace: str = ""
    ) -> None:
        """Send an error card with the execution facts, error message, and stacktrace."""
        await self._teams.send_card(
            self._card(
                f"💣 Command {execution.name} failed",
                "Attention",
                f"--- Stacktrace ---\n{stacktrace}" if stacktrace else "",
                self._facts(execution, error=error),
            )
        )

    def _card(self, title: str, color: Color, text: str, facts: FactsSection) -> AdaptiveCard:
        heading = TextBlock(text=title, weight="Bolder", size="Large", color=color, wrap=True)
        body: list[CardElement] = [heading, *facts_blocks(facts)]
        if text:
            body.append(TextBlock(text=text, wrap=True))
        return AdaptiveCard(body=body)

    def _facts(self, execution: ExecutionSummary, error: str | None = None) -> FactsSection:
        entries = {
            "Started": execution.started_at.isoformat(sep=" ", timespec="seconds"),
            "Duration": format_duration(execution.duration),
            "Command": execution.command,
        }
        if error is not None:
            entries["Error message"] = error
        return FactsSection(title="Execution", entries=entries)


def build_execution_notifier(settings: ExtensionSettings) -> ExecutionNotifier:
    """Build the notifier from settings; a missing webhook disables sends entirely."""
    return ExecutionNotifier(
        AsyncTeamsNotifications(
            webhook_url=settings.teams_webhook_url,
            enabled=settings.teams_notifications_enabled and bool(settings.teams_webhook_url),
        )
    )
