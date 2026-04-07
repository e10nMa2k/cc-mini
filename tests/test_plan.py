"""Tests for plan mode — path isolation and basic lifecycle."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from core.plan import PlanModeManager, _get_plans_dir


class TestPlanDir:
    """Ensure plan files are stored under ~/.mini-claude, not ~/.claude."""

    def test_plans_dir_uses_mini_claude(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch.object(Path, "home", return_value=fake_home):
            plans_dir = _get_plans_dir()

        assert ".mini-claude" in plans_dir.parts
        assert ".claude" not in plans_dir.parts
        assert plans_dir == fake_home / ".mini-claude" / "plans"
        assert plans_dir.exists()

    def test_plans_dir_does_not_create_dot_claude(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch.object(Path, "home", return_value=fake_home):
            _get_plans_dir()

        dot_claude = fake_home / ".claude"
        assert not dot_claude.exists(), (
            "~/.claude should not be created by cc-mini"
        )


class TestPlanModeManager:
    """Basic enter/exit lifecycle."""

    def _make_engine_mock(self):
        engine = MagicMock()
        engine._tools = {}
        engine.system_prompt = "base prompt"
        return engine

    def test_enter_creates_plan_file_under_mini_claude(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        manager = PlanModeManager()
        manager.bind_engine(self._make_engine_mock())

        with patch.object(Path, "home", return_value=fake_home):
            result = manager.enter()

        assert manager.is_active
        assert ".mini-claude" in result
        assert ".claude/plans" not in result

    def test_exit_restores_state(self, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        engine = self._make_engine_mock()
        manager = PlanModeManager()
        manager.bind_engine(engine)

        with patch.object(Path, "home", return_value=fake_home):
            manager.enter()
            msg, content = manager.exit()

        assert not manager.is_active
        assert "Exited plan mode" in msg or "approved" in msg.lower()
