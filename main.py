#!/usr/bin/env python3
# ============================================================
# main.py — Solar Power Plant AI — Terminal CLI
#
# Setup:
#   1. Install packages:
#        pip install -r requirements.txt
#   2. Set your OpenRouter key:
#        Windows PowerShell:  $env:OPENROUTER_API_KEY = "sk-or-..."
#        Linux / macOS:       export OPENROUTER_API_KEY="sk-or-..."
#   3. Run:
#        python main.py
# ============================================================

import os
import sys
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich import box

load_dotenv()   # load .env if present

console = Console()

# ─── Startup banner ───────────────────────────────────────────
BANNER = """\
[bold cyan]╔══════════════════════════════════════════════════════╗[/bold cyan]
[bold cyan]║[/bold cyan]   [bold yellow]☀  Solar Power Plant AI Analytics System  ☀[/bold yellow]     [bold cyan]║[/bold cyan]
[bold cyan]║[/bold cyan]   [dim]LangGraph · Claude 3.5 Sonnet · PostgreSQL[/dim]        [bold cyan]║[/bold cyan]
[bold cyan]╚══════════════════════════════════════════════════════╝[/bold cyan]

[bold white]Ask anything about your solar plants![/bold white]
[dim]Examples:
  • What was the total energy yield of Solar Alpha last week?
  • Are there any inverter faults or anomalies in the system?
  • Predict the power output for Plant 3 tomorrow.
  • Which plant had the highest yield on 2026-09-21?[/dim]

Type [bold red]exit[/bold red] or [bold red]quit[/bold red] to stop.\n"""

NODE_LABELS = {
    "supervisor":       "🔀 Routing query...",
    "sql_orchestrator": "🗄️  Generating & executing SQL...",
    "diagnostic_agent": "🔬 Running fault diagnostics...",
    "predictive_agent": "📈 Training XGBoost forecast...",
    "synthesis_agent":  "✍️  Synthesising response...",
}


def _check_api_key():
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key or key.startswith("YOUR_"):
        console.print(Panel(
            "[bold red]OPENROUTER_API_KEY is not set![/bold red]\n\n"
            "Run:\n"
            "  [cyan]$env:OPENROUTER_API_KEY = 'sk-or-...'[/cyan]  (PowerShell)\n"
            "  [cyan]export OPENROUTER_API_KEY='sk-or-...'[/cyan]   (bash/zsh)\n\n"
            "Or create a [bold].env[/bold] file with:\n"
            "  [cyan]OPENROUTER_API_KEY=sk-or-...[/cyan]",
            title="⚠  Missing API Key",
            border_style="red",
        ))
        sys.exit(1)


def _stream_graph(app, user_query: str):
    """Stream graph execution and print live trace + final response."""

    final_response = ""
    active_node    = None

    console.print()

    for chunk in app.stream(
        {"user_query": user_query},
        stream_mode="updates",
    ):
        for node_name, node_output in chunk.items():
            # Print node trace
            if node_name != active_node:
                active_node = node_name
                label = NODE_LABELS.get(node_name, f"▶ {node_name}")
                console.print(f"  [dim]{label}[/dim]")

            # Capture SQL for trace
            if node_name == "sql_orchestrator" and node_output.get("sql_query"):
                console.print(
                    f"  [dim]  SQL → {node_output['sql_query'][:120]}{'...' if len(node_output.get('sql_query','')) > 120 else ''}[/dim]"
                )

            # Capture routing decision
            if node_name == "supervisor" and node_output.get("next_node"):
                console.print(
                    f"  [dim]  Routing to → [bold]{node_output['next_node']}[/bold][/dim]"
                )

            # Grab the final response
            if node_output.get("final_response"):
                final_response = node_output["final_response"]

    console.print()

    if final_response:
        console.print(Panel(
            Markdown(final_response),
            title="[bold green]☀ Solar AI Response[/bold green]",
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
        ))
    else:
        console.print("[red]No response was generated. Check your API key and database connection.[/red]")

    console.print()


def main():
    _check_api_key()

    # Lazy import (so missing key error shows before heavy imports)
    from graph import build_graph

    console.print(BANNER)

    with console.status("[cyan]Compiling agent graph...[/cyan]", spinner="dots"):
        app = build_graph()

    console.print("[bold green]✓ Agent graph ready.[/bold green]\n")

    while True:
        try:
            user_input = console.input("[bold cyan]Solar-AI>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Goodbye! ☀[/yellow]")
            break

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit", "q"}:
            console.print("[yellow]Goodbye! ☀[/yellow]")
            break

        try:
            _stream_graph(app, user_input)
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}\n")


if __name__ == "__main__":
    main()
