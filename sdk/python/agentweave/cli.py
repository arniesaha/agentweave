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
proxy_app = typer.Typer(name="proxy", help="Anthropic API proxy for zero-config tracing.")
hooks_app = typer.Typer(name="hooks", help="Claude Code hooks integration.")
app.add_typer(trace_app)
app.add_typer(proxy_app)
app.add_typer(hooks_app)

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
# agentweave proxy start
# ---------------------------------------------------------------------------


@proxy_app.command("start")
def proxy_start(
    port: int = typer.Option(4000, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to."),
    endpoint: Optional[str] = typer.Option(
        None, "--endpoint", "-e",
        help="OTLP HTTP endpoint (e.g. http://localhost:4318). Overrides AGENTWEAVE_OTLP_ENDPOINT.",
    ),
    agent_id: Optional[str] = typer.Option(
        None, "--agent-id", help="Default agent ID tag for all traced calls."
    ),
    capture_prompts: bool = typer.Option(
        False, "--capture-prompts",
        help="Record first 512 chars of prompt and response in span attributes.",
    ),
    auth_token: Optional[str] = typer.Option(
        None, "--auth-token",
        help="Bearer token required on incoming requests. Recommended for NodePort/LAN exposure. "
             "Also reads from AGENTWEAVE_PROXY_TOKEN env var.",
    ),
) -> None:
    """Start the AgentWeave Anthropic API proxy.

    Point any Anthropic SDK client at this proxy by setting:

        ANTHROPIC_BASE_URL=http://localhost:<port>

    All requests are forwarded transparently; each LLM call gets an OTel span
    with token counts, model, stop reason, and latency.
    """
    import os

    try:
        from agentweave.proxy import run as proxy_run
    except ImportError:
        console.print(
            "[red]Proxy dependencies not installed.[/red]\n"
            "Run: [bold]pip install agentweave\\[proxy][/bold]"
        )
        raise typer.Exit(code=1)

    if endpoint:
        os.environ["AGENTWEAVE_OTLP_ENDPOINT"] = endpoint
    if capture_prompts:
        os.environ["AGENTWEAVE_CAPTURE_PROMPTS"] = "1"
    if auth_token:
        os.environ["AGENTWEAVE_PROXY_TOKEN"] = auth_token
    # Also propagate env var if already set (CLI flag takes precedence)
    effective_token = auth_token or os.getenv("AGENTWEAVE_PROXY_TOKEN")

    otel_endpoint = endpoint or os.getenv("AGENTWEAVE_OTLP_ENDPOINT", "http://localhost:4318")

    from agentweave.config import AgentWeaveConfig
    from agentweave import exporter as _exp
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({
        "service.name": "agentweave-proxy",
        "agentweave.agent.id": agent_id or "proxy",
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otel_endpoint}/v1/traces"))
    )
    _exp._provider = provider

    console.print(f"[bold green]AgentWeave Proxy[/bold green] starting on [bold]{host}:{port}[/bold]")
    console.print(f"  OTLP endpoint : [cyan]{otel_endpoint}[/cyan]")
    console.print(f"  Agent ID      : [cyan]{agent_id or '(from X-AgentWeave-Agent-Id header)'}[/cyan]")
    console.print(f"  Capture prompts: [cyan]{capture_prompts}[/cyan]")
    console.print(f"  Auth token    : [cyan]{'set ✓' if effective_token else 'NONE (open mode — localhost only!)'}[/cyan]")
    console.print()
    console.print(f"  Set in your agent: [bold]ANTHROPIC_BASE_URL=http://localhost:{port}[/bold]")
    console.print()

    proxy_run(host=host, port=port)


# ---------------------------------------------------------------------------
# agentweave hooks install
# ---------------------------------------------------------------------------


@hooks_app.command("install")
def hooks_install(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Show what would be changed without modifying settings.json.",
    ),
    settings_path: Optional[str] = typer.Option(
        None, "--settings-path",
        help="Path to Claude Code settings.json (default: ~/.claude/settings.json).",
    ),
) -> None:
    """Install AgentWeave hooks into Claude Code settings.

    Idempotently merges PostToolUse, SubagentStop, and Stop hooks into
    ~/.claude/settings.json. Existing hooks are preserved.
    """
    import os
    from pathlib import Path

    # Determine settings path
    if settings_path:
        settings_file = Path(settings_path)
    else:
        settings_file = Path.home() / ".claude" / "settings.json"

    # Load existing settings or create empty
    existing_settings: dict = {}
    if settings_file.exists():
        try:
            existing_settings = json.loads(settings_file.read_text())
        except json.JSONDecodeError:
            console.print(f"[red]Error:[/red] Invalid JSON in {settings_file}")
            raise typer.Exit(code=1)

    # Load our hook template
    package_dir = Path(__file__).parent
    template_file = package_dir / "hooks" / "claude-code" / "settings_template.json"
    if not template_file.exists():
        # Try from project root (for development installs)
        template_file = package_dir.parent.parent.parent / "agentweave" / "hooks" / "claude-code" / "settings_template.json"
    if not template_file.exists():
        console.print("[red]Error:[/red] Could not find hooks template file.")
        console.print(f"[dim]Searched: {package_dir / 'hooks' / 'claude-code' / 'settings_template.json'}[/dim]")
        raise typer.Exit(code=1)
    template_content = template_file.read_text()

    template_hooks = json.loads(template_content).get("hooks", {})

    # Merge hooks (preserve existing, add new)
    existing_hooks = existing_settings.get("hooks", {})
    merged_hooks = dict(existing_hooks)

    changes_made = []
    for hook_type, hook_config in template_hooks.items():
        if hook_type not in merged_hooks:
            merged_hooks[hook_type] = hook_config
            changes_made.append(f"Added {hook_type} hook")
        else:
            # Check if our hook is already present
            existing_commands = []
            for entry in merged_hooks[hook_type]:
                for h in entry.get("hooks", []):
                    if h.get("type") == "command":
                        existing_commands.append(h.get("command", ""))

            our_commands = []
            for entry in hook_config:
                for h in entry.get("hooks", []):
                    if h.get("type") == "command":
                        our_commands.append(h.get("command", ""))

            for cmd in our_commands:
                if cmd not in existing_commands:
                    merged_hooks[hook_type].extend(hook_config)
                    changes_made.append(f"Added AgentWeave to {hook_type} hook")
                    break

    if not changes_made:
        console.print("[green]AgentWeave hooks already installed.[/green]")
        return

    # Update settings
    existing_settings["hooks"] = merged_hooks

    if dry_run:
        console.print("[yellow]Dry run — would make these changes:[/yellow]")
        for change in changes_made:
            console.print(f"  - {change}")
        console.print(f"\n[dim]Settings file: {settings_file}[/dim]")
        console.print("\n[bold]Merged settings:[/bold]")
        console.print_json(json.dumps(existing_settings, indent=2))
    else:
        # Ensure directory exists
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(existing_settings, indent=2) + "\n")
        console.print("[green]AgentWeave hooks installed successfully![/green]")
        for change in changes_made:
            console.print(f"  - {change}")
        console.print(f"\n[dim]Settings file: {settings_file}[/dim]")
        console.print("\n[yellow]Next steps:[/yellow]")
        console.print("  1. Ensure the AgentWeave proxy is running:")
        console.print("     [bold]agentweave proxy start[/bold]")
        console.print("  2. Restart Claude Code to load the new hooks")


@hooks_app.command("uninstall")
def hooks_uninstall(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Show what would be changed without modifying settings.json.",
    ),
    settings_path: Optional[str] = typer.Option(
        None, "--settings-path",
        help="Path to Claude Code settings.json (default: ~/.claude/settings.json).",
    ),
) -> None:
    """Remove AgentWeave hooks from Claude Code settings."""
    from pathlib import Path

    # Determine settings path
    if settings_path:
        settings_file = Path(settings_path)
    else:
        settings_file = Path.home() / ".claude" / "settings.json"

    if not settings_file.exists():
        console.print("[yellow]No settings file found — nothing to uninstall.[/yellow]")
        return

    try:
        existing_settings = json.loads(settings_file.read_text())
    except json.JSONDecodeError:
        console.print(f"[red]Error:[/red] Invalid JSON in {settings_file}")
        raise typer.Exit(code=1)

    existing_hooks = existing_settings.get("hooks", {})
    if not existing_hooks:
        console.print("[yellow]No hooks configured — nothing to uninstall.[/yellow]")
        return

    changes_made = []
    cleaned_hooks = {}

    for hook_type, hook_entries in existing_hooks.items():
        cleaned_entries = []
        for entry in hook_entries:
            cleaned_entry_hooks = []
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if not cmd.startswith("agentweave-hook-"):
                    cleaned_entry_hooks.append(h)
                else:
                    changes_made.append(f"Removed {cmd} from {hook_type}")
            if cleaned_entry_hooks:
                entry["hooks"] = cleaned_entry_hooks
                cleaned_entries.append(entry)
        if cleaned_entries:
            cleaned_hooks[hook_type] = cleaned_entries

    if not changes_made:
        console.print("[green]No AgentWeave hooks found — nothing to uninstall.[/green]")
        return

    existing_settings["hooks"] = cleaned_hooks if cleaned_hooks else {}

    if dry_run:
        console.print("[yellow]Dry run — would make these changes:[/yellow]")
        for change in changes_made:
            console.print(f"  - {change}")
    else:
        settings_file.write_text(json.dumps(existing_settings, indent=2) + "\n")
        console.print("[green]AgentWeave hooks uninstalled successfully![/green]")
        for change in changes_made:
            console.print(f"  - {change}")


# ---------------------------------------------------------------------------
# agentweave version
# ---------------------------------------------------------------------------


@app.command("version")
def version() -> None:
    """Show the AgentWeave version."""
    console.print("[bold]agentweave[/bold] v0.1.0")


if __name__ == "__main__":
    app()
