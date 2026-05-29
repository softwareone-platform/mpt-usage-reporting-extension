"""Command line interface for the MPT usage reporting extension."""

import typer

app = typer.Typer(
    help="Report MPT billing subscription usage.",
    no_args_is_help=False,
)

NOT_IMPLEMENTED_ART = r"""
  _   _       _     _                 _                           _           _
 | \ | |     | |   (_)               | |                         | |         | |
 |  \| | ___ | |_   _ _ __ ___  _ __ | | ___ _ __ ___   ___ _ __ | |_ ___  __| |
 | . ` |/ _ \| __| | | '_ ` _ \| '_ \| |/ _ \ '_ ` _ \ / _ \ '_ \| __/ _ \/ _` |
 | |\  | (_) | |_  | | | | | | | |_) | |  __/ | | | | |  __/ | | | ||  __/ (_| |
 |_| \_|\___/ \__| |_|_| |_| |_| .__/|_|\___|_| |_| |_|\___|_| |_|\__\___|\__,_|
                               | |
                               |_|
"""


@app.command()
def billing_subscription_usage() -> None:
    """Report billing subscription usage for an account."""
    typer.secho(NOT_IMPLEMENTED_ART, fg=typer.colors.YELLOW)
    typer.secho("Not implemented", fg=typer.colors.YELLOW)


def main() -> None:
    """Entry point for the CLI."""
    app()
