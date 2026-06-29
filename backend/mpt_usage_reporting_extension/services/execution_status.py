import typer
from rich.console import Console
from rich.table import Table

from mpt_usage_reporting_extension.persistence.models import ExecutionRecord

_HEADERS = ("Command", "Started At", "Completed At", "Status")


class StatusReport:
    """Render recent command executions as a console table."""

    def __init__(self, executions: list[ExecutionRecord]) -> None:
        self._executions = executions

    def render(self) -> None:
        """Print the recent executions table, or a notice when there are none."""
        if not self._executions:
            typer.echo("No command executions recorded yet.")
            return
        Console().print(self._table())

    def _table(self) -> Table:
        table = Table(*_HEADERS)
        for execution in self._executions:
            table.add_row(
                execution.command,
                execution.started_at,
                execution.completed_at or "-",
                execution.status,
            )
        return table
