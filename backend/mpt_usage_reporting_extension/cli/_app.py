import typer

from mpt_usage_reporting_extension.cli.commands import (
    cleanup,
    delete,
    push_estimates_by_id,
    push_estimates_by_updated_at,
    recalculate,
    run,
)

app = typer.Typer(
    help="Report MPT billing subscription usage.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """Report MPT billing subscription usage (keeps ``run`` as a named subcommand)."""


_push_estimates_app = typer.Typer(
    help="Push subscription price estimates from stored usage.",
    no_args_is_help=True,
)
_push_estimates_app.command(name="by-id")(push_estimates_by_id.push_estimates_by_id)
_push_estimates_app.command(name="by-updated-at")(
    push_estimates_by_updated_at.push_estimates_by_updated_at
)

app.command()(run.run)
app.command()(cleanup.cleanup)
app.command()(delete.delete)
app.command()(recalculate.recalculate)
app.add_typer(_push_estimates_app, name="push-estimates")


def main() -> None:
    """Entry point for the CLI."""
    app()
