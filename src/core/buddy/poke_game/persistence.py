"""Roguelike persistence — save/load loot between runs.

Storage: ~/.config/mini-claude/companion_loot.json
Adventure logs: ~/.config/mini-claude/adventure_logs/{timestamp}.txt

Badges are 100% kept. Items/skills use probability-based retention.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .types import (
    Badge,
    GameSession,
    Item,
    Skill,
    SAVE_PROBABILITY,
    BATTLE_LOOT_MULTIPLIER,
    MAX_STAT_BOOST,
)
from .badges import ALL_BADGES

_CONFIG_DIR = Path.home() / ".config" / "mini-claude"
_LOOT_FILE = _CONFIG_DIR / "companion_loot.json"
_LOG_DIR = _CONFIG_DIR / "adventure_logs"


def load_loot() -> dict:
    """Load persisted loot from disk."""
    if not _LOOT_FILE.exists():
        return {
            "items": [],
            "skills": [],
            "stat_boosts": {},
            "badges": [],
            "tickets_banked": 0,
            "total_runs": 0,
            "battle_wins": 0,
        }
    try:
        data = json.loads(_LOOT_FILE.read_text(encoding="utf-8"))
        # Ensure all keys exist
        data.setdefault("items", [])
        data.setdefault("skills", [])
        data.setdefault("stat_boosts", {})
        data.setdefault("badges", [])
        data.setdefault("tickets_banked", 0)
        data.setdefault("total_runs", 0)
        data.setdefault("battle_wins", 0)
        return data
    except (json.JSONDecodeError, TypeError):
        return {
            "items": [], "skills": [], "stat_boosts": {},
            "badges": [], "tickets_banked": 0, "total_runs": 0, "battle_wins": 0,
        }


def save_loot(loot: dict) -> None:
    """Save loot to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _LOOT_FILE.write_text(json.dumps(loot, indent=2, ensure_ascii=False), encoding="utf-8")


def roguelike_save(session: GameSession) -> dict:
    """Perform roguelike save at game end.

    Returns dict of what was actually saved (for display).
    """
    loot = load_loot()
    saved = {"items": [], "skills": [], "stat_changes": {}, "badges": [], "tickets": 0}

    # Items — probability based
    for item in session.inventory:
        prob = SAVE_PROBABILITY.get(item.rarity, 0.20)
        if item.from_battle:
            prob = min(prob * BATTLE_LOOT_MULTIPLIER, 1.0)
        if random.random() < prob:
            loot["items"].append({
                "name": item.name, "rarity": item.rarity,
                "effect": item.effect, "from_battle": item.from_battle,
                "description": item.description,
            })
            saved["items"].append(item)

    # Skills — probability based
    for skill in session.skills:
        # Skill rarity approximated from power
        if skill.power >= 80:
            rarity = "legendary"
        elif skill.power >= 60:
            rarity = "epic"
        elif skill.power >= 40:
            rarity = "rare"
        elif skill.power >= 20:
            rarity = "uncommon"
        else:
            rarity = "common"
        prob = SAVE_PROBABILITY.get(rarity, 0.20)
        if skill.from_battle:
            prob = min(prob * BATTLE_LOOT_MULTIPLIER, 1.0)
        if random.random() < prob:
            loot["skills"].append({
                "name": skill.name, "power": skill.power,
                "element": skill.element, "from_battle": skill.from_battle,
                "description": skill.description,
            })
            saved["skills"].append(skill)

    # Badges — 100% kept
    existing_badges = set(loot["badges"])
    for badge in session.badges:
        if badge.badge_id not in existing_badges:
            loot["badges"].append(badge.badge_id)
            saved["badges"].append(badge)
            existing_badges.add(badge.badge_id)

    # Tickets — keep 50%
    banked = session.tickets // 2
    loot["tickets_banked"] = loot.get("tickets_banked", 0) + banked
    saved["tickets"] = banked

    # Stat boosts — not persisted to loot (game-session only)
    # But we track cumulative stat boosts with a cap
    existing_boosts = loot.get("stat_boosts", {})
    for stat, val in session.stats.items():
        from .types import INITIAL_STATS
        gain = val - INITIAL_STATS.get(stat, 0)
        if gain > 0:
            # Save a fraction of gains
            keep = max(1, gain // 3)
            old = existing_boosts.get(stat, 0)
            new_val = min(old + keep, MAX_STAT_BOOST)
            if new_val > old:
                existing_boosts[stat] = new_val
                saved["stat_changes"][stat] = new_val - old
    loot["stat_boosts"] = existing_boosts

    # Run counter
    loot["total_runs"] = loot.get("total_runs", 0) + 1

    save_loot(loot)
    return saved


def restore_from_loot(session: GameSession) -> None:
    """Restore persisted badges and banked tickets into a new session."""
    loot = load_loot()

    # Restore badges
    for badge_id in loot.get("badges", []):
        badge = ALL_BADGES.get(badge_id)
        if badge and badge_id not in {b.badge_id for b in session.badges}:
            session.badges.append(badge)

    # Restore banked tickets
    session.tickets += loot.get("tickets_banked", 0)

    # Apply persistent stat boosts
    for stat, amount in loot.get("stat_boosts", {}).items():
        if stat in session.stats:
            session.stats[stat] += amount


def save_adventure_log(session: GameSession) -> Path | None:
    """Save the adventure log to a timestamped file."""
    if not session.adventure_log:
        return None
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = _LOG_DIR / f"{ts}.txt"
    content = f"# {session.companion_name} 的冒险日志\n"
    content += f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    content += "\n\n".join(session.adventure_log)
    path.write_text(content, encoding="utf-8")
    return path
