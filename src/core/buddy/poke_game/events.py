"""Buddy interactive events — proactive interactions every 3-8 turns.

10 event types with keyword matching, timeout handling, and God prayer penalty.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .types import GameSession, Item, NPC, GAME_STAT_NAMES


# ---------------------------------------------------------------------------
# Keyword maps (lenient matching)
# ---------------------------------------------------------------------------

POSITIVE_KEYWORDS: dict[str, list[str] | None] = {
    "hurt":      ["治愈", "治疗", "吃药", "药", "抱抱", "安慰", "包扎", "heal", "cure", "hug", "medicine"],
    "direction":  [],  # matches location name dynamically
    "hungry":    ["喂", "吃", "食物", "食", "feed", "eat", "food"],
    "treasure":  ["开", "打开", "拿", "取", "open", "take", "get"],
    "noise":     ["看", "调查", "去", "查", "check", "look", "go", "investigate"],
    "homesick":  None,  # any non-empty reply is positive
    "npc_help":  ["帮", "助", "好", "行", "可以", "help", "yes", "ok", "sure"],
    "weather":   ["躲", "藏", "避", "洞", "shelter", "hide", "cave", "run"],
    "dream":     ["追", "跟", "去", "follow", "chase", "go"],
    "gift":      ["收", "谢", "要", "拿", "take", "thanks", "yes", "accept"],
}


# ---------------------------------------------------------------------------
# Event definitions
# ---------------------------------------------------------------------------

@dataclass
class InteractiveEvent:
    event_id: str
    prompt_text: str  # what buddy says (with {name} placeholder)
    timeout_seconds: int = 60

    # For direction events
    choices: list[str] = field(default_factory=list)


def _generate_events(session: GameSession) -> list[InteractiveEvent]:
    """Build the list of possible events based on current game state."""
    name = session.companion_name
    loc = session.location

    # Get two random connected locations for direction event
    connections = loc.connections if loc else []
    dir_choices = random.sample(connections, min(2, len(connections))) if connections else []

    events = [
        InteractiveEvent(
            "hurt",
            f"{name}在探索中被荆棘划伤了，疼得直哼哼... 你能帮帮它吗？",
        ),
        InteractiveEvent(
            "hungry",
            f"{name}的肚子咕咕叫了起来，眼巴巴地看着你...",
        ),
        InteractiveEvent(
            "treasure",
            f"{name}发现了一个被藤蔓缠绕的神秘宝箱！要打开吗？",
        ),
        InteractiveEvent(
            "noise",
            f"{name}竖起耳朵——远处传来了奇怪的声音... 要去调查吗？",
        ),
        InteractiveEvent(
            "homesick",
            f"{name}抬头望着星空，似乎有点想你了...",
        ),
        InteractiveEvent(
            "weather",
            f"乌云突然聚集，暴风雨就要来了！{name}在四处寻找躲避的地方...",
        ),
        InteractiveEvent(
            "dream",
            f"{name}打了个盹，梦到了一条发光的小路通向远方... 要跟随梦境的指引吗？",
        ),
        InteractiveEvent(
            "gift",
            f"{name}兴冲冲地跑过来，嘴里叼着一个小东西想送给你！",
        ),
    ]

    # Direction event (only if there are connections)
    if len(dir_choices) >= 2:
        events.append(InteractiveEvent(
            "direction",
            f"{name}站在岔路口，不知道该往哪走...\n"
            f"  左边是 [{dir_choices[0]}]，右边是 [{dir_choices[1]}]",
            choices=dir_choices,
        ))

    # NPC help event
    npc_species = random.choice(["精灵", "矮人", "旅行商人", "流浪骑士", "神秘老者"])
    npc_requests = [
        "能帮我找回丢失的护身符吗？",
        "我迷路了，能指个方向吗？",
        "我的伙伴受伤了，你有多余的草药吗？",
        "这附近有危险的怪物，能一起走吗？",
    ]
    events.append(InteractiveEvent(
        "npc_help",
        f"一个{npc_species}向{name}走来求助：\"{random.choice(npc_requests)}\"",
    ))

    return events


# ---------------------------------------------------------------------------
# Event scheduler
# ---------------------------------------------------------------------------

class EventScheduler:
    """Tracks turn count and triggers random interactive events."""

    _MIN_INTERVAL = 3
    _MAX_INTERVAL = 8

    def __init__(self) -> None:
        self._turns_since_last = 0
        self._next_trigger = random.randint(self._MIN_INTERVAL, self._MAX_INTERVAL)
        self._used_events: set[str] = set()  # avoid immediate repeats

    def tick(self) -> None:
        """Call after each player command."""
        self._turns_since_last += 1

    def should_trigger(self) -> bool:
        return self._turns_since_last >= self._next_trigger

    def get_event(self, session: GameSession) -> InteractiveEvent | None:
        """Pick a random event and reset timer."""
        if not self.should_trigger():
            return None

        events = _generate_events(session)
        # Avoid repeating the last event
        available = [e for e in events if e.event_id not in self._used_events]
        if not available:
            self._used_events.clear()
            available = events

        event = random.choice(available)
        self._used_events.add(event.event_id)
        # Keep used set small
        if len(self._used_events) > 5:
            self._used_events.pop()

        self._turns_since_last = 0
        self._next_trigger = random.randint(self._MIN_INTERVAL, self._MAX_INTERVAL)
        return event


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------

def is_positive_response(event_id: str, user_input: str,
                          event: InteractiveEvent | None = None) -> bool:
    """Check if user response is positive for the given event type."""
    text = user_input.strip().lower()
    if not text:
        return False

    keywords = POSITIVE_KEYWORDS.get(event_id)
    if keywords is None:
        # homesick: any non-empty reply is positive
        return True
    if event_id == "direction" and event and event.choices:
        # Match location name
        return any(c in user_input for c in event.choices)
    return any(kw in text for kw in keywords)


def apply_positive_result(event_id: str, session: GameSession,
                            event: InteractiveEvent | None = None) -> str:
    """Apply positive outcome. Returns description text."""
    from . import state
    name = session.companion_name

    if event_id == "hurt":
        state.apply_stat_change("HP", 15)
        return f"你温柔地照顾了{name}，它的伤口很快就好了！(HP+15)"

    elif event_id == "direction":
        if event and event.choices:
            # The user picked a direction — handled by caller to do actual movement
            return f"{name}朝着你指的方向开心地跑去了！"
        return f"{name}继续前行。"

    elif event_id == "hungry":
        state.apply_stat_change("SPD", 1)
        session.mood = min(100, session.mood + 5)
        return f"你喂饱了{name}，它满足地打了个饱嗝。(SPD+1)"

    elif event_id == "treasure":
        item = Item(
            name=random.choice(["魔法水晶", "古老卷轴", "神秘药水", "精灵石"]),
            description="从宝箱中发现的宝物",
            rarity=random.choice(["common", "uncommon", "rare"]),
            effect=random.choice(["HP+10", "ATK+2", "DEF+2", "LCK+2"]),
        )
        state.add_item(item)
        return f"宝箱打开了！获得了 [{item.rarity}] {item.name}！({item.effect})"

    elif event_id == "noise":
        # Random mini-event
        roll = random.random()
        if roll < 0.5:
            state.add_tickets(2)
            return f"{name}勇敢地调查了声源，发现了一个藏宝处！(+2 抽奖券)"
        else:
            state.apply_stat_change("HP", -5)
            state.apply_stat_change("ATK", 1)
            return f"{name}遇到了一只小怪物，经过一番搏斗获得了经验！(HP-5, ATK+1)"

    elif event_id == "homesick":
        state.apply_stat_change("LCK", 1)
        session.mood = min(100, session.mood + 10)
        return f"{name}看到你的回复，开心地摇起了尾巴！(LCK+1, 心情+10)"

    elif event_id == "npc_help":
        roll = random.random()
        if roll < 0.5:
            item = Item(name="感恩礼物", description="NPC送的谢礼",
                        rarity="uncommon", effect="HP+15")
            state.add_item(item)
            return f"NPC感激地送了{name}一份礼物！获得 [{item.rarity}] {item.name}"
        else:
            state.add_tickets(3)
            return f"NPC感激不尽，赠送了珍贵的抽奖券！(+3 抽奖券)"

    elif event_id == "weather":
        return f"{name}找到了一个安全的洞穴，平安度过了暴风雨！"

    elif event_id == "dream":
        tickets = random.randint(3, 5)
        state.add_tickets(tickets)
        return f"{name}跟随梦境的指引，发现了一个隐藏的宝藏！(+{tickets} 抽奖券)"

    elif event_id == "gift":
        item = Item(name="小玩意儿", description=f"{name}找到的小宝贝",
                    rarity="common", effect=random.choice(["HP+5", "LCK+1"]))
        state.add_item(item)
        return f"你收下了{name}的礼物：{item.name}！({item.effect})"

    return ""


def apply_negative_result(event_id: str, session: GameSession) -> str:
    """Apply timeout/negative outcome. Returns description text."""
    from . import state
    name = session.companion_name

    if event_id == "hurt":
        return _pray_to_god(session)

    elif event_id == "direction":
        if session.location and session.location.connections:
            dest = random.choice(session.location.connections)
            return f"{name}等不到回答，随机选择了去 {dest}... 希望不会有危险。"
        return f"{name}在原地徘徊了一会儿。"

    elif event_id == "hungry":
        session.mood = max(0, session.mood - 5)
        return f"{name}饿着肚子继续走了... 下次探索的效率可能会降低。(心情-5)"

    elif event_id == "treasure":
        return f"没人回应...宝箱缓缓沉入了地面，消失了。"

    elif event_id == "noise":
        return f"{name}决定不去调查，声音渐渐远去了。也许错过了什么好东西。"

    elif event_id == "homesick":
        session.mood = max(0, session.mood - 10)
        return f"{name}叹了口气，继续独自前行了。(心情-10)"

    elif event_id == "npc_help":
        return f"NPC等了一会儿，摇摇头离开了。{name}错过了这次机会。"

    elif event_id == "weather":
        state.apply_stat_change("HP", -10)
        return f"{name}没来得及躲避，被暴风雨淋了个透！(HP-10)"

    elif event_id == "dream":
        return f"{name}醒来后，梦境的记忆如同晨雾般散去了... 线索消失了。"

    elif event_id == "gift":
        return f"{name}把玩着那个小东西，一不小心弄丢了... 它有点沮丧。"

    return ""


def _pray_to_god(session: GameSession) -> str:
    """Buddy prays to God for healing — with random penalties."""
    from . import state
    name = session.companion_name
    roll = random.random()

    if roll < 0.10 and session.inventory:
        lost = state.remove_random_item()
        if lost:
            state.apply_stat_change("HP", 10)
            return (f"{name}向天空祈祷... God治愈了它，"
                    f"但 {lost.name} 作为祭品消失了。(HP+10, 失去{lost.name})")

    if roll < 0.20 and session.skills:
        lost = state.remove_random_skill()
        if lost:
            state.apply_stat_change("HP", 10)
            return (f"{name}向天空祈祷... God治愈了它，"
                    f"但{name}忘记了 {lost.name}。(HP+10, 失去技能{lost.name})")

    if roll < 0.50:
        stat = random.choice(list(GAME_STAT_NAMES))
        if stat != "HP":
            amount = random.randint(1, 3)
            state.apply_stat_change(stat, -amount)
            state.apply_stat_change("HP", 10)
            return (f"{name}向天空祈祷... God治愈了它，"
                    f"但{name}的 {stat} 下降了 {amount} 点。(HP+10, {stat}-{amount})")

    # 50%: free heal
    state.apply_stat_change("HP", 15)
    return f"God怜悯了{name}，免费施予了治愈之光！(HP+15)"
