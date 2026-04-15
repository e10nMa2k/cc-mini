"""Run a single query turn with TUI feedback (spinner, markdown streaming)."""
from __future__ import annotations

import sys

from rich.console import Console

from core.engine import AbortedError, Engine
from tui.keylistener import EscListener
from core.permissions import PermissionChecker
from tui.rendering import (
    StreamingMarkdown,
    SpinnerManager,
    tool_preview,
    collapsed_tool_summary,
)

console = Console()


def run_query(engine: Engine, user_input: str | list, print_mode: bool,
              permissions: PermissionChecker | None = None,
              quiet: bool = False) -> None:
    """Run a single turn. Ctrl+C or Esc cancels the active turn.

    If *quiet* is True, suppress all terminal output (spinner, tool calls, text).
    Used for background tasks like auto-dream.
    """
    listener = EscListener(on_cancel=engine.abort)
    if permissions:
        permissions.set_esc_listener(listener)

    spinner = SpinnerManager(console)
    md_stream = StreamingMarkdown(console)
    first_text = True
    streaming = False
    # Track pending tool calls for spinner display.
    # key: unique tool key, value: (tool_name, display_line)
    pending_tools: dict[str, tuple[str, str]] = {}

    try:
        with listener:
            if not quiet:
                spinner.start("Thinking…")

            for event in engine.submit(user_input):
                if not quiet and streaming and listener.pressed:
                    md_stream.flush()
                    spinner.stop()
                    engine.cancel_turn()
                    console.print("\n[dim yellow]⏹ Turn cancelled (Esc)[/dim yellow]")
                    return

                if event[0] == "text":
                    if quiet:
                        continue
                    if first_text:
                        spinner.stop()
                        streaming = True
                        first_text = False
                    if print_mode:
                        print(event[1], end="", flush=True)
                    else:
                        md_stream.feed(event[1])

                elif event[0] == "waiting":
                    if not quiet:
                        md_stream.flush()
                    streaming = False
                    if not quiet:
                        listener.resume()
                        spinner.start("Preparing tool call…")

                elif event[0] == "tool_call":
                    if not quiet:
                        spinner.stop()
                        streaming = False
                        listener.pause()
                        _, tool_name, tool_input, activity = event
                        preview = tool_preview(tool_name, tool_input)
                        key = f"{tool_name}({preview})"
                        if tool_name == "Agent":
                            desc = tool_input.get("description", "worker")[:60]
                            agent_type = tool_input.get("subagent_type", "")
                            _SWARM_TYPES = ("general-purpose", "researcher", "implementer", "verifier")
                            if agent_type in _SWARM_TYPES:
                                # Swarm agent: ◈ icon with type badge
                                pending_tools[key] = (tool_name, f"◈ [{agent_type}] {desc}")
                            else:
                                pending_tools[key] = (tool_name, f"◎ {desc}")
                        else:
                            pending_tools[key] = (tool_name, f"↳ {key}")

                elif event[0] == "tool_executing":
                    if not quiet:
                        _, tool_name, tool_input, activity = event
                        n = len(pending_tools)
                        agent_count = sum(1 for tn, _ in pending_tools.values() if tn == "Agent")
                        _SWARM_TYPES = ("general-purpose", "researcher", "implementer", "verifier")
                        agent_type = tool_input.get("subagent_type", "") if tool_name == "Agent" else ""
                        is_swarm_agent = agent_type in _SWARM_TYPES
                        if tool_name == "AskUserQuestion":
                            # Interactive prompt — stop spinner so it renders on a clean line
                            spinner.stop()
                            _, line = next(iter(pending_tools.values()), ("", f"↳ {tool_name}"))
                            console.print(f"[dim]{line}[/dim]", highlight=False)
                        elif tool_name == "Agent" and n == 1:
                            desc = tool_input.get("description", "worker")[:50]
                            if is_swarm_agent:
                                spinner.start(f"◈ Spawning {agent_type}: {desc}…")
                            else:
                                spinner.start(f"◎ Spawning sub-agent: {desc}…")
                        elif agent_count > 1 and agent_count == n:
                            # All pending are Agent calls — check if they are swarm-type
                            swarm_count = sum(
                                1 for _, (tn, line) in pending_tools.items()
                                if tn == "Agent" and line.startswith("◈")
                            )
                            if swarm_count == agent_count:
                                spinner.start(f"◈ Launching swarm: {agent_count} agents…")
                            else:
                                spinner.start(f"◎ Launching swarm: {agent_count} sub-agents…")
                        elif n > 1:
                            names = [tn for tn, _ in pending_tools.values()]
                            spinner.start(collapsed_tool_summary(names))
                        else:
                            _, line = next(iter(pending_tools.values()), ("", f"↳ {tool_name}"))
                            activity_text = activity or f"Running {tool_name}…"
                            spinner.start(f"{line} … {activity_text}")

                elif event[0] == "tool_result":
                    if not quiet:
                        spinner.stop()
                        _, tool_name, tool_input, result = event
                        preview = tool_preview(tool_name, tool_input)
                        key = f"{tool_name}({preview})"
                        tname, line = pending_tools.pop(key, (tool_name, f"↳ {key}"))
                        if tname == "Agent" and not result.is_error:
                            desc = tool_input.get("description", "worker")[:60]
                            agent_type = tool_input.get("subagent_type", "")
                            _SWARM_TYPES = ("general-purpose", "researcher", "implementer", "verifier")
                            if agent_type in _SWARM_TYPES:
                                # Swarm agent: magenta ◈ with type badge
                                type_badge = f"  [dim]{agent_type}[/dim]" if agent_type else ""
                                console.print(
                                    f"[magenta]◈[/magenta] [dim]Agent launched[/dim]{type_badge}"
                                    f"  [bold magenta]{desc}[/bold magenta]"
                                    f"  [dim]→ background[/dim]",
                                    highlight=False,
                                )
                            else:
                                console.print(
                                    f"[cyan]◎[/cyan] [dim]Sub-agent started[/dim]  "
                                    f"[bold cyan]{desc}[/bold cyan]  [dim]→ running in background[/dim]",
                                    highlight=False,
                                )
                        elif result.is_error:
                            console.print(f"[dim]{line}[/dim] [red]✗[/red]", highlight=False)
                            console.print(f"  [red]{result.content[:200]}[/red]")
                        else:
                            console.print(f"[dim]{line}[/dim] [green]✓[/green]", highlight=False)

                        remaining_agents = sum(1 for tn, _ in pending_tools.values() if tn == "Agent")
                        if pending_tools:
                            if remaining_agents > 0 and remaining_agents == len(pending_tools):
                                swarm_remaining = sum(
                                    1 for _, (tn, l) in pending_tools.items()
                                    if tn == "Agent" and l.startswith("◈")
                                )
                                if swarm_remaining == remaining_agents:
                                    spinner.start(f"◈ {remaining_agents} more agent{'s' if remaining_agents > 1 else ''} starting…")
                                else:
                                    spinner.start(f"◎ {remaining_agents} more sub-agent{'s' if remaining_agents > 1 else ''} starting…")
                            else:
                                names = [tn for tn, _ in pending_tools.values()]
                                spinner.start(collapsed_tool_summary(names))
                        else:
                            streaming = False
                            listener.resume()
                            spinner.start("Thinking…")
                            first_text = True

                elif event[0] == "error":
                    if not quiet:
                        md_stream.flush()
                        spinner.stop()
                        console.print(f"\n[bold red]{event[1]}[/bold red]")

            md_stream.flush()
            spinner.stop()
    except (AbortedError, KeyboardInterrupt):
        md_stream.flush()
        spinner.stop()
        if not isinstance(sys.exc_info()[1], AbortedError):
            engine.cancel_turn()
        if not quiet:
            console.print("\n[dim yellow]⏹ Turn cancelled[/dim yellow]")
        return
    finally:
        md_stream.flush()
        spinner.stop()
        if permissions:
            permissions.set_esc_listener(None)

    if not print_mode:
        console.print()
