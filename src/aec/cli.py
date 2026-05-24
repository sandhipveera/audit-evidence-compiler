"""CLI entry point — `aec ask ...`. Real implementation lands in task 005."""
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


if __name__ == "__main__":
    app()
