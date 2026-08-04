import datetime as dt
import re
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

_SQL_DIAGNOSTICS = re.compile(r"\[(?:SQL|parameters):.*?\]", re.DOTALL)
_URL_USERINFO = re.compile(r"(?<=://)[^/\s@]+(?=@)")
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+\S+")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|sig"
    r"|signature)\b(\s*[=:]\s*)\S+"
)
_TRACEBACK_PATH = re.compile(r'File "([^"]+)"')
_PATH_MARKERS = ("site-packages/", "mpt_usage_reporting_extension/")


def _strip_path(match: re.Match[str]) -> str:
    path = match.group(1)
    stripped = path.rsplit("/", 1)[-1]
    for marker in _PATH_MARKERS:
        idx = path.rfind(marker)
        if idx != -1:
            stripped = path[idx:]
            break
    return f'File "{stripped}"'


def sanitize_diagnostics(text: str) -> str:
    """Scrub exception diagnostics before they leave the host for MS Teams.

    Drops SQLAlchemy ``[SQL: ...]``/``[parameters: ...]`` sections (statements and customer
    values), credentials (URL userinfo, bearer tokens, ``key=value`` secrets), and the local
    filesystem prefix of stacktrace paths, keeping the exception type, message, and frame
    locations that identify the failure.
    """
    text = _SQL_DIAGNOSTICS.sub("[redacted]", text)
    text = _URL_USERINFO.sub("[redacted]", text)
    text = _BEARER_TOKEN.sub("[redacted]", text)
    text = _CREDENTIAL_ASSIGNMENT.sub(r"\1\2[redacted]", text)
    return _TRACEBACK_PATH.sub(_strip_path, text)


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
        """Send an error card with the execution facts, error message, and stacktrace.

        Both the error message and the stacktrace are passed through
        ``sanitize_diagnostics`` first, so raw exception details never reach Teams.
        """
        error = sanitize_diagnostics(error)
        stacktrace = sanitize_diagnostics(stacktrace)
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
