"""Rich terminal rendering for Idle Adventure.

Persistent HUD + scrollable message area + animations.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import time

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from .types import (
    Badge, GameSession, Item, Skill, NPC,
    BADGE_COLORS, GAME_STAT_NAMES,
)
from .badges import ALL_BADGES, badge_progress
from .world import LOCATIONS, REGIONS, REGION_ORDER


# ---------------------------------------------------------------------------
# Sprite states — determines which frame and expression
# ---------------------------------------------------------------------------

SPRITE_IDLE = "idle"
SPRITE_EXPLORE = "explore"
SPRITE_BATTLE = "battle"
SPRITE_REST = "rest"
SPRITE_TALK = "talk"
SPRITE_HURT = "hurt"

# Tick counter for idle animation (incremented each redraw)
_idle_tick = 0


def _get_sprite_lines(session: GameSession, state: str = SPRITE_IDLE) -> list[str]:
    """Get buddy sprite lines with state-appropriate frame."""
    global _idle_tick
    _idle_tick += 1

    try:
        from ..sprites import render_sprite
        from ..types import CompanionBones
        bones = CompanionBones(
            rarity="common",
            species=session.companion_species,
            eye=session.companion_eye,
            hat=session.companion_hat,
            shiny=False,
        )

        if state == SPRITE_IDLE:
            # Cycle through frames slowly
            frame = (_idle_tick // 2) % 3
        elif state == SPRITE_EXPLORE:
            frame = (_idle_tick % 3)  # fast cycling
        elif state == SPRITE_BATTLE:
            frame = (_idle_tick % 2)  # alternating attack frames
        elif state == SPRITE_REST:
            frame = 0  # static, eyes closed handled below
        elif state == SPRITE_TALK:
            frame = 1  # engaged frame
        else:
            frame = 0

        lines = render_sprite(bones, frame=frame)

        # Modify sprite based on state
        if state == SPRITE_REST:
            # Replace eyes with - for sleeping
            lines = [line.replace(session.companion_eye, "-") for line in lines]
        elif state == SPRITE_BATTLE:
            # Add battle effect
            if _idle_tick % 2 == 0:
                lines[-1] = lines[-1].rstrip() + "  *"
            else:
                lines[-1] = lines[-1].rstrip() + " **"
        elif state == SPRITE_HURT:
            lines = [line.replace(session.companion_eye, "x") for line in lines]

        return lines
    except Exception:
        return [
            "  .---.",
            f"  |{session.companion_eye} {session.companion_eye}|",
            "  | _ |",
            "  '---'",
        ]


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------

def _stat_bar(value: int, max_val: int, width: int = 12) -> str:
    filled = min(width, max(0, value * width // max(max_val, 1)))
    return "\u2588" * filled + "\u2591" * (width - filled)


def render_hud(session: GameSession, console: Console,
               sprite_state: str = SPRITE_IDLE) -> None:
    """Render the persistent HUD panel."""
    sprite_lines = _get_sprite_lines(session, sprite_state)

    hp = session.stats.get("HP", 0)
    hp_color = "red" if hp < 30 else ("yellow" if hp < 60 else "green")
    stats_text = Text()
    stats_text.append(f" HP  {_stat_bar(hp, 200)} {hp}\n", style=hp_color)
    stats_text.append(f" ATK {_stat_bar(session.stats.get('ATK', 0), 60)} {session.stats.get('ATK', 0)}\n", style="cyan")
    stats_text.append(f" DEF {_stat_bar(session.stats.get('DEF', 0), 60)} {session.stats.get('DEF', 0)}\n", style="cyan")
    stats_text.append(f" SPD {_stat_bar(session.stats.get('SPD', 0), 60)} {session.stats.get('SPD', 0)}\n", style="cyan")
    stats_text.append(f" LCK {_stat_bar(session.stats.get('LCK', 0), 60)} {session.stats.get('LCK', 0)}\n", style="cyan")

    loc_name = session.location.name if session.location else "???"
    region = session.location.region if session.location else "???"
    owned, total = badge_progress(session)

    from .world import get_location_npcs
    local_npcs = get_location_npcs(loc_name)
    npc_str = ", ".join(n.name for n in local_npcs) if local_npcs else "-"

    info_text = Text()
    info_text.append(f" \u2302 {loc_name}\n", style="bold green")
    info_text.append(f"   {region}\n", style="dim")
    info_text.append(f" \u2606 徽章 {owned}/{total}\n")
    info_text.append(f" \u2726 券 {session.tickets}\n", style="yellow")
    info_text.append(f" \u263a NPC {npc_str}\n", style="dim")

    sprite_text = Text()
    for line in sprite_lines:
        sprite_text.append(f"  {line}\n")

    cols = Columns([sprite_text, stats_text, info_text], padding=(0, 2))
    title = f" {session.companion_name} the {session.companion_species} "
    mood_icon = "\u2665" if session.mood > 60 else ("\u223c" if session.mood > 30 else "\u2639")
    subtitle = f" 心情:{mood_icon}{session.mood} | 回合:{session.turn_count} "

    console.print(Panel(
        cols,
        title=f"[bold]{title}[/bold]",
        subtitle=f"[dim]{subtitle}[/dim]",
        border_style="cyan",
        box=box.ROUNDED,
    ))


# ---------------------------------------------------------------------------
# Message box — scrollable area below HUD
# ---------------------------------------------------------------------------

class MessageBuffer:
    """Circular buffer of game messages with timestamps and action separators."""

    def __init__(self, max_size: int = 200):
        self._messages: list[str] = []
        self._max_size = max_size

    def add(self, text: str) -> None:
        """Add a plain message line (no separator)."""
        ts = time.strftime("%H:%M:%S")
        self._messages.append(f"[dim]{ts}[/dim] {text}")
        if len(self._messages) > self._max_size:
            self._messages = self._messages[-self._max_size:]

    def add_action(self, action: str, detail: str = "") -> None:
        """Add an action separator line with timestamp."""
        ts = time.strftime("%H:%M:%S")
        label = f"{action} {detail}".strip()
        # Build separator: ─── 探索 林间小径 ───────────
        sep = f"\u2500\u2500\u2500 {label} "
        sep += "\u2500" * max(1, 40 - len(sep))
        self._messages.append(f"\n[bold cyan]{ts}[/bold cyan] [dim]{sep}[/dim]")
        if len(self._messages) > self._max_size:
            self._messages = self._messages[-self._max_size:]

    def get_recent(self, n: int = 15) -> list[str]:
        return self._messages[-n:]

    def render(self, console: Console, max_lines: int = 0) -> None:
        """Print recent messages as a panel."""
        term_h = shutil.get_terminal_size((80, 24)).lines
        available = max_lines or max(5, term_h - 14)
        recent = self.get_recent(available)
        if not recent:
            console.print(Panel("[dim]等待冒险开始...[/dim]",
                                border_style="dim", box=box.ROUNDED))
            return
        content = "\n".join(recent)
        console.print(Panel(content, border_style="dim", box=box.ROUNDED))


# Global message buffer instance
message_buffer = MessageBuffer()


def msg(text: str) -> None:
    """Add a message to the scrollable buffer."""
    message_buffer.add(text)


def msg_action(action: str, detail: str = "") -> None:
    """Add an action separator to the message buffer."""
    message_buffer.add_action(action, detail)


def clear_and_redraw(session: GameSession, console: Console,
                      sprite_state: str = SPRITE_IDLE) -> None:
    """Clear screen, draw HUD, then message area."""
    # Use ANSI escape codes instead of os.system("clear") for compatibility
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    render_hud(session, console, sprite_state)
    message_buffer.render(console)


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

_BANNER = r"""
  ___ ____  _     _____
 |_ _|  _ \| |   | ____|
  | || | | | |   |  _|
  | || |_| | |___| |___
 |___|____/|_____|_____|
     _       ____  _     _______ _   _ _____ _   _ ____  _____
    / \     |  _ \| |   | ____\ | \ | |_   _| | | |  _ \| ____|
   / _ \    | | | | |   |  _|  |  \| | | | | | | | |_) |  _|
  / ___ \ _ | |_| | |___| |___ | |\  | | | | |_| |  _ <| |___
 /_/   \_(_)|____/|_____|_____||_| \_| |_|  \___/|_| \_\_____|
"""


def render_game_banner(name: str, species: str, console: Console) -> None:
    console.print(Panel(
        f"[bold cyan]{_BANNER}[/bold cyan]\n"
        f"  [bold]{name}[/bold] the [italic]{species}[/italic] 开始了冒险！",
        border_style="cyan",
        box=box.DOUBLE,
    ))


# ---------------------------------------------------------------------------
# Explore animation (writes to message buffer)
# ---------------------------------------------------------------------------

_EXPLORE_FRAMES = [
    "{name} 正在四处张望 @_@",
    "{name} 翻开了一块石头...",
    "{name} 钻进了灌木丛...",
    "{name} 发现了什么痕迹!",
    "{name} 小心翼翼地前进...",
]


def render_explore_animation(name: str, console: Console) -> None:
    frames = [f.format(name=name) for f in _EXPLORE_FRAMES]
    for frame in random.sample(frames, min(3, len(frames))):
        console.print(f"  [dim]{frame}[/dim]")
        time.sleep(0.4)


# ---------------------------------------------------------------------------
# Travel animation
# ---------------------------------------------------------------------------

def render_travel_animation(name: str, dest: str, console: Console) -> None:
    steps = ["\u00b7", "\u00b7\u00b7", "\u00b7\u00b7\u00b7", "\u00b7\u00b7\u00b7\u00b7"]
    for i, step in enumerate(steps):
        pad = "  " * (i + 1)
        console.print(f"\r  {name} {pad}{step} \u279c {dest}", end="")
        sys.stdout.flush()
        time.sleep(0.25)
    console.print()


# ---------------------------------------------------------------------------
# Talk animation
# ---------------------------------------------------------------------------

_BUDDY_FACES = ["(^_^)", "(>_<)", "(o_o)", "(-_-)", "(*_*)", "(~_~)"]


def render_talk_enter(name: str, console: Console) -> None:
    face = random.choice(_BUDDY_FACES)
    console.print(f"  {name}  {face}  !")


def render_talk_exit(name: str, console: Console) -> None:
    console.print(f"  {name} (^_^)/ ~~")


# ---------------------------------------------------------------------------
# Use item animation
# ---------------------------------------------------------------------------

def render_use_item_animation(item_name: str, effect: str,
                                console: Console) -> None:
    sparkles = ["\u2728", "\u2728\u2728", "\u2728\u2728\u2728"]
    for s in sparkles:
        console.print(f"\r  {s} 使用 {item_name}...", end="")
        sys.stdout.flush()
        time.sleep(0.2)
    console.print(f"\r  \u2728 使用 {item_name}... [bold green]{effect}[/bold green]!")


# ---------------------------------------------------------------------------
# Rest animation
# ---------------------------------------------------------------------------

def render_rest_animation(name: str, hp_gain: int, console: Console) -> None:
    zzz = ""
    for _ in range(3):
        zzz += "z"
        console.print(f"\r  {name} {zzz.upper()}...", end="")
        sys.stdout.flush()
        time.sleep(0.3)
    console.print(f"\r  {name} ZZZ... [green]HP+{hp_gain}[/green]!")


# ---------------------------------------------------------------------------
# Look animation
# ---------------------------------------------------------------------------

def render_look_animation(name: str, console: Console) -> None:
    eyes = ["\u25c9 \u00b7", "\u00b7 \u25c9", "\u25c9 \u25c9"]
    for e in eyes:
        console.print(f"\r  {name} [{e}]", end="")
        sys.stdout.flush()
        time.sleep(0.15)
    console.print()


# ---------------------------------------------------------------------------
# Location / Narration (these now also push to message buffer)
# ---------------------------------------------------------------------------

def render_location(session: GameSession, console: Console) -> None:
    loc = session.location
    if not loc:
        return
    from .world import get_location_npcs
    connections = ", ".join(loc.connections)
    local_npcs = get_location_npcs(loc.name)
    npcs = ", ".join(n.name for n in local_npcs) if local_npcs else ""

    content = f"{loc.description}"
    content += f"\n可前往: {connections}"
    if npcs:
        content += f"  |  NPC: {npcs}"
    console.print(Panel(content, title=f"[bold]{loc.name}[/bold]", border_style="green"))


def render_narration(text: str, console: Console) -> None:
    console.print(Panel(text, border_style="blue", box=box.ROUNDED))
    msg(text)


# ---------------------------------------------------------------------------
# Stats / Inventory / Skills / Badges / Map (info commands)
# ---------------------------------------------------------------------------

def render_game_stats(session: GameSession, console: Console) -> None:
    table = Table(title=f"{session.companion_name} 的属性", box=box.SIMPLE_HEAVY)
    table.add_column("属性", style="bold")
    table.add_column("值", justify="right")
    table.add_column("", width=20)
    for stat in GAME_STAT_NAMES:
        val = session.stats.get(stat, 0)
        if stat == "HP":
            bar_len = min(20, max(0, val * 20 // 200))
            clr = "red" if val < 30 else ("yellow" if val < 60 else "green")
            bar = f"[{clr}]" + "\u2588" * bar_len + "\u2591" * (20 - bar_len) + f"[/{clr}]"
            table.add_row(stat, f"[{clr}]{val}[/{clr}]", bar)
        else:
            bar_len = min(20, val * 20 // 60)
            bar = "[cyan]" + "\u2588" * bar_len + "\u2591" * (20 - bar_len) + "[/cyan]"
            table.add_row(stat, str(val), bar)
    table.add_row("", "", "")
    table.add_row("[bold]心情[/bold]", str(session.mood), "")
    table.add_row("[bold]抽奖券[/bold]", str(session.tickets), "")
    console.print(table)


def render_inventory(items: list[Item], console: Console) -> None:
    if not items:
        console.print("[dim]背包空空如也。[/dim]")
        return
    table = Table(title="\U0001f392 背包", box=box.SIMPLE)
    table.add_column("#", width=3)
    table.add_column("物品", style="bold")
    table.add_column("稀有度")
    table.add_column("效果", style="cyan")
    rarity_colors = {"common": "dim", "uncommon": "green", "rare": "blue",
                     "epic": "magenta", "legendary": "yellow"}
    for i, item in enumerate(items, 1):
        c = rarity_colors.get(item.rarity, "dim")
        table.add_row(str(i), item.name, f"[{c}]{item.rarity}[/{c}]", item.effect)
    console.print(table)


def render_skills(skills: list[Skill], console: Console) -> None:
    if not skills:
        console.print("[dim]还没有学会任何技能。[/dim]")
        return
    table = Table(title="\u2694 技能", box=box.SIMPLE)
    table.add_column("#", width=3)
    table.add_column("技能", style="bold")
    table.add_column("威力", justify="right")
    table.add_column("元素")
    elem_colors = {"fire": "red", "water": "blue", "earth": "yellow",
                   "wind": "green", "shadow": "magenta", "light": "white"}
    for i, skill in enumerate(skills, 1):
        c = elem_colors.get(skill.element, "dim")
        table.add_row(str(i), skill.name, str(skill.power), f"[{c}]{skill.element}[/{c}]")
    console.print(table)


def render_badges(badges: list[Badge], console: Console) -> None:
    owned_ids = {b.badge_id for b in badges}
    owned, total = len(owned_ids), len(ALL_BADGES)
    console.print(f"\n[bold]\u2606 徽章 {owned}/{total}[/bold]\n")
    for tier, label in [("green", "\u25cf 普通"), ("purple", "\u25c6 珍贵"),
                         ("red", "\u2666 稀有"), ("gold", "\u2605 传说")]:
        color = BADGE_COLORS.get(tier, "dim")
        parts = []
        for bid, badge in ALL_BADGES.items():
            if badge.tier != tier:
                continue
            if bid in owned_ids:
                parts.append(f"[{color} bold]\u25c9 {badge.name}[/{color} bold]")
            else:
                parts.append(f"[dim]\u25cb ???[/dim]")
        console.print(f"  [{color}][{label}][/{color}] {' '.join(parts)}")
    console.print()


def render_map(session: GameSession, console: Console) -> None:
    cur = session.location.name if session.location else ""

    def _n(name: str) -> str:
        if name == cur:
            return f"[bold green on dark_green] {name} [/bold green on dark_green]"
        return f"[dim]{name}[/dim]"

    L = _n  # shorthand
    map_text = (
        f"  [bold yellow]-- 幽暗森林 --[/bold yellow]                    [bold blue]-- 水晶洞穴 --[/bold blue]\n"
        f"\n"
        f"  {L('林间小径')} ─┬─ {L('古树之心')}        {L('入口大厅')} ─┬─ {L('矿脉通道')}\n"
        f"              │         │                  │              │\n"
        f"        {L('蘑菇洼地')}    {L('精灵泉')}        {L('地底湖')} ─┴─ {L('晶体殿堂')}\n"
        f"              │              ↑                  │\n"
        f"              └──────────────┼──────────────────┤\n"
        f"                             │                  │\n"
        f"  [bold red]-- 风暴山脉 --[/bold red]                    [bold cyan]-- 深海遗迹 --[/bold cyan]\n"
        f"\n"
        f"  {L('山脚营地')}                            {L('沉船残骸')}\n"
        f"        │                                    │\n"
        f"  {L('悬崖小径')}                            {L('珊瑚迷宫')}\n"
        f"        │                                    │\n"
        f"  {L('云端平台')}                            {L('海神祭坛')}\n"
        f"        │                                    │\n"
        f"  {L('山顶神殿')}                            {L('深渊裂隙')}\n"
        f"                                             │\n"
        f"                                   ┌─────────┘\n"
        f"                                   │\n"
        f"  [bold magenta]-- 机械废墟 --[/bold magenta]                    [bold yellow]-- 星光圣殿 --[/bold yellow]\n"
        f"\n"
        f"  {L('废弃工厂')} ─── {L('数据中心')}        {L('前厅')} ─┬─ {L('星图室')}\n"
        f"                         │                │         │\n"
        f"                   {L('能量核心')}        {L('命运之池')}    {L('王座大厅')}\n"
        f"                         │                │              │\n"
        f"                   {L('控制室')} ─────── ┘   └──────────┘\n"
    )

    console.print(Panel(map_text, title="[bold]世界地图[/bold]", border_style="cyan"))


# ---------------------------------------------------------------------------
# Draw (gacha) animation
# ---------------------------------------------------------------------------

_GACHA_COLORS = ["red", "yellow", "green", "cyan", "magenta", "blue"]


def render_draw_animation(badge: Badge, is_new: bool, refund: int,
                           console: Console) -> None:
    color = BADGE_COLORS.get(badge.tier, "dim")
    tier_labels = {"green": "普通", "purple": "珍贵", "red": "稀有", "gold": "传说"}
    tier_label = tier_labels.get(badge.tier, "???")

    symbols = ["\u2660", "\u2663", "\u2665", "\u2666", "\u2605", "\u2606"]
    for i in range(10):
        c = _GACHA_COLORS[i % len(_GACHA_COLORS)]
        s = symbols[i % len(symbols)]
        speed = 0.06 + i * 0.03
        console.print(f"\r  [{c}]  {s}  抽卡中... {s}  [/{c}]", end="")
        sys.stdout.flush()
        time.sleep(speed)
    console.print()

    if is_new:
        console.print(Panel(
            f"[{color} bold]\u2605 新徽章！\u2605[/{color} bold]\n"
            f"[{color} bold]{badge.name}[/{color} bold]\n"
            f"[dim]{badge.description}[/dim]\n"
            f"效果: [{color}]{badge.effect}[/{color}]  "
            f"等级: [{color}]{tier_label}[/{color}]",
            border_style=color, box=box.DOUBLE,
        ))
        msg(f"\u2605 新徽章: {badge.name} ({badge.effect})")
    else:
        console.print(f"  [{color}]{badge.name}[/{color}] [dim]— 已拥有！退还 {refund} 券[/dim]")
        msg(f"重复徽章: {badge.name}, 退还 {refund} 券")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def render_event_prompt(text: str, console: Console) -> None:
    console.print(Panel(
        text + "\n\n[dim](60秒内回复，或留空跳过)[/dim]",
        title="[bold yellow]\u26a0 Buddy 需要你！[/bold yellow]",
        border_style="yellow", box=box.ROUNDED,
    ))


def render_event_result(text: str, console: Console) -> None:
    console.print(f"  [italic]{text}[/italic]")
    msg(f"[事件] {text}")


# ---------------------------------------------------------------------------
# Game Over
# ---------------------------------------------------------------------------

def render_game_over(session: GameSession, saved: dict, log_path: str | None,
                      console: Console) -> None:
    console.print()
    console.print(Panel("[bold]冒险结束！[/bold]", border_style="cyan", box=box.DOUBLE))

    stats_lines = [
        f"  回合: {session.turn_count}",
        f"  属性: " + ", ".join(f"{k}={session.stats.get(k, 0)}" for k in GAME_STAT_NAMES),
        f"  道具: {len(session.inventory)}  技能: {len(session.skills)}  徽章: {badge_progress(session)[0]}/{badge_progress(session)[1]}",
    ]
    for line in stats_lines:
        console.print(line)

    console.print("\n[bold]Roguelike 结算:[/bold]")
    saved_something = False
    if saved.get("items"):
        console.print(f"  [green]\u2713[/green] 道具: {', '.join(i.name for i in saved['items'])}")
        saved_something = True
    if saved.get("skills"):
        console.print(f"  [green]\u2713[/green] 技能: {', '.join(s.name for s in saved['skills'])}")
        saved_something = True
    if saved.get("badges"):
        console.print(f"  [green]\u2713[/green] 徽章: {', '.join(b.name for b in saved['badges'])}")
        saved_something = True
    if saved.get("tickets", 0) > 0:
        console.print(f"  [green]\u2713[/green] 抽奖券: {saved['tickets']}")
        saved_something = True
    if saved.get("stat_changes"):
        for stat, amount in saved["stat_changes"].items():
            console.print(f"  [green]\u2713[/green] 永久属性: {stat}+{amount}")
            saved_something = True
    if not saved_something:
        console.print("  [dim]这次运气不太好...[/dim]")
    if log_path:
        console.print(f"\n  [dim]日志: {log_path}[/dim]")
    console.print()
