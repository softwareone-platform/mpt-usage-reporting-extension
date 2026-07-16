import typer

from mpt_usage_reporting_extension.cli.commands import (
    cleanup,
    delete,
    push_estimates_by_id,
    push_estimates_by_updated_at,
    recalculate,
    run,
    status,
)
from mpt_usage_reporting_extension.observability import setup_observability

app = typer.Typer(
    help="Report MPT billing subscription usage.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """Report MPT billing subscription usage (keeps ``run`` as a named subcommand)."""
    setup_observability()


_push_estimates_app = typer.Typer(
    help="Push subscription price estimates from stored usage.",
    no_args_is_help=True,
)
_push_estimates_app.command(name="by-id")(push_estimates_by_id.push_estimates_by_id_command)
_push_estimates_app.command(name="by-updated-at")(
    push_estimates_by_updated_at.push_estimates_by_updated_at_command
)

app.command()(run.run)
app.command()(cleanup.cleanup)
app.command(name="delete")(delete.delete_command)
app.command()(recalculate.recalculate)
app.command(name="status")(status.status_command)
app.add_typer(_push_estimates_app, name="push-estimates")


def main() -> None:
    """Entry point for the CLI."""
    app()
