"""Companion persistence — JSON storage at ~/.config/mini-claude/companion.json

Supports multiple companions. The JSON structure is:
{
  "active": 0,
  "muted": true,
  "companions": [
    {"name": "...", "personality": "...", "hatchedAt": ..., "seed": "..."},
    ...
  ]
}

Old single-companion format (flat object with name/personality/hatchedAt) is
auto-migrated on first read.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .types import CompanionMood, CompanionSoul, StoredCompanion, StoredCompanionWithSeed

_CONFIG_DIR = Path.home() / ".config" / "mini-claude"
_COMPANION_FILE = _CONFIG_DIR / "companion.json"


def _ensure_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_data(path: Path | None = None) -> dict | None:
    """Read and return raw JSON data, or None if missing/corrupt."""
    fp = path or _COMPANION_FILE
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError):
        return None


def _write_data(data: dict, path: Path | None = None) -> None:
    fp = path or _COMPANION_FILE
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _migrate_if_needed(data: dict, default_seed: str, path: Path | None = None) -> dict:
    """Migrate old flat format to new multi-companion format in-place."""
    if "companions" in data:
        return data  # already new format

    # Old format: flat {name, personality, hatchedAt, muted?}
    if "name" not in data:
        return data

    new_data = {
        "active": 0,
        "muted": data.get("muted", True),
        "companions": [
            {
                "name": data["name"],
                "personality": data["personality"],
                "hatchedAt": data["hatchedAt"],
                "seed": default_seed,
            }
        ],
    }
    _write_data(new_data, path)
    return new_data


def _default_seed() -> str:
    """Build the default seed for the original companion (matches companion.py logic)."""
    from .companion import companion_user_id, SALT
    return companion_user_id() + SALT


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_stored_companion(path: Path | None = None) -> StoredCompanion | None:
    """Load the *active* stored companion from disk, or None if not hatched yet."""
    data = _read_data(path)
    if data is None:
        return None
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return None
        c = companions[active]
        return StoredCompanion(
            name=c["name"],
            personality=c["personality"],
            hatched_at=c["hatchedAt"],
        )
    except (KeyError, TypeError, IndexError):
        return None


def load_active_seed(path: Path | None = None) -> str | None:
    """Load the seed of the active companion."""
    data = _read_data(path)
    if data is None:
        return None
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return None
        return companions[active].get("seed", "")
    except (KeyError, TypeError, IndexError):
        return None


def save_stored_companion(
    soul: CompanionSoul, path: Path | None = None
) -> StoredCompanion:
    """Save the companion soul to disk (first companion / original hatch)."""
    fp = path or _COMPANION_FILE
    seed = _default_seed()
    hatched_at = int(time.time() * 1000)
    entry = {
        "name": soul.name,
        "personality": soul.personality,
        "hatchedAt": hatched_at,
        "seed": seed,
    }

    data = _read_data(fp)
    if data and "companions" in data:
        # Append to existing list
        data["companions"].append(entry)
        data["active"] = len(data["companions"]) - 1
    else:
        data = {
            "active": 0,
            "muted": True,
            "companions": [entry],
        }
    _write_data(data, fp)
    return StoredCompanion(
        name=soul.name,
        personality=soul.personality,
        hatched_at=hatched_at,
    )


def save_new_companion(
    soul: CompanionSoul, seed: str, path: Path | None = None
) -> StoredCompanion:
    """Append a new companion to the collection and make it active."""
    fp = path or _COMPANION_FILE
    hatched_at = int(time.time() * 1000)
    entry = {
        "name": soul.name,
        "personality": soul.personality,
        "hatchedAt": hatched_at,
        "seed": seed,
    }

    data = _read_data(fp)
    if data is None:
        data = {"active": 0, "muted": True, "companions": []}
    elif "companions" not in data:
        data = _migrate_if_needed(data, _default_seed(), fp)

    data["companions"].append(entry)
    data["active"] = len(data["companions"]) - 1
    _write_data(data, fp)
    return StoredCompanion(
        name=soul.name,
        personality=soul.personality,
        hatched_at=hatched_at,
    )


def load_all_stored_companions(path: Path | None = None) -> list[StoredCompanionWithSeed]:
    """Load all stored companions."""
    data = _read_data(path)
    if data is None:
        return []
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        result = []
        for c in data.get("companions", []):
            result.append(StoredCompanionWithSeed(
                name=c["name"],
                personality=c["personality"],
                hatched_at=c["hatchedAt"],
                seed=c.get("seed", ""),
            ))
        return result
    except (KeyError, TypeError):
        return []


def load_active_index(path: Path | None = None) -> int:
    """Return the active companion index (0-based)."""
    data = _read_data(path)
    if data is None:
        return 0
    data = _migrate_if_needed(data, _default_seed(), path)
    return data.get("active", 0)


def save_active_index(index: int, path: Path | None = None) -> bool:
    """Set the active companion index. Returns True on success."""
    fp = path or _COMPANION_FILE
    data = _read_data(fp)
    if data is None:
        return False
    data = _migrate_if_needed(data, _default_seed(), fp)
    companions = data.get("companions", [])
    if index < 0 or index >= len(companions):
        return False
    data["active"] = index
    _write_data(data, fp)
    return True


def load_companion_muted(path: Path | None = None) -> bool:
    """Check if companion reactions are muted."""
    data = _read_data(path)
    if data is None:
        return True
    data = _migrate_if_needed(data, _default_seed(), path)
    return bool(data.get("muted", True))


def save_companion_muted(muted: bool, path: Path | None = None) -> None:
    """Toggle the muted flag in the companion file."""
    fp = path or _COMPANION_FILE
    data = _read_data(fp)
    if data is None:
        return
    data = _migrate_if_needed(data, _default_seed(), fp)
    data["muted"] = muted
    _write_data(data, fp)


def load_active_mood(path: Path | None = None) -> CompanionMood:
    """Load mood of the active companion. Returns neutral mood if missing."""
    data = _read_data(path)
    if data is None:
        return CompanionMood()
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return CompanionMood()
        mood_data = companions[active].get("mood")
        if mood_data is None:
            return CompanionMood()
        return CompanionMood.from_dict(mood_data)
    except (KeyError, TypeError, IndexError):
        return CompanionMood()


def save_active_mood(mood: CompanionMood, path: Path | None = None) -> None:
    """Save mood for the active companion."""
    fp = path or _COMPANION_FILE
    data = _read_data(fp)
    if data is None:
        return
    try:
        data = _migrate_if_needed(data, _default_seed(), fp)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return
        companions[active]["mood"] = mood.to_dict()
        _write_data(data, fp)
    except (KeyError, TypeError, IndexError):
        pass
