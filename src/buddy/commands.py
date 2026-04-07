"""/buddy command handler — AI companion pet.

Subcommands:
  /buddy          — hatch (first time) or show companion card
  /buddy help     — show all commands and gameplay guide
  /buddy pet      — pet your companion (heart animation)
  /buddy stats    — show detailed stats
  /buddy mood     — show current mood
  /buddy new      — hatch a new random companion
  /buddy list     — view all companions (仓库)
  /buddy select N  — switch active companion to #N
  /buddy mute     — mute companion reactions
  /buddy unmute   — unmute companion reactions
"""
from __future__ import annotations

import time
import uuid

from rich.console import Console
from rich.live import Live
from rich.text import Text
from core.llm import LLMClient

from .companion import companion_user_id, get_companion, get_all_companions, roll, roll_with_seed
from .render import render_companion_card, render_hatch_animation, render_compact_status, render_companion_list
from .storage import (
    load_active_index,
    load_companion_muted,
    save_active_index,
    save_companion_muted,
    save_new_companion,
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
    roll.cache_clear()  # ensure fresh roll (seed may have changed)
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


def _hatch_new(client: LLMClient, console: Console, model: str) -> None:
    """Hatch an additional random companion with a unique seed."""
    seed = f'buddy-new-{uuid.uuid4()}'
    r = roll_with_seed(seed)
    bones = r.bones

    console.print(f'\n[dim]Hatching a new companion...[/dim]')

    try:
        soul = _generate_soul(bones, client, model)
    except Exception as e:
        console.print(f'[red]Failed to generate companion soul: {e}[/red]')
        soul = CompanionSoul(
            name='Buddy',
            personality=f'A quiet {bones.species} who prefers actions over words.',
        )

    save_new_companion(soul, seed)
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

    # Pet boosts mood
    try:
        from .mood import apply_events, apply_decay
        from .storage import load_active_mood, save_active_mood
        now_ms = int(time.time() * 1000)
        mood = load_active_mood()
        mood = apply_decay(mood, now_ms)
        mood = apply_events(mood, ['pet'])
        save_active_mood(mood)
    except Exception:
        pass


def _render_mood(companion, console: Console) -> None:
    """Show mood detail for a companion."""
    from .types import RARITY_COLORS, MOOD_DIMENSIONS, MOOD_NEUTRAL
    from .render import _stat_bar

    color = RARITY_COLORS.get(companion.rarity, 'dim')
    mood = companion.mood
    console.print(f'\n[{color}]{companion.name}\'s mood:[/{color}]')
    for dim in MOOD_DIMENSIONS:
        val = getattr(mood, dim)
        bar = _stat_bar(val)
        if abs(val - MOOD_NEUTRAL) < 10:
            label = 'neutral'
        elif val > MOOD_NEUTRAL:
            label = 'high'
        else:
            label = 'low'
        console.print(f'  {dim.capitalize():<10} {bar} {val:>3} ({label})')
    console.print(f'\n[dim]Dominant mood: {mood.dominant().lower()}[/dim]')


def _render_help(console: Console) -> None:
    """Show all buddy commands and gameplay guide."""
    from rich.panel import Panel
    from rich.text import Text

    help_text = (
        "[bold]Commands[/bold]\n"
        "\n"
        "  [cyan]/buddy[/cyan]              Hatch your first companion, or show its card\n"
        "  [cyan]/buddy help[/cyan]          Show this help\n"
        "  [cyan]/buddy pet[/cyan]           Pet your companion (heart animation, boosts happy)\n"
        "  [cyan]/buddy stats[/cyan]         Show companion card with stats and mood\n"
        "  [cyan]/buddy mood[/cyan]          Show current mood details\n"
        "  [cyan]/buddy new[/cyan]           Hatch an additional random companion\n"
        "  [cyan]/buddy list[/cyan]          View all companions in your collection\n"
        "  [cyan]/buddy select N[/cyan]      Switch active companion to #N\n"
        "  [cyan]/buddy mute[/cyan]          Mute companion speech bubbles\n"
        "  [cyan]/buddy unmute[/cyan]        Unmute companion speech bubbles\n"
        "  [cyan]/buddy ia[/cyan]            Start the Poke Game adventure\n"
        "\n"
        "[bold]Gameplay Guide[/bold]\n"
        "\n"
        "  [yellow]Hatching[/yellow]  Your first companion is determined by your username.\n"
        "            Use [cyan]/buddy new[/cyan] to hatch more with random seeds.\n"
        "            18 species, 5 rarities (Common to Legendary), 1% shiny chance.\n"
        "\n"
        "  [yellow]Stats[/yellow]    Each companion has 5 permanent stats (0-100):\n"
        "            DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK.\n"
        "            These shape how your companion talks and reacts.\n"
        "\n"
        "  [yellow]Mood[/yellow]     6 dynamic mood dimensions that change over time:\n"
        "            Happy, Bored, Excited, Tired, Grumpy, Curious.\n"
        "            Mood is affected by your coding activity:\n"
        "            - Task success / bug fixes  ->  happy, excited\n"
        "            - Errors / failures         ->  grumpy, tired\n"
        "            - Reading / exploring code  ->  curious\n"
        "            - Petting ([cyan]/buddy pet[/cyan])     ->  happy, excited\n"
        "            - Long idle time            ->  bored\n"
        "            Mood gradually decays back to neutral over time.\n"
        "\n"
        "  [yellow]Talking[/yellow]  Your companion reacts after each Claude response.\n"
        "            Address it by name to chat directly (20-turn memory).\n"
        "            Its tone adapts to both stats and current mood.\n"
        "\n"
        "  [yellow]Pikachu[/yellow]  Set CC_MINI_BUDDY_SEED=pikachu-3361 before hatching\n"
        "            to unlock the secret Legendary Pikachu species."
    )

    panel = Panel(
        help_text,
        title="[bold]Buddy — AI Companion Pet[/bold]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


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

    elif subcmd == 'help':
        _render_help(console)

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

    elif subcmd == 'mood':
        companion = get_companion()
        if not companion:
            console.print('[dim]No companion yet. Type /buddy to hatch one![/dim]')
        else:
            _render_mood(companion, console)

    elif subcmd == 'ia':
        from .poke_game import start_game
        start_game(client, console, model)

    elif subcmd == 'new':
        _hatch_new(client, console, model)

    elif subcmd == 'list':
        companions = get_all_companions()
        active = load_active_index()
        render_companion_list(companions, active, console)

    elif subcmd.startswith('select'):
        parts = subcmd.split()
        if len(parts) != 2 or not parts[1].isdigit():
            console.print('[dim]Usage: /buddy select <number> (e.g. /buddy select 2)[/dim]')
        else:
            n = int(parts[1])
            companions = get_all_companions()
            if n < 1 or n > len(companions):
                console.print(f'[dim]Invalid number. You have {len(companions)} companion(s). Use 1-{len(companions)}.[/dim]')
            else:
                idx = n - 1
                save_active_index(idx)
                comp = companions[idx]
                console.print(f'[bold]Switched to #{n}: {comp.name} the {comp.species}[/bold]')
                render_companion_card(comp, console)

    else:
        console.print(
            '[dim]Usage: /buddy [help|pet|stats|mood|new|list|select N|mute|unmute|ia][/dim]'
          )
