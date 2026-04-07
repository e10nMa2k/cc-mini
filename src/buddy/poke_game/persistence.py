"""Idle Adventure persistence — save/load tickets and badges between sessions.

Only tickets and badges persist across runs. Everything else resets.
Storage: ~/.config/mini-claude/companion_loot.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .types import GameSession, GAME_STAT_NAMES
from .badges import ALL_BADGES

_CONFIG_DIR = Path.home() / ".config" / "mini-claude"
_LOOT_FILE = _CONFIG_DIR / "companion_loot.json"

# Regex to parse badge effects like "HP+5", "ATK+3,DEF+3", "全属性+3"
_EFFECT_RE = re.compile(r"(HP|ATK|DEF|SPD|LCK|全属性)\+(\d+)")


def load_loot() -> dict:
    """Load persisted data from disk."""
    if not _LOOT_FILE.exists():
        return {"tickets": 0, "badges": [], "total_runs": 0}
    try:
        data = json.loads(_LOOT_FILE.read_text(encoding="utf-8"))
        data.setdefault("tickets", 0)
        data.setdefault("badges", [])
        data.setdefault("total_runs", 0)
        return data
    except (json.JSONDecodeError, TypeError):
        return {"tickets": 0, "badges": [], "total_runs": 0}


def save_loot(loot: dict) -> None:
    """Save data to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _LOOT_FILE.write_text(json.dumps(loot, indent=2, ensure_ascii=False), encoding="utf-8")


def save_session(session: GameSession) -> None:
    """Save tickets and badges at end of session. Everything else is discarded."""
    loot = load_loot()
    loot["tickets"] = session.tickets
    loot["badges"] = [b.badge_id for b in session.badges]
    loot["total_runs"] = loot.get("total_runs", 0) + 1
    save_loot(loot)


def restore_from_loot(session: GameSession) -> None:
    """Restore banked tickets, owned badges, and badge stat bonuses."""
    loot = load_loot()
    session.tickets = loot.get("tickets", 0)
    for badge_id in loot.get("badges", []):
        badge = ALL_BADGES.get(badge_id)
        if badge and badge_id not in {b.badge_id for b in session.badges}:
            session.badges.append(badge)

    # Apply all badge passive effects to initial stats
    for badge in session.badges:
        for stat, amount in _parse_effect(badge.effect):
            if stat in session.stats:
                session.stats[stat] += amount


def _parse_effect(effect: str) -> list[tuple[str, int]]:
    """Parse badge effect string into [(stat, amount), ...].

    Examples:
      "HP+5"          → [("HP", 5)]
      "ATK+3,DEF+3"   → [("ATK", 3), ("DEF", 3)]
      "全属性+3"       → [("HP",3),("ATK",3),("DEF",3),("SPD",3),("LCK",3)]
    """
    results = []
    for match in _EFFECT_RE.finditer(effect):
        stat_name = match.group(1)
        amount = int(match.group(2))
        if stat_name == "全属性":
            for s in GAME_STAT_NAMES:
                results.append((s, amount))
        else:
            results.append((stat_name, amount))
    return results
