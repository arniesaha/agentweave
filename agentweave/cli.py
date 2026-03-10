"""AgentWeave CLI — ``agentweave trace show/list/export``."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

app = typer.Typer(name="agentweave", help="AgentWeave — observability for multi-agent AI systems.")
trace_app = typer.Typer(name="trace", help="Inspect decision provenance traces.")
app.add_typer(trace_app)

console = Console()


def _get_provider():
    """Return the current TracerProvider (if any)."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider

    provider = otel_trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        return provider
    return None


# ---------------------------------------------------------------------------
# agentweave trace show <trace-id>
# ---------------------------------------------------------------------------


@trace_app.command("show")
def trace_show(
    trace_id: str = typer.Argument(help="The trace ID to display."),
) -> None:
    """Pretty-print a decision trail for a given trace ID."""
    # In v0.1 we display a placeholder — real backend querying comes in v0.2
    tree = Tree(f"[bold cyan]Trace:[/bold cyan] {trace_id}")
    tree.add("[dim]Span data requires a connected OTel backend (Langfuse, Jaeger, Tempo).[/dim]")
    tree.add("[dim]Use your backend's UI or API to query full span details.[/dim]")
    console.print(tree)
    console.print(
        "\n[yellow]Tip:[/yellow] Export traces with "
        "[bold]agentweave trace export <trace-id> --format prov-json[/bold]"
    )


# ---------------------------------------------------------------------------
# agentweave trace list
# ---------------------------------------------------------------------------


@trace_app.command("list")
def trace_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent traces to show."),
    endpoint: Optional[str] = typer.Option(None, "--endpoint", "-e", help="OTLP endpoint to query."),
) -> None:
    """List recent traces from the configured backend."""
    table = Table(title="Recent Traces")
    table.add_column("Trace ID", style="cyan")
    table.add_column("Agent", style="green")
    table.add_column("Root Span", style="white")
    table.add_column("Status", style="bold")
    table.add_column("Duration", style="dim")

    # v0.1: show helpful message — backend query support in v0.2
    console.print(table)
    console.print(
        "\n[yellow]Note:[/yellow] Direct backend querying is planned for v0.2. "
        "Use your OTel backend's UI to browse traces.\n"
        "Traces are being exported to your configured OTLP endpoint."
    )


# ---------------------------------------------------------------------------
# agentweave trace export <trace-id>
# ---------------------------------------------------------------------------


@trace_app.command("export")
def trace_export(
    trace_id: str = typer.Argument(help="The trace ID to export."),
    format: str = typer.Option(
        "prov-json",
        "--format",
        "-f",
        help="Export format: prov-json",
    ),
) -> None:
    """Export a trace as W3C PROV-JSON."""
    if format != "prov-json":
        console.print(f"[red]Unsupported format:[/red] {format}. Only 'prov-json' is supported in v0.1.")
        raise typer.Exit(code=1)

    # Emit a PROV-JSON skeleton — real data requires backend query (v0.2)
    prov_doc = {
        "prefix": {
            "prov": "http://www.w3.org/ns/prov#",
            "agentweave": "https://agentweave.dev/ns#",
        },
        "bundle": {
            f"agentweave:trace/{trace_id}": {
                "entity": {},
                "activity": {},
                "agent": {},
                "wasGeneratedBy": {},
                "used": {},
                "wasAssociatedWith": {},
            }
        },
    }
    console.print_json(json.dumps(prov_doc, indent=2))
    console.print(
        "\n[yellow]Note:[/yellow] Full trace data export requires backend query support (v0.2)."
    )


# ---------------------------------------------------------------------------
# agentweave version
# ---------------------------------------------------------------------------


@app.command("version")
def version() -> None:
    """Show the AgentWeave version."""
    console.print("[bold]agentweave[/bold] v0.1.0")


if __name__ == "__main__":
    app()
