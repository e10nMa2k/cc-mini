"""Tests for buddy persistence (Phase 2)."""
import json

from buddy.storage import (
    load_companion_muted,
    load_stored_companion,
    save_companion_muted,
    save_stored_companion,
)
from buddy.types import CompanionSoul


class TestStorage:
    def test_round_trip(self, tmp_path):
        fp = tmp_path / "companion.json"
        soul = CompanionSoul(name="Quackers", personality="A chaotic duck")
        stored = save_stored_companion(soul, path=fp)
        assert stored.name == "Quackers"
        assert stored.personality == "A chaotic duck"
        assert stored.hatched_at > 0

        loaded = load_stored_companion(path=fp)
        assert loaded is not None
        assert loaded.name == stored.name
        assert loaded.personality == stored.personality
        assert loaded.hatched_at == stored.hatched_at

    def test_missing_file(self, tmp_path):
        fp = tmp_path / "nonexistent.json"
        assert load_stored_companion(path=fp) is None

    def test_corrupt_json(self, tmp_path):
        fp = tmp_path / "companion.json"
        fp.write_text("not valid json {{{", encoding="utf-8")
        assert load_stored_companion(path=fp) is None

    def test_missing_fields(self, tmp_path):
        fp = tmp_path / "companion.json"
        fp.write_text('{"name": "Bob"}', encoding="utf-8")
        assert load_stored_companion(path=fp) is None


class TestMuted:
    def test_default_muted(self, tmp_path):
        fp = tmp_path / "companion.json"
        assert load_companion_muted(path=fp) is True

    def test_mute_toggle(self, tmp_path):
        fp = tmp_path / "companion.json"
        soul = CompanionSoul(name="Ghost", personality="Spooky")
        save_stored_companion(soul, path=fp)

        assert load_companion_muted(path=fp) is True
        save_companion_muted(False, path=fp)
        assert load_companion_muted(path=fp) is False
        save_companion_muted(True, path=fp)
        assert load_companion_muted(path=fp) is True

    def test_mute_preserves_data(self, tmp_path):
        fp = tmp_path / "companion.json"
        soul = CompanionSoul(name="Ghost", personality="Spooky")
        save_stored_companion(soul, path=fp)
        save_companion_muted(True, path=fp)

        loaded = load_stored_companion(path=fp)
        assert loaded is not None
        assert loaded.name == "Ghost"
