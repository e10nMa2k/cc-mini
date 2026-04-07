"""Buddy auto-events — triggered automatically during adventure.

No user interaction. Each event auto-resolves with a random outcome.
Returns log lines describing what happened.
"""
from __future__ import annotations

import random
from typing import Callable

from .types import GameSession, Item


# ---------------------------------------------------------------------------
# Auto-resolve event: pick a random event and settle it
# ---------------------------------------------------------------------------

def auto_event(session: GameSession, log_fn: Callable[[str], None]) -> None:
    """Pick and auto-resolve a random event. Appends log lines via log_fn."""
    name = session.companion_name
    loc = session.location
    connections = loc.connections if loc else []

    events = [
        "hurt", "hungry", "treasure", "noise",
        "homesick", "weather", "dream", "gift", "npc_help",
    ]
    if connections:
        events.append("direction")

    event_id = random.choice(events)
    positive = random.random() < 0.6  # 60% positive outcome

    from . import state

    if event_id == "hurt":
        log_fn(f"🩹 {name} 在探索中被荆棘划伤了...")
        if positive:
            state.apply_stat_change("HP", 15)
            log_fn(f"   幸运地找到了治愈草药！(HP+15)")
        else:
            result = _pray_to_god(session, log_fn)

    elif event_id == "hungry":
        log_fn(f"🍖 {name} 的肚子咕咕叫了起来...")
        if positive:
            state.apply_stat_change("SPD", 1)
            session.mood = min(100, session.mood + 5)
            log_fn(f"   找到了一些野果，吃得心满意足！(SPD+1)")
        else:
            session.mood = max(0, session.mood - 5)
            log_fn(f"   到处找不到食物，只能饿着肚子继续走... (心情-5)")

    elif event_id == "treasure":
        log_fn(f"📦 {name} 发现了一个被藤蔓缠绕的神秘宝箱！")
        if positive:
            tickets = random.randint(2, 5)
            state.add_tickets(tickets)
            log_fn(f"   宝箱打开了！获得了 {tickets} 张奖券！")
        else:
            log_fn(f"   宝箱是空的...或许早就被人取走了。")

    elif event_id == "noise":
        log_fn(f"👂 {name} 竖起耳朵——远处传来了奇怪的声音...")
        if positive:
            state.add_tickets(2)
            log_fn(f"   勇敢地调查了声源，发现了一个藏宝处！(+2 奖券)")
        else:
            state.apply_stat_change("HP", -5)
            state.apply_stat_change("ATK", 1)
            log_fn(f"   遇到了一只小怪物，经过搏斗获得了经验！(HP-5, ATK+1)")

    elif event_id == "homesick":
        log_fn(f"🌟 {name} 抬头望着星空，想起了你...")
        if positive:
            state.apply_stat_change("LCK", 1)
            session.mood = min(100, session.mood + 10)
            log_fn(f"   美好的回忆给了它力量！(LCK+1, 心情+10)")
        else:
            session.mood = max(0, session.mood - 10)
            log_fn(f"   叹了口气，独自继续前行了... (心情-10)")

    elif event_id == "weather":
        log_fn(f"⛈️  乌云聚集，暴风雨就要来了！")
        if positive:
            log_fn(f"   {name} 找到了一个安全的洞穴，平安度过了暴风雨！")
        else:
            state.apply_stat_change("HP", -10)
            log_fn(f"   没来得及躲避，被暴风雨淋了个透！(HP-10)")

    elif event_id == "dream":
        log_fn(f"💤 {name} 小憩了一会儿，梦到了一条发光的小路...")
        if positive:
            tickets = random.randint(3, 5)
            state.add_tickets(tickets)
            log_fn(f"   跟随梦境的指引，发现了隐藏的宝藏！(+{tickets} 奖券)")
        else:
            log_fn(f"   醒来后，梦境的记忆如同晨雾般散去了...")

    elif event_id == "gift":
        log_fn(f"🎁 {name} 兴冲冲地跑过来，嘴里叼着一个小东西！")
        if positive:
            tickets = random.randint(1, 3)
            state.add_tickets(tickets)
            log_fn(f"   竟然是宝贵的奖券！(+{tickets} 奖券)")
        else:
            log_fn(f"   仔细一看是一根普通的木棍... 但心意可嘉！")

    elif event_id == "npc_help":
        npc_species = random.choice(["精灵", "矮人", "旅行商人", "流浪骑士", "神秘老者"])
        log_fn(f"🧝 一个{npc_species}向{name}走来求助...")
        if positive:
            tickets = random.randint(2, 4)
            state.add_tickets(tickets)
            log_fn(f"   {name} 热心地帮了忙，对方感激地赠送了奖券！(+{tickets} 奖券)")
        else:
            log_fn(f"   {name} 尝试帮忙，但事情太复杂了... 双方都有些无奈。")

    elif event_id == "direction":
        dest = random.choice(connections)
        log_fn(f"🔀 {name} 站在岔路口，随机选择了前往 [{dest}]...")
        from .world import get_location
        new_loc = get_location(dest)
        if new_loc:
            session.location = new_loc
            log_fn(f"   抵达了 {dest}！")


def _pray_to_god(session: GameSession, log_fn: Callable[[str], None]) -> None:
    """Buddy prays for healing — random penalty."""
    from . import state
    name = session.companion_name
    roll = random.random()

    if roll < 0.50:
        state.apply_stat_change("HP", 15)
        log_fn(f"   神灵降下治愈之光！(HP+15)")
    else:
        state.apply_stat_change("HP", 10)
        stat = random.choice(["ATK", "DEF", "SPD", "LCK"])
        amount = random.randint(1, 2)
        state.apply_stat_change(stat, -amount)
        log_fn(f"   神灵有些吝啬...恢复了一些体力但代价不小。(HP+10, {stat}-{amount})")
