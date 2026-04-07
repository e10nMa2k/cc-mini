"""Buddy type definitions and constants.

Port of claude-code-main/src/buddy/types.ts
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Rarities
# ---------------------------------------------------------------------------

RARITIES = ('common', 'uncommon', 'rare', 'epic', 'legendary')

RARITY_WEIGHTS: dict[str, int] = {
    'common': 60,
    'uncommon': 25,
    'rare': 10,
    'epic': 4,
    'legendary': 1,
}

RARITY_STARS: dict[str, str] = {
    'common': '\u2605',
    'uncommon': '\u2605\u2605',
    'rare': '\u2605\u2605\u2605',
    'epic': '\u2605\u2605\u2605\u2605',
    'legendary': '\u2605\u2605\u2605\u2605\u2605',
}

# Mapped to rich style names (original uses theme keys)
RARITY_COLORS: dict[str, str] = {
    'common': 'dim',
    'uncommon': 'green',
    'rare': 'blue',
    'epic': 'magenta',
    'legendary': 'yellow',
}

RARITY_FLOOR: dict[str, int] = {
    'common': 5,
    'uncommon': 15,
    'rare': 25,
    'epic': 35,
    'legendary': 50,
}

# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------

SPECIES = (
    'duck', 'goose', 'blob', 'cat', 'dragon', 'octopus', 'owl', 'penguin',
    'turtle', 'snail', 'ghost', 'axolotl', 'capybara', 'cactus', 'robot',
    'rabbit', 'mushroom', 'chonk',
)

# Bonus species — only available via CC_MINI_BUDDY_SEED, not in random pool
BONUS_SPECIES = ('pikachu',)
ALL_SPECIES = SPECIES + BONUS_SPECIES

# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------

EYES = ('\u00b7', '\u2726', '\u00d7', '\u25c9', '@', '\u00b0')
# ·  ✦  ×  ◉  @  °

HATS = ('none', 'crown', 'tophat', 'propeller', 'halo', 'wizard', 'beanie', 'tinyduck')

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

STAT_NAMES = ('DEBUGGING', 'PATIENCE', 'CHAOS', 'WISDOM', 'SNARK')

# ---------------------------------------------------------------------------
# Mood
# ---------------------------------------------------------------------------

MOOD_DIMENSIONS = ('happy', 'bored', 'excited', 'tired', 'grumpy', 'curious')
MOOD_NEUTRAL = 50

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanionBones:
    """Deterministic parts — derived from hash(userId)."""
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CompanionMood:
    """Dynamic mood state — changes based on conversation events."""
    happy: int = MOOD_NEUTRAL
    bored: int = MOOD_NEUTRAL
    excited: int = MOOD_NEUTRAL
    tired: int = MOOD_NEUTRAL
    grumpy: int = MOOD_NEUTRAL
    curious: int = MOOD_NEUTRAL
    last_updated: int = 0  # ms since epoch

    def to_dict(self) -> dict:
        return {
            'happy': self.happy, 'bored': self.bored,
            'excited': self.excited, 'tired': self.tired,
            'grumpy': self.grumpy, 'curious': self.curious,
            'lastUpdated': self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'CompanionMood':
        return cls(
            happy=d.get('happy', MOOD_NEUTRAL),
            bored=d.get('bored', MOOD_NEUTRAL),
            excited=d.get('excited', MOOD_NEUTRAL),
            tired=d.get('tired', MOOD_NEUTRAL),
            grumpy=d.get('grumpy', MOOD_NEUTRAL),
            curious=d.get('curious', MOOD_NEUTRAL),
            last_updated=d.get('lastUpdated', 0),
        )

    def dominant(self) -> str:
        """Return the mood dimension furthest from neutral."""
        best_dim = 'happy'
        best_dist = 0
        for dim in MOOD_DIMENSIONS:
            dist = abs(getattr(self, dim) - MOOD_NEUTRAL)
            if dist > best_dist:
                best_dist = dist
                best_dim = dim
        return best_dim


@dataclass(frozen=True)
class CompanionSoul:
    """Model-generated soul — stored in config after first hatch."""
    name: str
    personality: str


@dataclass(frozen=True)
class StoredCompanion:
    """What actually persists on disk."""
    name: str
    personality: str
    hatched_at: int  # ms since epoch


@dataclass(frozen=True)
class StoredCompanionWithSeed(StoredCompanion):
    """Stored companion that also remembers the seed used to generate bones."""
    seed: str = ''


@dataclass(frozen=True)
class Companion:
    """Full companion = bones + soul + metadata."""
    # Bones
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]
    # Soul
    name: str
    personality: str
    # Metadata
    hatched_at: int
    # Mood
    mood: CompanionMood = field(default_factory=CompanionMood)
