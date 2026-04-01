"""Idle Adventure (IA) type definitions and constants.

All game data types, RPG stats, and probability tables.
Game stats are independent of buddy's original DEBUGGING/PATIENCE/CHAOS/WISDOM/SNARK.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Game RPG stats (independent from buddy original stats)
# ---------------------------------------------------------------------------

GAME_STAT_NAMES = ("HP", "ATK", "DEF", "SPD", "LCK")

INITIAL_STATS: dict[str, int] = {
    "HP": 100,
    "ATK": 10,
    "DEF": 10,
    "SPD": 10,
    "LCK": 10,
}

MAX_STAT_BOOST = 50  # per-stat persistent boost cap

# ---------------------------------------------------------------------------
# Roguelike save probabilities
# ---------------------------------------------------------------------------

SAVE_PROBABILITY: dict[str, float] = {
    "common": 0.40,
    "uncommon": 0.30,
    "rare": 0.20,
    "epic": 0.10,
    "legendary": 0.05,
}

BATTLE_LOOT_MULTIPLIER = 2.0

# ---------------------------------------------------------------------------
# Ticket economy
# ---------------------------------------------------------------------------

TICKET_COST = 5
EXPLORE_TICKETS_MIN = 1
EXPLORE_TICKETS_MAX = 3

# ---------------------------------------------------------------------------
# Badge tiers
# ---------------------------------------------------------------------------

BADGE_TIERS = ("green", "purple", "red", "gold")

DRAW_PROBABILITY: dict[str, float] = {
    "green": 0.60,
    "purple": 0.25,
    "red": 0.10,
    "gold": 0.05,
}

DUPLICATE_REFUND: dict[str, int] = {
    "green": 3,
    "purple": 8,
    "red": 20,
    "gold": 50,
}

BADGE_COLORS: dict[str, str] = {
    "green": "green",
    "purple": "magenta",
    "red": "red",
    "gold": "yellow",
}

# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

ELEMENTS = ("fire", "water", "earth", "wind", "shadow", "light")

# ---------------------------------------------------------------------------
# Item rarities (shared with buddy system)
# ---------------------------------------------------------------------------

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Item:
    name: str
    description: str
    rarity: str  # common/uncommon/rare/epic/legendary
    effect: str  # e.g. "HP+20", "ATK+3"
    from_battle: bool = False


@dataclass
class Skill:
    name: str
    description: str
    power: int  # 1-100
    element: str  # fire/water/earth/wind/shadow/light
    from_battle: bool = False


@dataclass
class Badge:
    badge_id: str  # "green_01" .. "gold_02"
    name: str
    description: str
    tier: str  # green/purple/red/gold
    effect: str  # passive effect description


@dataclass
class NPC:
    name: str
    species: str
    personality: str
    disposition: str  # friendly/neutral/hostile
    greeting: str = ""  # what they say when you meet them
    gifts: list[dict] = field(default_factory=list)  # possible items to give
    secrets: list[str] = field(default_factory=list)  # world lore snippets
    # Probabilities for encounter outcomes
    gift_chance: float = 0.30  # chance to give an item
    ignore_chance: float = 0.30  # chance to ignore you
    secret_chance: float = 0.40  # chance to tell a secret


@dataclass
class Monster:
    name: str
    species: str
    hp: int
    atk: int
    defense: int  # 'def' is reserved
    spd: int
    element: str  # fire/water/earth/wind/shadow/light
    level: int  # 1-10, affects rewards
    description: str = ""


@dataclass
class Location:
    name: str
    region: str
    description: str
    connections: list[str] = field(default_factory=list)
    event_weights: dict[str, float] = field(default_factory=dict)
    ticket_bonus: int = 0  # extra tickets from this location (0-2)


@dataclass
class GameSession:
    # Buddy identity (appearance only, no original stats)
    companion_name: str
    companion_species: str
    companion_eye: str
    companion_hat: str
    # Game RPG stats
    stats: dict[str, int] = field(default_factory=lambda: dict(INITIAL_STATS))
    # Current state
    location: Location | None = None
    inventory: list[Item] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    badges: list[Badge] = field(default_factory=list)
    tickets: int = 0
    # Log & history
    adventure_log: list[str] = field(default_factory=list)
    summary_history: str = ""
    # Counters
    turn_count: int = 0
    mood: int = 80  # 0-100
    # Flags
    active: bool = True
    # Exploration counts per location (for guaranteed-event first 3 visits)
    explore_counts: dict[str, int] = field(default_factory=dict)
    # Tracks unique locations visited since leaving each location
    # Used to determine when explore_counts should reset (need >=3 other locations)
    locations_since_left: dict[str, set] = field(default_factory=dict)

# Number of guaranteed-event explorations per location
GUARANTEED_EXPLORE_COUNT = 3
# Event probability after guaranteed explorations are used up
POST_GUARANTEE_EVENT_CHANCE = 0.05
