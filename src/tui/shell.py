"""Shell execution and sandbox commands for the TUI."""
from __future__ import annotations

import subprocess

from rich.console import Console

from features.sandbox.manager import SandboxManager


def run_shell(cmd: str, console: Console) -> None:
    """Execute a shell command and print output."""
    console.print(f"[dim]$ {cmd}[/dim]")
    try:
        result = subprocess.run(
            cmd, shell=True, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if result.stdout:
            console.print(result.stdout, end="", markup=False)
        if result.returncode != 0:
            console.print(f"[red][exit {result.returncode}][/red]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")


def handle_sandbox_command(
    user_input: str, mgr: SandboxManager, con: Console
) -> None:
    """Handle /sandbox REPL command.

    Corresponds to commands/sandbox-toggle/sandbox-toggle.tsx.

    Sub-commands:
    - /sandbox           -- interactive setup
    - /sandbox status    -- show current status
    - /sandbox exclude <pattern> -- add excluded command
    - /sandbox mode <auto-allow|regular|disabled> -- set mode
    """
    parts = user_input.strip().split(maxsplit=2)
    subcmd = parts[1] if len(parts) > 1 else ""

    if subcmd == "status" or subcmd == "":
        show_sandbox_status(mgr, con)
    elif subcmd == "exclude" and len(parts) > 2:
        pattern = parts[2].strip("\"'")
        msg = mgr.add_excluded_command(pattern)
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    elif subcmd == "mode" and len(parts) > 2:
        msg = mgr.set_mode(parts[2])
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    else:
        interactive_sandbox_setup(mgr, con)


def show_sandbox_status(mgr: SandboxManager, con: Console) -> None:
    """Display sandbox status. Corresponds to SandboxConfigTab + SandboxDependenciesTab."""
    dep = mgr.check_dependencies()
    mode = (
        "auto-allow"
        if mgr.is_auto_allow()
        else ("regular" if mgr.config.enabled else "disabled")
    )
    con.print("[bold]Sandbox Status[/bold]")
    con.print(f"  Mode: [cyan]{mode}[/cyan]")
    con.print(f"  Enabled: {'yes' if mgr.is_enabled() else 'no'}")
    con.print(
        f"  Network isolation: {'yes' if mgr.config.unshare_net else 'no'}"
    )
    if dep.errors:
        con.print("[bold red]Dependency errors:[/bold red]")
        for e in dep.errors:
            con.print(f"  [red]{e}[/red]")
    if dep.warnings:
        for w in dep.warnings:
            con.print(f"  [yellow]{w}[/yellow]")
    if mgr.config.excluded_commands:
        con.print("[bold]Excluded commands:[/bold]")
        for cmd in mgr.config.excluded_commands:
            con.print(f"  - {cmd}")


def interactive_sandbox_setup(mgr: SandboxManager, con: Console) -> None:
    """Interactive three-way mode selection. Corresponds to SandboxModeTab Select."""
    dep = mgr.check_dependencies()
    if dep.errors:
        con.print("[bold red]Cannot enable sandbox:[/bold red]")
        for e in dep.errors:
            con.print(f"  [red]{e}[/red]")
        return

    con.print("[bold]Configure sandbox mode:[/bold]")
    con.print("  [1] auto-allow -- bash commands auto-approved in sandbox")
    con.print("  [2] regular    -- bash commands still need confirmation")
    con.print("  [3] disabled   -- no sandbox")
    choice = input("  Select [1/2/3]: ").strip()
    mode_map = {"1": "auto-allow", "2": "regular", "3": "disabled"}
    mode = mode_map.get(choice)
    if mode:
        msg = mgr.set_mode(mode)
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    else:
        con.print("[dim]Cancelled[/dim]")
