"""Tests for buddy mood system."""
from buddy.mood import apply_decay, apply_events, classify_events, describe_mood
from buddy.storage import (
    load_active_mood,
    save_active_mood,
    save_stored_companion,
)
from buddy.types import CompanionMood, MOOD_DIMENSIONS, MOOD_NEUTRAL, CompanionSoul


# ---------------------------------------------------------------------------
# CompanionMood dataclass
# ---------------------------------------------------------------------------


class TestCompanionMood:
    def test_default_neutral(self):
        mood = CompanionMood()
        for dim in MOOD_DIMENSIONS:
            assert getattr(mood, dim) == MOOD_NEUTRAL

    def test_to_dict_round_trip(self):
        mood = CompanionMood(happy=75, bored=30, curious=80, last_updated=12345)
        d = mood.to_dict()
        restored = CompanionMood.from_dict(d)
        assert restored.happy == 75
        assert restored.bored == 30
        assert restored.curious == 80
        assert restored.last_updated == 12345

    def test_from_dict_missing_keys(self):
        mood = CompanionMood.from_dict({})
        assert mood.happy == MOOD_NEUTRAL
        assert mood.last_updated == 0

    def test_dominant_returns_furthest_from_neutral(self):
        mood = CompanionMood(happy=90, grumpy=10)
        # Both are 40 away from neutral; happy comes first in MOOD_DIMENSIONS
        assert mood.dominant() in ('happy', 'grumpy')

    def test_dominant_all_neutral(self):
        mood = CompanionMood()
        # All at 50, dominant defaults to first dimension
        assert mood.dominant() == 'happy'


# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------


class TestClassifyEvents:
    def test_success_keywords(self):
        events = classify_events("Done! File created successfully.", "")
        assert 'task_success' in events

    def test_error_keywords(self):
        events = classify_events("Error: traceback in module", "")
        assert 'error' in events

    def test_exploration_keywords(self):
        events = classify_events("Reading file src/main.py", "")
        assert 'exploration' in events

    def test_long_text(self):
        events = classify_events("x" * 2001, "")
        assert 'long_text' in events

    def test_no_events_for_generic_text(self):
        events = classify_events("Here is some information.", "")
        assert events == []

    def test_case_insensitive(self):
        events = classify_events("FIXED the issue", "")
        assert 'task_success' in events

    def test_multiple_events(self):
        events = classify_events("Error found while searching files", "")
        assert 'error' in events
        assert 'exploration' in events


# ---------------------------------------------------------------------------
# Apply events
# ---------------------------------------------------------------------------


class TestApplyEvents:
    def test_pet_boosts_happy(self):
        mood = CompanionMood()
        updated = apply_events(mood, ['pet'])
        assert updated.happy > MOOD_NEUTRAL
        assert updated.grumpy < MOOD_NEUTRAL

    def test_error_boosts_grumpy(self):
        mood = CompanionMood()
        updated = apply_events(mood, ['error'])
        assert updated.grumpy > MOOD_NEUTRAL
        assert updated.happy < MOOD_NEUTRAL

    def test_clamp_upper(self):
        mood = CompanionMood(happy=98)
        updated = apply_events(mood, ['pet'])
        assert updated.happy <= 100

    def test_clamp_lower(self):
        mood = CompanionMood(grumpy=2)
        updated = apply_events(mood, ['pet'])
        assert updated.grumpy >= 0

    def test_multiple_events_stack(self):
        mood = CompanionMood()
        updated = apply_events(mood, ['task_success', 'exploration'])
        assert updated.happy > MOOD_NEUTRAL
        assert updated.curious > MOOD_NEUTRAL

    def test_unknown_event_ignored(self):
        mood = CompanionMood()
        updated = apply_events(mood, ['unknown_event'])
        assert updated.happy == MOOD_NEUTRAL

    def test_preserves_last_updated(self):
        mood = CompanionMood(last_updated=999)
        updated = apply_events(mood, ['pet'])
        assert updated.last_updated == 999


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------


class TestMoodDecay:
    def test_no_decay_on_first_update(self):
        mood = CompanionMood(happy=80, last_updated=0)
        decayed = apply_decay(mood, 1000)
        assert decayed.happy == 80
        assert decayed.last_updated == 1000

    def test_decay_toward_neutral(self):
        mood = CompanionMood(happy=80, grumpy=20, last_updated=1000)
        now = 1000 + 10 * 60_000  # 10 minutes later
        decayed = apply_decay(mood, now)
        assert decayed.happy < 80
        assert decayed.happy >= MOOD_NEUTRAL
        assert decayed.grumpy > 20
        assert decayed.grumpy <= MOOD_NEUTRAL

    def test_no_overshoot_above(self):
        mood = CompanionMood(happy=55, last_updated=1000)
        now = 1000 + 60 * 60_000  # 60 minutes — more than enough to reach 50
        decayed = apply_decay(mood, now)
        assert decayed.happy == MOOD_NEUTRAL

    def test_no_overshoot_below(self):
        mood = CompanionMood(happy=45, last_updated=1000)
        now = 1000 + 60 * 60_000
        decayed = apply_decay(mood, now)
        assert decayed.happy == MOOD_NEUTRAL

    def test_bored_drift_on_idle(self):
        mood = CompanionMood(bored=MOOD_NEUTRAL, last_updated=1000)
        now = 1000 + 30 * 60_000  # 30 minutes idle
        decayed = apply_decay(mood, now)
        assert decayed.bored > MOOD_NEUTRAL

    def test_no_decay_within_same_minute(self):
        mood = CompanionMood(happy=80, last_updated=1000)
        decayed = apply_decay(mood, 1000 + 30_000)  # 30 seconds
        assert decayed.happy == 80


# ---------------------------------------------------------------------------
# Describe mood
# ---------------------------------------------------------------------------


class TestDescribeMood:
    def test_contains_dimensions(self):
        mood = CompanionMood(happy=75, grumpy=20)
        desc = describe_mood(mood)
        assert 'happy=75' in desc
        assert 'grumpy=20' in desc

    def test_contains_dominant(self):
        mood = CompanionMood(curious=90)
        desc = describe_mood(mood)
        assert 'CURIOUS' in desc


# ---------------------------------------------------------------------------
# Mood persistence
# ---------------------------------------------------------------------------


class TestMoodStorage:
    def test_round_trip(self, tmp_path):
        fp = tmp_path / "companion.json"
        soul = CompanionSoul(name="Test", personality="Testing")
        save_stored_companion(soul, path=fp)

        mood = CompanionMood(happy=75, curious=80, last_updated=12345)
        save_active_mood(mood, path=fp)

        loaded = load_active_mood(path=fp)
        assert loaded.happy == 75
        assert loaded.curious == 80
        assert loaded.last_updated == 12345

    def test_missing_mood_returns_neutral(self, tmp_path):
        fp = tmp_path / "companion.json"
        soul = CompanionSoul(name="Test", personality="Testing")
        save_stored_companion(soul, path=fp)

        loaded = load_active_mood(path=fp)
        assert loaded.happy == MOOD_NEUTRAL

    def test_missing_file_returns_neutral(self, tmp_path):
        fp = tmp_path / "nonexistent.json"
        loaded = load_active_mood(path=fp)
        assert loaded.happy == MOOD_NEUTRAL

    def test_mood_preserves_companion_data(self, tmp_path):
        fp = tmp_path / "companion.json"
        soul = CompanionSoul(name="Ghost", personality="Spooky")
        save_stored_companion(soul, path=fp)
        save_active_mood(CompanionMood(happy=75), path=fp)

        from buddy.storage import load_stored_companion
        loaded = load_stored_companion(path=fp)
        assert loaded is not None
        assert loaded.name == "Ghost"
