"""/buddy command handler — AI companion pet.

Subcommands:
  /buddy          — hatch (first time) or show companion card
  /buddy pet      — pet your companion (heart animation)
  /buddy stats    — show detailed stats
  /buddy mute     — mute companion reactions
  /buddy unmute   — unmute companion reactions
"""
from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.text import Text
from ..llm import LLMClient

from .companion import companion_user_id, get_companion, roll
from .render import render_companion_card, render_hatch_animation, render_compact_status
from .storage import (
    load_companion_muted,
    save_companion_muted,
    save_stored_companion,
)
from .types import CompanionBones, CompanionSoul

def _generate_soul(
    bones: CompanionBones,
    client: LLMClient,
    model: str,
) -> CompanionSoul:
    """Call the configured LLM to generate a name and personality."""
    stats_desc = ', '.join(f'{k}={v}' for k, v in bones.stats.items())
    shiny_note = ' This is an extremely rare SHINY companion!' if bones.shiny else ''

    prompt = (
        f'You are naming a new companion pet. It is a {bones.rarity} {bones.species} '
        f'with these stats: {stats_desc}. Its eye style is {bones.eye} and '
        f'it wears a {bones.hat} hat.{shiny_note}\n\n'
        f'Generate:\n'
        f'1. A short, creative name (1-2 words, no quotes)\n'
        f'2. A one-sentence personality description (under 80 chars)\n\n'
        f'Format your response EXACTLY as:\n'
        f'NAME: <name>\n'
        f'PERSONALITY: <personality>'
    )

    response = client.create_message(
        model=model,
        max_tokens=100,
        messages=[{'role': 'user', 'content': prompt}],
    )

    text = ""
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
    text = text.strip()
    name = 'Buddy'
    personality = f'A mysterious {bones.species}.'

    for line in text.split('\n'):
        line = line.strip()
        if line.upper().startswith('NAME:'):
            name = line.split(':', 1)[1].strip()
        elif line.upper().startswith('PERSONALITY:'):
            personality = line.split(':', 1)[1].strip()

    return CompanionSoul(name=name, personality=personality)


def _hatch(client: LLMClient, console: Console, model: str) -> None:
    """Hatch a new companion: generate bones, call API for soul, save, animate."""
    user_id = companion_user_id()
    r = roll(user_id)
    bones = r.bones

    console.print(f'\n[dim]Hatching your companion...[/dim]')

    try:
        soul = _generate_soul(bones, client, model)
    except Exception as e:
        console.print(f'[red]Failed to generate companion soul: {e}[/red]')
        # Fallback soul
        soul = CompanionSoul(
            name='Buddy',
            personality=f'A quiet {bones.species} who prefers actions over words.',
        )

    save_stored_companion(soul)
    render_hatch_animation(bones, soul, console)

    companion = get_companion()
    if companion:
        render_companion_card(companion, console)


def _pet_animation(console: Console) -> None:
    """Show a heart animation when petting the companion.

    Matches CompanionSprite.tsx PET_HEARTS: 5-frame heart float
    animation over 2.5 seconds with fading dots at the end.
    """
    companion = get_companion()
    if not companion:
        return

    from .sprites import render_sprite
    from .types import RARITY_COLORS

    color = RARITY_COLORS.get(companion.rarity, 'dim')
    bones = CompanionBones(
        rarity=companion.rarity, species=companion.species,
        eye=companion.eye, hat=companion.hat,
        shiny=companion.shiny, stats=companion.stats,
    )

    # Match CompanionSprite.tsx PET_HEARTS — hearts float up and fade to dots
    H = '\u2764'
    pet_hearts = [
        f'   {H}    {H}   ',
        f'  {H}  {H}   {H}  ',
        f' {H}   {H}  {H}   ',
        f'{H}  {H}      {H} ',
        '\u00b7    \u00b7   \u00b7  ',
    ]

    # Excited mode: cycle through all sprite frames fast
    frame_count = len([f for f in [0, 1, 2]])

    with Live(console=console, refresh_per_second=4, transient=True) as live:
        for i, heart_line in enumerate(pet_hearts):
            sprite_lines = render_sprite(bones, frame=i % 3)
            # Build rich Text with proper styling (not markup strings)
            frame_text = Text()
            frame_text.append(f'  {heart_line}\n', style='bold red')
            for sl in sprite_lines:
                frame_text.append(f'  {sl}\n', style=color)
            live.update(frame_text)
            time.sleep(0.5)

    console.print(f'[dim]{companion.name} wiggles happily.[/dim]')


def handle_buddy_command(
    args: str,
    client: LLMClient,
    console: Console,
    model: str,
) -> None:
    """Handle /buddy commands."""
    subcmd = args.strip().lower()

    if subcmd == '':
        # Hatch or show card
        companion = get_companion()
        if companion:
            render_companion_card(companion, console)
        else:
            _hatch(client, console, model)

    elif subcmd == 'pet':
        companion = get_companion()
        if not companion:
            console.print('[dim]No companion yet. Type /buddy to hatch one![/dim]')
        else:
            _pet_animation(console)

    elif subcmd == 'stats':
        companion = get_companion()
        if not companion:
            console.print('[dim]No companion yet. Type /buddy to hatch one![/dim]')
        else:
            render_companion_card(companion, console)

    elif subcmd == 'mute':
        save_companion_muted(True)
        console.print('[dim]Companion reactions muted.[/dim]')

    elif subcmd == 'unmute':
        save_companion_muted(False)
        console.print('[dim]Companion reactions unmuted.[/dim]')

    elif subcmd == 'ia':
        from .poke_game import start_game
        start_game(client, console, model)

    else:
        console.print(
            '[dim]Usage: /buddy [pet|stats|mute|unmute|ia][/dim]'
        )
