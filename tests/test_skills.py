"""Tests for the skill system — skills.py, skills_bundled.py, and integrations."""

import tempfile
from pathlib import Path

import pytest

from features.skills import (
    Skill,
    _parse_frontmatter,
    _skill_from_frontmatter,
    build_skills_prompt_section,
    clear_skills,
    discover_skills,
    get_skill,
    list_skills,
    load_skills_from_dir,
    register_skill,
)
from features.skills_bundled import register_bundled_skills


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure each test starts with a clean skill registry."""
    clear_skills()
    yield
    clear_skills()


# -----------------------------------------------------------------------
# Frontmatter parsing
# -----------------------------------------------------------------------

class TestParseFrontmatter:
    def test_full_frontmatter(self):
        meta, body = _parse_frontmatter(
            "---\n"
            "name: my-skill\n"
            "description: A test skill\n"
            "user-invocable: true\n"
            "allowed-tools: Bash, Read, Grep\n"
            "context: fork\n"
            "paths: src/**/*.py, tests/**\n"
            "model: claude-sonnet-4\n"
            "---\n"
            "# Prompt body\n"
        )
        assert meta["name"] == "my-skill"
        assert meta["description"] == "A test skill"
        assert meta["user_invocable"] is True
        assert meta["allowed_tools"] == ["Bash", "Read", "Grep"]
        assert meta["context"] == "fork"
        assert meta["paths"] == ["src/**/*.py", "tests/**"]
        assert meta["model"] == "claude-sonnet-4"
        assert "# Prompt body" in body

    def test_no_frontmatter(self):
        meta, body = _parse_frontmatter("Just plain text")
        assert meta == {}
        assert body == "Just plain text"

    def test_quoted_values(self):
        meta, _ = _parse_frontmatter(
            '---\nname: "quoted name"\ndesc: \'single\'\n---\nbody'
        )
        assert meta["name"] == "quoted name"
        assert meta["desc"] == "single"

    def test_boolean_variants(self):
        meta, _ = _parse_frontmatter(
            "---\na: yes\nb: no\nc: True\nd: False\n---\n"
        )
        assert meta["a"] is True
        assert meta["b"] is False
        assert meta["c"] is True
        assert meta["d"] is False

    def test_comment_lines_ignored(self):
        meta, _ = _parse_frontmatter(
            "---\n# comment\nname: ok\n---\nbody"
        )
        assert meta == {"name": "ok"}


# -----------------------------------------------------------------------
# Skill data structure
# -----------------------------------------------------------------------

class TestSkill:
    def test_from_frontmatter(self):
        meta = {
            "name": "deploy",
            "description": "Deploy app",
            "context": "fork",
            "allowed_tools": ["Bash"],
            "model": "claude-sonnet-4",
        }
        skill = _skill_from_frontmatter(
            meta, "Run deploy $ARGUMENTS",
            name="fallback", source="project", skill_root="/tmp/sk",
        )
        assert skill.name == "deploy"  # frontmatter name takes priority
        assert skill.context == "fork"
        assert skill.allowed_tools == ["Bash"]
        assert skill.model == "claude-sonnet-4"
        assert skill.skill_root == "/tmp/sk"

    def test_arguments_substitution(self):
        skill = Skill(name="s", _prompt_text="run $ARGUMENTS now")
        assert "hello" in skill.get_prompt("hello")

    def test_skill_dir_substitution(self):
        skill = Skill(
            name="s", skill_root="/my/root",
            _prompt_text="dir=${CLAUDE_SKILL_DIR}/scripts",
        )
        assert "/my/root/scripts" in skill.get_prompt()

    def test_prompt_fn(self):
        skill = Skill(name="dyn", _prompt_fn=lambda a: f"dynamic:{a}")
        assert skill.get_prompt("x") == "dynamic:x"

    def test_prompt_fn_takes_priority(self):
        skill = Skill(
            name="s",
            _prompt_text="static",
            _prompt_fn=lambda a: "from_fn",
        )
        assert skill.get_prompt("") == "from_fn"


# -----------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------

class TestRegistry:
    def test_register_and_get(self):
        register_skill(Skill(name="a", description="A"))
        assert get_skill("a") is not None
        assert get_skill("nonexistent") is None

    def test_list_all(self):
        register_skill(Skill(name="a", user_invocable=True))
        register_skill(Skill(name="b", user_invocable=False))
        assert len(list_skills(user_invocable_only=False)) == 2
        assert len(list_skills(user_invocable_only=True)) == 1

    def test_bundled_sorted_first(self):
        register_skill(Skill(name="z", source="project"))
        register_skill(Skill(name="a", source="bundled"))
        skills = list_skills(user_invocable_only=False)
        assert skills[0].source == "bundled"

    def test_clear_all(self):
        register_skill(Skill(name="a"))
        clear_skills()
        assert list_skills(user_invocable_only=False) == []

    def test_clear_by_source(self):
        register_skill(Skill(name="a", source="bundled"))
        register_skill(Skill(name="b", source="project"))
        clear_skills(source="project")
        assert get_skill("a") is not None
        assert get_skill("b") is None


# -----------------------------------------------------------------------
# Bundled skills
# -----------------------------------------------------------------------

class TestBundledSkills:
    def test_registers_four_skills(self):
        register_bundled_skills()
        names = sorted(s.name for s in list_skills())
        assert names == ["commit", "review", "simplify", "test"]

    def test_simplify_no_args(self):
        register_bundled_skills()
        p = get_skill("simplify").get_prompt("")
        assert "# Simplify" in p
        assert "Additional Focus" not in p

    def test_simplify_with_args(self):
        register_bundled_skills()
        p = get_skill("simplify").get_prompt("security")
        assert "## Additional Focus" in p
        assert "security" in p

    def test_review_prompt(self):
        register_bundled_skills()
        p = get_skill("review").get_prompt("check API")
        assert "# Code Review" in p
        assert "Do NOT make changes" in p
        assert "check API" in p

    def test_commit_prompt(self):
        register_bundled_skills()
        p = get_skill("commit").get_prompt("fix bug")
        assert "Git Commit" in p
        assert "fix bug" in p

    def test_test_prompt(self):
        register_bundled_skills()
        p = get_skill("test").get_prompt("")
        assert "Run Tests" in p

    def test_all_bundled_are_user_invocable(self):
        register_bundled_skills()
        for s in list_skills():
            assert s.user_invocable is True

    def test_all_bundled_source_is_bundled(self):
        register_bundled_skills()
        for s in list_skills():
            assert s.source == "bundled"


# -----------------------------------------------------------------------
# Disk loading
# -----------------------------------------------------------------------

class TestLoadFromDisk:
    def test_directory_format_skill_md(self, tmp_path):
        d = tmp_path / "deploy"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\n"
            "name: deploy\n"
            "description: Deploy app\n"
            "context: fork\n"
            "allowed-tools: Bash\n"
            "---\n"
            "Run deploy $ARGUMENTS\n"
        )
        loaded = load_skills_from_dir(tmp_path, source="project")
        assert len(loaded) == 1
        s = get_skill("deploy")
        assert s.description == "Deploy app"
        assert s.context == "fork"
        assert s.allowed_tools == ["Bash"]
        assert s.skill_root == str(d)

    def test_directory_without_skill_md_has_fallback(self, tmp_path):
        d = tmp_path / "fmt"
        d.mkdir()
        (d / "README.md").write_text("---\ndescription: Format\n---\nRun fmt.")
        loaded = load_skills_from_dir(tmp_path, source="project")
        assert len(loaded) == 1
        assert get_skill("fmt") is not None

    def test_legacy_single_file(self, tmp_path):
        (tmp_path / "lint.md").write_text(
            "---\ndescription: Run linter\n---\nRun linter.\n"
        )
        loaded = load_skills_from_dir(tmp_path, source="project")
        assert len(loaded) == 1
        assert get_skill("lint").description == "Run linter"

    def test_empty_dir_skipped(self, tmp_path):
        (tmp_path / "empty").mkdir()
        loaded = load_skills_from_dir(tmp_path)
        assert loaded == []

    def test_no_md_dir_skipped(self, tmp_path):
        d = tmp_path / "nomd"
        d.mkdir()
        (d / "README.txt").write_text("not a skill")
        loaded = load_skills_from_dir(tmp_path)
        assert loaded == []

    def test_nonexistent_dir(self):
        loaded = load_skills_from_dir(Path("/tmp/nonexistent-12345"))
        assert loaded == []

    def test_prompt_substitution_from_disk(self, tmp_path):
        d = tmp_path / "greet"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: greet\n---\nHello $ARGUMENTS!")
        load_skills_from_dir(tmp_path)
        assert "world" in get_skill("greet").get_prompt("world")

    def test_skill_root_set_correctly(self, tmp_path):
        d = tmp_path / "myskill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: myskill\n---\nBody")
        load_skills_from_dir(tmp_path)
        assert get_skill("myskill").skill_root == str(d)


# -----------------------------------------------------------------------
# discover_skills
# -----------------------------------------------------------------------

class TestDiscoverSkills:
    def test_project_level(self, tmp_path):
        sd = tmp_path / ".cc-mini" / "skills" / "check"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("---\ndescription: Check\n---\nRun check.")
        loaded = discover_skills(str(tmp_path))
        assert any(s.name == "check" and s.source == "project" for s in loaded)

    def test_nonexistent_path_safe(self):
        loaded = discover_skills("/tmp/nonexistent-99999")
        assert isinstance(loaded, list)


# -----------------------------------------------------------------------
# System prompt section
# -----------------------------------------------------------------------

class TestPromptSection:
    def test_empty_when_no_skills(self):
        assert build_skills_prompt_section() == ""

    def test_lists_skills(self):
        register_skill(Skill(name="deploy", description="Deploy app",
                             when_to_use="After testing"))
        section = build_skills_prompt_section()
        assert "# Available Skills" in section
        assert "/deploy: Deploy app" in section
        assert "— After testing" in section


# -----------------------------------------------------------------------
# Autocomplete integration
# -----------------------------------------------------------------------

class TestAutocomplete:
    def _completions(self, text: str) -> list[str]:
        from prompt_toolkit.document import Document
        from tui.prompt import SlashCommandCompleter
        doc = Document(text, cursor_position=len(text))
        return [c.text for c in SlashCommandCompleter().get_completions(doc, None)]

    def test_slash_s_includes_skills_and_simplify(self):
        register_bundled_skills()
        results = self._completions("/s")
        assert "/skills" in results
        assert "/simplify" in results

    def test_slash_co_includes_compact_and_commit(self):
        register_bundled_skills()
        results = self._completions("/co")
        assert "/compact" in results
        assert "/commit" in results

    def test_slash_re_includes_resume_and_review(self):
        register_bundled_skills()
        results = self._completions("/re")
        assert "/resume" in results
        assert "/review" in results

    def test_no_completions_without_slash(self):
        register_bundled_skills()
        assert self._completions("hello") == []

    def test_project_skill_appears(self, tmp_path):
        sd = tmp_path / "mything"
        sd.mkdir()
        (sd / "SKILL.md").write_text("---\ndescription: My thing\n---\nDo it.")
        load_skills_from_dir(tmp_path)
        results = self._completions("/my")
        assert "/mything" in results


# -----------------------------------------------------------------------
# Command dispatch (parse_command for skills)
# -----------------------------------------------------------------------

class TestCommandParsing:
    def test_skill_as_slash_command(self):
        from commands import parse_command
        assert parse_command("/simplify") == ("simplify", "")
        assert parse_command("/simplify security") == ("simplify", "security")
        assert parse_command("/commit fix bug") == ("commit", "fix bug")

    def test_non_slash_not_parsed(self):
        from commands import parse_command
        assert parse_command("hello") is None
