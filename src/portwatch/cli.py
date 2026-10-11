"""PortWatch command line interface."""

import typer

from portwatch.doctor import diagnose, required_checks_pass

app = typer.Typer(help="PortWatch local data engineering commands.")


@app.callback()
def main() -> None:
    """PortWatch CLI."""


@app.command()
def doctor() -> None:
    """Check package, configuration, local storage, database, and PostGIS."""
    checks = diagnose()
    for check in checks:
        typer.echo(f"[{check.status}] {check.name}: {check.detail}")
    healthy = required_checks_pass(checks)
    typer.echo(f"Overall: {'PASS' if healthy else 'FAIL'}")
    if not healthy:
        raise typer.Exit(code=1)
