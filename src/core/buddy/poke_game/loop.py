"""Main game loop for Idle Adventure (IA) — takes over the terminal when active.

Entry point: start_game() called from /buddy ia command.
"""
from __future__ import annotations

import atexit
import random
import threading
import time

from rich.console import Console

from .types import GameSession, Item, Skill, EXPLORE_TICKETS_MIN, EXPLORE_TICKETS_MAX, GUARANTEED_EXPLORE_COUNT, POST_GUARANTEE_EVENT_CHANCE
from .world import LOCATIONS, START_LOCATION, get_location, get_random_monster, get_location_npcs, BUDDY_TALK_RESPONSES
from .badges import draw_badge, badge_progress
from .lockfile import acquire_lock, release_lock, update_heartbeat
from .battle import run_battle
from .persistence import roguelike_save, restore_from_loot, save_adventure_log
from .state import (
    new_session, get_session, end_session,
    apply_stat_change, add_item, add_skill, add_tickets,
    append_log, is_alive,
)
from .events import EventScheduler, is_positive_response, apply_positive_result, apply_negative_result
from .narrator import Narrator
from .commands import parse_game_command, HELP_TEXT, GameCompleter, game_toolbar
from .render import (
    render_game_banner, render_location, render_narration,
    render_game_stats, render_inventory, render_skills, render_badges,
    render_draw_animation, render_map,
    render_event_prompt, render_event_result, render_game_over,
    render_explore_animation, render_travel_animation,
    render_talk_enter, render_talk_exit,
    render_use_item_animation, render_rest_animation,
    render_look_animation,
    clear_and_redraw, msg, msg_action, message_buffer,
    SPRITE_IDLE, SPRITE_EXPLORE, SPRITE_BATTLE, SPRITE_REST, SPRITE_TALK, SPRITE_HURT,
)


# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------

_heartbeat_stop = threading.Event()


def _heartbeat_loop() -> None:
    while not _heartbeat_stop.is_set():
        update_heartbeat()
        _heartbeat_stop.wait(30)


# ---------------------------------------------------------------------------
# Input with timeout (for interactive events)
# ---------------------------------------------------------------------------

def _input_with_timeout(prompt: str, timeout: int = 60) -> str | None:
    """Blocking input with timeout. Returns None on timeout."""
    result: list[str | None] = [None]

    def _read() -> None:
        try:
            result[0] = input(prompt)
        except (EOFError, KeyboardInterrupt):
            pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    return result[0]


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------

def _process_events(events: list[dict], session: GameSession,
                     narrator: Narrator, console: Console) -> None:
    """Process events returned by narrator."""
    for event in events:
        etype = event.get("type")
        if etype == "item":
            item = Item(
                name=event.get("name", "未知物品"),
                description=event.get("description", ""),
                rarity=event.get("rarity", "common"),
                effect=event.get("effect", ""),
            )
            add_item(item)
            text = f"  \u2726 获得物品: [{item.rarity}] {item.name} ({item.effect})"
            console.print(f"  [green]\u2726[/green] 获得物品: [{_rarity_color(item.rarity)}]{item.name}[/{_rarity_color(item.rarity)}] ({item.effect})")
            msg(text)

        elif etype == "skill":
            skill = Skill(
                name=event.get("name", "未知技能"),
                description=event.get("description", ""),
                power=int(event.get("power", 10)),
                element=event.get("element", "light"),
            )
            add_skill(skill)
            console.print(f"  [green]\u2726[/green] 学会技能: [bold]{skill.name}[/bold] (威力:{skill.power}, {skill.element})")
            msg(f"  \u2726 学会技能: {skill.name} (威力:{skill.power}, {skill.element})")

        elif etype == "stat":
            stat = event.get("stat", "HP")
            amount = int(event.get("amount", 1))
            new_val = apply_stat_change(stat, amount)
            console.print(f"  [green]\u2726[/green] 属性变化: {stat} +{amount} → {new_val}")

        elif etype == "tickets":
            amount = int(event.get("amount", 1))
            add_tickets(amount)
            console.print(f"  [green]\u2726[/green] 获得抽奖券 x{amount}")


def _rarity_color(rarity: str) -> str:
    return {"common": "dim", "uncommon": "green", "rare": "blue",
            "epic": "magenta", "legendary": "yellow"}.get(rarity, "dim")


def _handle_npc_encounter(session: GameSession, console: Console) -> None:
    """Encounter a fixed NPC at current location. Random outcome."""
    loc_name = session.location.name if session.location else ""
    npcs = get_location_npcs(loc_name)
    if not npcs:
        return
    npc = random.choice(npcs)

    msg_action("遇见NPC", npc.name)
    console.print(f"\n  [yellow bold]{npc.name}[/yellow bold] ({npc.species}) 出现了！")
    console.print(f"  [dim]{npc.greeting}[/dim]")

    roll = random.random()
    if roll < npc.gift_chance and npc.gifts:
        gift_data = random.choice(npc.gifts)
        item = Item(
            name=gift_data["name"], description=gift_data.get("description", ""),
            rarity=gift_data.get("rarity", "common"), effect=gift_data.get("effect", ""),
        )
        add_item(item)
        console.print(f"  [green]\u2726[/green] {npc.name}送了你: [{_rarity_color(item.rarity)}]{item.name}[/{_rarity_color(item.rarity)}] ({item.effect})")
        msg(f"{npc.name} 赠送了 {item.name} ({item.effect})")
        append_log(f"[NPC] {npc.name}赠送了{item.name}")
    elif roll < npc.gift_chance + npc.secret_chance and npc.secrets:
        secret = random.choice(npc.secrets)
        console.print(f"  [cyan]\u2731[/cyan] {npc.name}低声说：\"{secret}\"")
        msg(f"{npc.name} 说：\"{secret}\"")
        append_log(f"[NPC] {npc.name}透露：{secret}")
    else:
        # Ignore
        ignore_msgs = [
            f"{npc.name}瞥了你一眼，没有搭理。",
            f"{npc.name}似乎在忙自己的事情，没注意到你。",
            f"{npc.name}摇了摇头，转身离开了。",
            f"{npc.name}嘟囔了几句听不清的话。",
        ]
        chosen_msg = random.choice(ignore_msgs)
        console.print(f"  [dim]{chosen_msg}[/dim]")
        msg(chosen_msg)
    console.print()


def _pick_from_list(options: list[tuple[str, str]], prompt_text: str,
                     console: Console) -> str | None:
    """Show a numbered list and let user pick. Returns chosen value or None."""
    if not options:
        return None
    console.print()
    for i, (name, desc) in enumerate(options, 1):
        console.print(f"  [cyan]{i}[/cyan]. {name}  [dim]{desc}[/dim]")
    console.print(f"  [dim]0. 取消[/dim]")
    console.print()
    try:
        choice = input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        return None
    choice = choice.strip()
    # Accept number
    try:
        idx = int(choice)
        if idx == 0:
            return None
        if 1 <= idx <= len(options):
            return options[idx - 1][0]
    except ValueError:
        pass
    # Accept name directly
    for name, _ in options:
        if choice in name:
            return name
    return None


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def _execute_command(cmd: str, args: str, session: GameSession,
                      narrator: Narrator, console: Console) -> bool:
    """Execute a game command. Returns False to quit."""

    if cmd == "quit":
        return False

    elif cmd == "explore":
        loc = session.location
        loc_name = loc.name if loc else "???"
        region = loc.region if loc else "???"
        msg_action("探索", loc_name)

        # Track exploration count for this location
        count = session.explore_counts.get(loc_name, 0) + 1
        session.explore_counts[loc_name] = count
        guaranteed = count <= GUARANTEED_EXPLORE_COUNT

        console.print(f"\n[dim]{session.companion_name} 开始探索 {loc_name}... (第{count}次)[/dim]")
        render_explore_animation(session.companion_name, console)
        console.print()

        # Decide if a monster encounter or event happens
        # First 3 visits: guaranteed something (40% monster, 60% event)
        # After 3: only 5% chance of anything
        roll = random.random()
        if guaranteed:
            # 30% monster, 30% NPC, 40% event (item/skill/stat)
            monster_chance = 0.30
            npc_chance = 0.30
            event_chance = 0.40
        else:
            # 5% total: 2% monster, 1% NPC, 2% event
            monster_chance = 0.02
            npc_chance = 0.01
            event_chance = 0.02

        if roll < monster_chance:
            # Monster battle!
            monster = get_random_monster(region)
            if monster:
                msg_action("战斗", f"vs Lv.{monster.level} {monster.name}")
                result = run_battle(session, monster, console)
                if result.won:
                    # Apply HP loss
                    apply_stat_change("HP", -result.hp_lost)
                    # Apply rewards
                    if result.reward_item:
                        add_item(result.reward_item)
                        console.print(f"  [green]\u2726[/green] 战利品: [{_rarity_color(result.reward_item.rarity)}]{result.reward_item.name}[/{_rarity_color(result.reward_item.rarity)}] ({result.reward_item.effect})")
                    if result.reward_skill:
                        add_skill(result.reward_skill)
                        console.print(f"  [green]\u2726[/green] 领悟技能: [bold]{result.reward_skill.name}[/bold] (威力:{result.reward_skill.power})")
                    if result.reward_stat:
                        stat, amount = result.reward_stat
                        new_val = apply_stat_change(stat, amount)
                        console.print(f"  [green]\u2726[/green] 属性提升: {stat}+{amount} → {new_val}")
                    if result.reward_tickets:
                        add_tickets(result.reward_tickets)
                        console.print(f"  [yellow]\u2726[/yellow] 获得抽奖券 x{result.reward_tickets}")
                    append_log(f"[战斗] 击败了 Lv.{monster.level} {monster.name}！")
                    msg(f"胜利！击败了 Lv.{monster.level} {monster.name} ({result.rounds}回合, HP-{result.hp_lost})")
                else:
                    # Defeat: lose HP but don't die (floor at 1)
                    new_hp = apply_stat_change("HP", -result.hp_lost)
                    if new_hp <= 0:
                        apply_stat_change("HP", 1 - session.stats.get("HP", 0))  # set to 1
                    # Small chance to lose item or skill
                    lost_msg = ""
                    if random.random() < 0.15 and session.inventory:
                        from .state import remove_random_item
                        lost = remove_random_item()
                        if lost:
                            lost_msg += f" 丢失了 {lost.name}..."
                    if random.random() < 0.10 and session.skills:
                        from .state import remove_random_skill
                        lost = remove_random_skill()
                        if lost:
                            lost_msg += f" 遗忘了 {lost.name}..."
                    if lost_msg:
                        console.print(f"  [red]{lost_msg.strip()}[/red]")
                    append_log(f"[战斗] 被 Lv.{monster.level} {monster.name} 击败了...{lost_msg}")
                    msg(f"战败... 被 Lv.{monster.level} {monster.name} 击倒{lost_msg}")
                console.print()

        elif roll < monster_chance + npc_chance:
            # NPC encounter at this location
            narrative = narrator.narrate_exploration(session)[0]
            render_narration(narrative, console)
            append_log(narrative)
            _handle_npc_encounter(session, console)

        elif roll < monster_chance + npc_chance + event_chance:
            # Normal exploration with events (LLM narration)
            narrative, events = narrator.narrate_exploration(session)
            render_narration(narrative, console)
            append_log(narrative)
            _process_events(events, session, narrator, console)

            # If guaranteed but LLM returned no events, force a random one
            if guaranteed and not events:
                from .narrator import Narrator
                fallback_events = narrator._fallback_events(loc, session)
                if fallback_events:
                    _process_events(fallback_events, session, narrator, console)

        else:
            # Nothing happens (only after 3rd explore)
            narrative = narrator.narrate_exploration(session)[0]
            render_narration(narrative, console)
            append_log(narrative)
            console.print(f"  [dim]这次探索没有特别的发现。[/dim]")

        # Award tickets: only during guaranteed explorations
        if guaranteed:
            base = random.randint(EXPLORE_TICKETS_MIN, EXPLORE_TICKETS_MAX)
            lck_bonus = 1 if session.stats.get("LCK", 10) > 30 else 0
            loc_bonus = loc.ticket_bonus if loc else 0
            total_tickets = base + lck_bonus + loc_bonus
            add_tickets(total_tickets)
            console.print(f"  [yellow]\u2726[/yellow] 获得抽奖券 x{total_tickets}")
        else:
            console.print(f"  [dim]这片区域已经被翻遍了，不如去别处看看吧。[/dim]")
        console.print()

    elif cmd == "go":
        if not args:
            if not session.location or not session.location.connections:
                console.print("[dim]无处可去...[/dim]")
                return True
            options = [(c, get_location(c).region if get_location(c) else "")
                       for c in session.location.connections]
            chosen = _pick_from_list(options, "  前往> ", console)
            if not chosen:
                return True
            args = chosen
        dest = get_location(args.strip())
        if not dest:
            # Fuzzy match
            for name in (session.location.connections if session.location else []):
                if args.strip() in name:
                    dest = get_location(name)
                    break
        if not dest:
            console.print(f"[red]找不到地点: {args}[/red]")
            if session.location:
                console.print(f"[dim]可前往: {', '.join(session.location.connections)}[/dim]")
            return True
        if session.location and dest.name not in session.location.connections:
            console.print(f"[red]无法从 {session.location.name} 直接前往 {dest.name}[/red]")
            console.print(f"[dim]可前往: {', '.join(session.location.connections)}[/dim]")
            return True
        # Track location transition for explore count reset
        old_name = session.location.name if session.location else None
        if old_name:
            # Start tracking the old location: record dest as a place visited since leaving
            session.locations_since_left.setdefault(old_name, set())
            # For ALL tracked locations, add dest as a new unique visit
            for tracked_loc in session.locations_since_left:
                session.locations_since_left[tracked_loc].add(dest.name)
        # Check if dest's explore count should reset (visited >=3 other places since leaving)
        if dest.name in session.locations_since_left:
            if len(session.locations_since_left[dest.name]) >= 3:
                session.explore_counts[dest.name] = 0
                del session.locations_since_left[dest.name]
        session.location = dest
        msg_action("前往", dest.name)
        render_travel_animation(session.companion_name, dest.name, console)
        narrative = narrator.narrate_arrival(session)
        render_narration(narrative, console)
        append_log(f"[前往 {dest.name}] {narrative}")
        # Show fixed NPCs at this location
        local_npcs = get_location_npcs(dest.name)
        if local_npcs:
            for npc in local_npcs:
                console.print(f"  [yellow]{npc.name}[/yellow] ({npc.species}) 在这里。")
        render_location(session, console)

    elif cmd == "look":
        msg_action("观察", session.location.name if session.location else "")
        render_look_animation(session.companion_name, console)
        render_location(session, console)

    elif cmd == "talk":
        name = session.companion_name
        msg_action("对话", name)
        render_talk_enter(name, console)
        console.print(f"[dim]  正在和 {name} 聊天... (输入 '再见' 或空行结束)[/dim]\n")
        response = random.choice(BUDDY_TALK_RESPONSES).format(name=name)
        console.print(f"  [italic]{response}[/italic]\n")
        while True:
            try:
                user_input = input(f"  你> ")
            except (EOFError, KeyboardInterrupt):
                break
            user_input = user_input.strip()
            if not user_input or user_input in ("再见", "拜拜", "bye", "quit", "exit"):
                render_talk_exit(name, console)
                console.print()
                break
            response = random.choice(BUDDY_TALK_RESPONSES).format(name=name)
            console.print(f"  [italic]{response}[/italic]\n")

    elif cmd == "use":
        if not args:
            if not session.inventory:
                console.print("[dim]背包是空的。[/dim]")
                return True
            options = [(it.name, f"[{it.rarity}] {it.effect}")
                       for it in session.inventory]
            chosen = _pick_from_list(options, "  使用哪个> ", console)
            if not chosen:
                return True
            args = chosen
        item = None
        for it in session.inventory:
            if args.strip() in it.name:
                item = it
                break
        if not item:
            console.print(f"[red]背包中没有: {args}[/red]")
            return True
        msg_action("使用", item.name)
        render_use_item_animation(item.name, item.effect, console)
        _apply_item_effect(item, session, console)
        session.inventory.remove(item)
        msg(f"使用了 {item.name} ({item.effect})")

    elif cmd == "draw":
        msg_action("抽卡")
        badge, is_new, refund = draw_badge(session)
        if badge is None:
            console.print(f"[dim]抽奖券不足！需要5张，当前{session.tickets}张。[/dim]")
            return True
        render_draw_animation(badge, is_new, refund, console)
        owned, total = badge_progress(session)
        console.print(f"  [dim]徽章进度: {owned}/{total} | 剩余券: {session.tickets}[/dim]\n")

    elif cmd == "bag":
        render_inventory(session.inventory, console)

    elif cmd == "skills":
        render_skills(session.skills, console)

    elif cmd == "stats":
        render_game_stats(session, console)

    elif cmd == "badges":
        render_badges(session.badges, console)

    elif cmd == "map":
        render_map(session, console)

    elif cmd == "rest":
        msg_action("休息")
        hp_gain = random.randint(10, 20)
        apply_stat_change("HP", hp_gain)
        render_rest_animation(session.companion_name, hp_gain, console)
        msg(f"休息恢复了 HP+{hp_gain}")
        append_log(f"[休息] HP恢复{hp_gain}点")

    elif cmd == "battle":
        console.print("[yellow]对战功能正在开发中...敬请期待！[/yellow]")

    elif cmd == "help":
        console.print(HELP_TEXT)

    elif cmd == "empty":
        pass

    elif cmd == "unknown":
        console.print(f"[dim]未知命令。输入 help 查看可用命令。[/dim]")

    return True


def _apply_item_effect(item: Item, session: GameSession, console: Console) -> None:
    """Parse item.effect like 'HP+20' or 'ATK+3' and apply."""
    import re
    effects = re.findall(r"(\w+)([+-])(\d+)", item.effect)
    if not effects:
        console.print(f"  使用了 {item.name}！")
        return
    for stat, sign, val_str in effects:
        val = int(val_str)
        if sign == "-":
            val = -val
        if stat == "全属性":
            for s in ("HP", "ATK", "DEF", "SPD", "LCK"):
                apply_stat_change(s, val)
            console.print(f"  使用 {item.name}：全属性 +{val_str}！")
        elif stat in session.stats:
            new_val = apply_stat_change(stat, val)
            console.print(f"  使用 {item.name}：{stat} {sign}{val_str} → {new_val}")
        else:
            console.print(f"  使用了 {item.name}！")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def start_game(client: object, console: Console, model: str) -> None:
    """Start the Poke_Game. Takes over the terminal until quit."""

    # 1. Check companion exists
    try:
        from ..companion import get_companion
        companion = get_companion()
    except Exception:
        companion = None

    if not companion:
        console.print("[dim]还没有伴侣呢！先输入 /buddy 孵化一个吧。[/dim]")
        return

    # 2. Acquire lock
    if not acquire_lock():
        console.print("[red]另一个终端正在进行 Idle Adventure！同一时间只能运行一个游戏。[/red]")
        return

    # 3. Setup cleanup
    _cleanup_done = [False]

    def _cleanup() -> None:
        if _cleanup_done[0]:
            return
        _cleanup_done[0] = True
        _heartbeat_stop.set()
        s = end_session()
        if s:
            try:
                saved = roguelike_save(s)
                save_adventure_log(s)
            except Exception:
                pass
        release_lock()

    atexit.register(_cleanup)

    # 4. Start heartbeat
    _heartbeat_stop.clear()
    hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb_thread.start()

    # 5. Create session
    session = new_session(
        companion_name=companion.name,
        companion_species=companion.species,
        companion_eye=companion.eye,
        companion_hat=companion.hat,
    )

    # Restore persisted loot
    restore_from_loot(session)

    # Set starting location
    start_loc = get_location(START_LOCATION)
    if start_loc:
        session.location = start_loc

    # 6. Create narrator and event scheduler
    narrator = Narrator(companion.name, companion.species)
    scheduler = EventScheduler()

    # 7. Render intro banner
    render_game_banner(companion.name, companion.species, console)
    console.print()
    owned, total = badge_progress(session)
    if session.tickets > 0 or owned > 0:
        console.print(f"[dim]载入存档: {owned} 个徽章, {session.tickets} 张抽奖券[/dim]")

    # Initial arrival
    narrative = narrator.narrate_arrival(session)
    msg(narrative)
    append_log(narrative)

    # 8. Setup prompt_toolkit session
    from prompt_toolkit import PromptSession

    def _get_session():
        return get_session()

    completer = GameCompleter(session_getter=_get_session)

    def _toolbar():
        return game_toolbar(_get_session)

    prompt_session = PromptSession(
        completer=completer,
        complete_while_typing=True,
        bottom_toolbar=_toolbar,
    )

    # Track current sprite state for HUD
    _sprite_state = [SPRITE_IDLE]

    def _redraw() -> None:
        clear_and_redraw(session, console, _sprite_state[0])

    # Initial HUD draw
    _redraw()

    # 9. Game loop
    try:
        while session.active:
            # Check for interactive event BEFORE prompt
            if scheduler.should_trigger():
                event = scheduler.get_event(session)
                if event:
                    _sprite_state[0] = SPRITE_HURT
                    _redraw()
                    render_event_prompt(event.prompt_text, console)
                    response = _input_with_timeout("  回复> ", 60)
                    result_text = ""
                    if response is not None and response.strip():
                        if is_positive_response(event.event_id, response, event):
                            result_text = apply_positive_result(event.event_id, session, event)
                            if event.event_id == "direction" and event.choices:
                                for choice in event.choices:
                                    if choice in response:
                                        dest = get_location(choice)
                                        if dest:
                                            session.location = dest
                                            arr = narrator.narrate_arrival(session)
                                            msg(arr)
                                            append_log(f"[前往 {dest.name}] {arr}")
                                        break
                        else:
                            result_text = apply_negative_result(event.event_id, session)
                    else:
                        result_text = apply_negative_result(event.event_id, session)

                    if result_text:
                        msg(f"[事件] {result_text}")
                    append_log(f"[事件: {event.event_id}] {result_text}")

                    if not is_alive():
                        msg("[HP 降到了 0... 冒险结束]")
                        break

                    _sprite_state[0] = SPRITE_IDLE
                    _redraw()

            # Prompt
            loc_name = session.location.name if session.location else "???"
            try:
                raw = prompt_session.prompt(f"[{loc_name}] {session.companion_name}> ")
            except (EOFError, KeyboardInterrupt):
                msg("[冒险中断]")
                break

            cmd, args = parse_game_command(raw)

            # Set sprite state based on command
            if cmd == "explore":
                _sprite_state[0] = SPRITE_EXPLORE
            elif cmd == "go":
                _sprite_state[0] = SPRITE_EXPLORE
            elif cmd == "rest":
                _sprite_state[0] = SPRITE_REST
            elif cmd == "talk":
                _sprite_state[0] = SPRITE_TALK
            elif cmd == "draw":
                _sprite_state[0] = SPRITE_IDLE

            # Info commands: redraw FIRST, then print output below
            _INFO_CMDS = ("bag", "skills", "stats", "badges", "map", "help")
            if cmd in _INFO_CMDS:
                _sprite_state[0] = SPRITE_IDLE
                _redraw()
                if not _execute_command(cmd, args, session, narrator, console):
                    break
                # Don't redraw again — leave info visible until next command
            else:
                # Action commands: execute (with animation), then redraw
                if not _execute_command(cmd, args, session, narrator, console):
                    break

                # Increment turn
                if cmd not in ("empty", "unknown", "talk"):
                    session.turn_count += 1
                    scheduler.tick()

                # Check HP
                if not is_alive():
                    _sprite_state[0] = SPRITE_HURT
                    _redraw()
                    console.print("[bold red]HP 降到了 0... 冒险结束。[/bold red]")
                    break

                # Redraw HUD after action
                _sprite_state[0] = SPRITE_IDLE
                if cmd != "quit":
                    _redraw()

    except Exception as e:
        console.print(f"[red]游戏出错: {e}[/red]")

    # 10. Game over
    _heartbeat_stop.set()
    s = end_session() or session

    try:
        saved = roguelike_save(s)
        log_path = save_adventure_log(s)
        render_game_over(s, saved, str(log_path) if log_path else None, console)
    except Exception as e:
        console.print(f"[red]保存失败: {e}[/red]")

    release_lock()
    _cleanup_done[0] = True

    try:
        atexit.unregister(_cleanup)
    except Exception:
        pass

    console.print("[dim]已返回 Claude Code。[/dim]\n")
