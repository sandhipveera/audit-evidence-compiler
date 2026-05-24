"""CLI entry point — `aec ask ...`, `aec verify ...`."""
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Audit Evidence Auto-Compiler")
console = Console()


@app.command()
def ask(
    framework: str = typer.Option(..., help="soc2 | iso27001 | nist-csf | nist-800-53"),
    control: str = typer.Option(..., help="Framework control ID, e.g., CC6.1 or PR.AC-1"),
    output: str = typer.Option("gap_report.xlsx", help="Output xlsx path"),
):
    """Compile evidence for a framework control from Splunk."""
    console.print(f"[bold cyan]aec[/] — {framework} {control} → {output}")
    console.print("[yellow]Agent pipeline not yet wired. See tasks/003-langgraph-agent.md[/]")


@app.command()
def catalog():
    """Show priors catalog summary."""
    import json
    from importlib.resources import files

    path = files("aec.priors") / "catalog.json"
    c = json.loads(path.read_text())
    console.print(f"[bold]Catalog v{c['version']}[/]")
    console.print(f"  Controls: {len(c['controls'])}")
    console.print(f"  Frameworks: {', '.join(c['frameworks'])}")
    console.print(f"  Categories: {', '.join(c['control_categories'])}")


@app.command()
def verify(
    xlsx: Path = typer.Argument(..., help="Path to gap_report.xlsx"),
    trail: Path = typer.Option(None, "--trail", help="Path to audit_trail.jsonl"),
):
    """Verify the integrity of a gap report and its evidence chain."""
    from aec.integrity.manifest import verify_report

    if trail is None:
        trail = xlsx.parent / "audit_trail.jsonl"

    ok, messages = verify_report(xlsx, trail)

    for msg in messages:
        if ok or "verified" in msg.lower() or "matches" in msg.lower() or "consistent" in msg.lower():
            prefix = "[green]✓[/]"
        else:
            prefix = "[red]✗[/]"
        # First message (chain length) is always neutral
        if "Chain length:" in msg:
            prefix = "[green]✓[/]"
        console.print(f"{prefix} {msg}")

    if ok:
        console.print()
        console.print("[bold green]Report is verifiable. Nothing has been modified.[/]")
        raise SystemExit(0)
    else:
        console.print()
        console.print("[bold red]✗ TAMPERED — do not trust this report.[/]")
        raise SystemExit(1)


if __name__ == "__main__":
    app()
