import typer

from mpt_usage_reporting_extension.cli.commands import (
    cleanup,
    run,
)

app = typer.Typer(
    help="Report MPT billing subscription usage.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """Report MPT billing subscription usage (keeps ``run`` as a named subcommand)."""


app.command()(run.run)
app.command()(cleanup.cleanup)


def main() -> None:
    """Entry point for the CLI."""
    app()
