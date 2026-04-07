"""Game command parser — rule-based, Chinese/English bilingual.

All commands are hardcoded. This module only parses and dispatches;
actual logic lives in loop.py and other modules.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from .types import GameSession

# ---------------------------------------------------------------------------
# Command aliases (Chinese → canonical English)
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # Movement
    "go": "go", "前往": "go", "去": "go", "move": "go",
    # Look
    "look": "look", "观察": "look", "查看": "look",
    # Explore
    "explore": "explore", "探索": "explore",
    # Talk
    "talk": "talk", "对话": "talk", "说话": "talk", "chat": "talk",
    # Use item
    "use": "use", "使用": "use",
    # Draw gacha
    "draw": "draw", "抽卡": "draw", "抽奖": "draw",
    # Inventory
    "bag": "bag", "背包": "bag", "inventory": "bag",
    # Skills
    "skills": "skills", "技能": "skills",
    # Stats
    "stats": "stats", "属性": "stats", "status": "stats",
    # Badges
    "badges": "badges", "徽章": "badges",
    # Map
    "map": "map", "地图": "map",
    # Rest
    "rest": "rest", "休息": "rest",
    # Help
    "help": "help", "帮助": "help",
    # Quit
    "quit": "quit", "退出": "quit", "exit": "quit",
}

# Special phrase triggers
_BATTLE_TRIGGERS = ["让我们去战斗吧", "let's battle", "battle", "fight", "战斗"]

# Commands with descriptions for autocomplete (shown to user)
COMMAND_HINTS: list[tuple[str, str]] = [
    ("explore",  "探索当前位置"),
    ("go",       "前往其他地点"),
    ("look",     "查看当前位置"),
    ("talk",     "和伙伴聊天"),
    ("use",      "使用道具"),
    ("draw",     "抽卡(5券)"),
    ("bag",      "查看背包"),
    ("skills",   "查看技能"),
    ("stats",    "查看属性"),
    ("badges",   "查看徽章"),
    ("map",      "查看地图"),
    ("rest",     "休息恢复HP"),
    ("help",     "帮助"),
    ("quit",     "退出游戏"),
]


def parse_game_command(raw: str) -> tuple[str, str]:
    """Parse raw input into (canonical_command, args).

    Returns ("unknown", raw) if not recognized.
    Returns ("battle", "") for battle triggers.
    """
    text = raw.strip()
    if not text:
        return ("empty", "")

    # Check battle triggers
    for trigger in _BATTLE_TRIGGERS:
        if trigger in text.lower():
            return ("battle", "")

    # Split into command + args
    parts = text.split(None, 1)
    cmd_word = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    canonical = _ALIASES.get(cmd_word)
    if canonical:
        return (canonical, args)

    # Try matching the whole input as an alias (for single-word Chinese commands)
    canonical = _ALIASES.get(text)
    if canonical:
        return (canonical, "")

    return ("unknown", text)


# ---------------------------------------------------------------------------
# prompt_toolkit completer
# ---------------------------------------------------------------------------

class GameCompleter(Completer):
    """Dynamic completer that suggests commands + contextual args."""

    def __init__(self, session_getter: Any = None):
        self._session_getter = session_getter

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        parts = text.split(None, 1)

        if len(parts) <= 1:
            # Completing the command itself
            query = text.lower()
            for cmd, desc in COMMAND_HINTS:
                if cmd.startswith(query):
                    yield Completion(cmd, start_position=-len(text),
                                     display_meta=desc)
            # Also suggest Chinese aliases
            for alias, canonical in _ALIASES.items():
                if alias == canonical:
                    continue  # skip English duplicates
                if alias.startswith(query) and query:
                    desc = next((d for c, d in COMMAND_HINTS if c == canonical), "")
                    yield Completion(alias, start_position=-len(text),
                                     display_meta=desc)
        else:
            # Completing arguments
            cmd_word = parts[0]
            arg_text = parts[1] if len(parts) > 1 else ""
            canonical = _ALIASES.get(cmd_word, "")

            session = self._session_getter() if self._session_getter else None
            if not session:
                return

            if canonical == "go":
                # Suggest connected locations
                if session.location:
                    for loc_name in session.location.connections:
                        if loc_name.startswith(arg_text) or not arg_text:
                            yield Completion(loc_name, start_position=-len(arg_text))

            elif canonical == "use":
                # Suggest inventory items
                for item in session.inventory:
                    if item.name.startswith(arg_text) or not arg_text:
                        yield Completion(item.name, start_position=-len(arg_text),
                                         display_meta=item.effect)


# ---------------------------------------------------------------------------
# Bottom toolbar
# ---------------------------------------------------------------------------

def game_toolbar(session_getter: Any) -> str:
    """Generate bottom toolbar text showing key info."""
    session = session_getter() if session_getter else None
    if not session:
        return ""
    hp = session.stats.get("HP", 0)
    hp_color = "ansired" if hp < 30 else ("ansiyellow" if hp < 60 else "ansigreen")
    return (
        f" HP:{hp} | ATK:{session.stats.get('ATK',0)} "
        f"DEF:{session.stats.get('DEF',0)} SPD:{session.stats.get('SPD',0)} "
        f"LCK:{session.stats.get('LCK',0)} | "
        f"券:{session.tickets} | "
        f"徽章:{len(session.badges)}/32 | "
        f"回合:{session.turn_count}"
    )


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

HELP_TEXT = """\
[bold]可用命令:[/bold]
  [cyan]explore[/cyan] / 探索     在当前位置探索（获得道具、技能、抽奖券）
  [cyan]go[/cyan] <地点> / 前往    移动到相连的地点
  [cyan]look[/cyan] / 观察        查看当前位置详情
  [cyan]talk[/cyan] / 对话           和你的伙伴聊聊天
  [cyan]use[/cyan] <物品> / 使用    使用背包中的道具
  [cyan]draw[/cyan] / 抽卡         消耗5张抽奖券抽取徽章
  [cyan]bag[/cyan] / 背包          查看物品列表
  [cyan]skills[/cyan] / 技能       查看已学技能
  [cyan]stats[/cyan] / 属性        查看HP/ATK/DEF/SPD/LCK
  [cyan]badges[/cyan] / 徽章       查看已收集的徽章 (进度X/32)
  [cyan]map[/cyan] / 地图          显示世界地图
  [cyan]rest[/cyan] / 休息         休息恢复HP (消耗1回合)
  [cyan]help[/cyan] / 帮助         显示此帮助
  [cyan]quit[/cyan] / 退出         结束游戏并保存\
"""
