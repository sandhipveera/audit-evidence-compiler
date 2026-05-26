"""Rich TUI for the panel debate view during `aec ask`."""
from __future__ import annotations

from typing import Literal

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

PersonaName = Literal["auditor", "engineer", "adversary", "security_model"]

PERSONA_COLORS: dict[PersonaName, str] = {
    "auditor": "cyan",
    "engineer": "green",
    "adversary": "red",
    "security_model": "magenta",
}

PERSONA_LABELS: dict[PersonaName, str] = {
    "auditor": "AUDITOR (Claude)",
    "engineer": "ENGINEER (GPT)",
    "adversary": "ADVERSARY (Gemini)",
    "security_model": "SECURITY MODEL (Foundation-Sec-8B)",
}


class PanelView:
    """Live display for the panel debate."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._states: dict[PersonaName, str] = {
            "auditor": "waiting...",
            "engineer": "waiting...",
            "adversary": "waiting...",
            "security_model": "waiting...",
        }
        self._verdicts: dict[PersonaName, str | None] = {
            "auditor": None,
            "engineer": None,
            "adversary": None,
            "security_model": None,
        }
        self._live: Live | None = None

    def _build_layout(self) -> Layout:
        layout = Layout()
        panels: list[Layout] = []
        for persona in ("auditor", "engineer", "adversary", "security_model"):
            color = PERSONA_COLORS[persona]
            label = PERSONA_LABELS[persona]
            body = Text(self._states[persona])
            verdict = self._verdicts[persona]
            if verdict:
                body.append(f"\n\n→ {verdict}", style=f"bold {color}")
            p = Panel(body, title=f"[bold {color}]{label}[/]", border_style=color)
            panels.append(Layout(p, name=persona))
        layout.split_row(*panels)
        return layout

    def start(self) -> None:
        self._live = Live(
            self._build_layout(),
            console=self._console,
            refresh_per_second=4,
        )
        self._live.start()

    def update(self, persona: PersonaName, status: str, verdict: str | None = None) -> None:
        self._states[persona] = status
        if verdict:
            self._verdicts[persona] = verdict
        if self._live:
            self._live.update(self._build_layout())

    def finish(self, final_verdict: str, consensus_method: str) -> None:
        if self._live:
            self._live.stop()
        style = "green" if final_verdict == "PASS" else "yellow" if final_verdict == "PARTIAL" else "red"
        self._console.print()
        self._console.print(
            Panel(
                f"[bold {style}]CONSENSUS: {final_verdict}[/]  ({consensus_method})",
                title="[bold]Panel Result[/]",
                border_style=style,
            )
        )

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None
