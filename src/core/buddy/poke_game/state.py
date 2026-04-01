"""In-memory game session state — module-level singleton.

The session lives only as long as the terminal process.
"""
from __future__ import annotations

from .types import GameSession, Item, Skill, Badge, INITIAL_STATS

_current_session: GameSession | None = None


def new_session(
    companion_name: str,
    companion_species: str,
    companion_eye: str,
    companion_hat: str,
) -> GameSession:
    """Create a fresh game session."""
    global _current_session
    _current_session = GameSession(
        companion_name=companion_name,
        companion_species=companion_species,
        companion_eye=companion_eye,
        companion_hat=companion_hat,
        stats=dict(INITIAL_STATS),
    )
    return _current_session


def get_session() -> GameSession | None:
    return _current_session


def end_session() -> GameSession | None:
    """Mark session inactive and return it for persistence."""
    global _current_session
    s = _current_session
    if s:
        s.active = False
    _current_session = None
    return s


# ---------------------------------------------------------------------------
# Convenience mutators
# ---------------------------------------------------------------------------

def apply_stat_change(stat: str, amount: int) -> int:
    """Apply a stat change. Returns new value. HP floor is 0."""
    s = _current_session
    if not s or stat not in s.stats:
        return 0
    s.stats[stat] = max(0, s.stats[stat] + amount)
    return s.stats[stat]


def add_item(item: Item) -> None:
    if _current_session:
        _current_session.inventory.append(item)


def remove_random_item() -> Item | None:
    s = _current_session
    if not s or not s.inventory:
        return None
    import random
    item = random.choice(s.inventory)
    s.inventory.remove(item)
    return item


def add_skill(skill: Skill) -> None:
    if _current_session:
        _current_session.skills.append(skill)


def remove_random_skill() -> Skill | None:
    s = _current_session
    if not s or not s.skills:
        return None
    import random
    skill = random.choice(s.skills)
    s.skills.remove(skill)
    return skill


def add_badge(badge: Badge) -> None:
    if _current_session:
        _current_session.badges.append(badge)


def add_tickets(amount: int) -> None:
    if _current_session:
        _current_session.tickets += amount


def spend_tickets(amount: int) -> bool:
    s = _current_session
    if not s or s.tickets < amount:
        return False
    s.tickets -= amount
    return True


def append_log(entry: str) -> None:
    if _current_session:
        _current_session.adventure_log.append(entry)


def is_alive() -> bool:
    s = _current_session
    return s is not None and s.stats.get("HP", 0) > 0
