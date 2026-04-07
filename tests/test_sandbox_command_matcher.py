"""Tests for sandbox/command_matcher.py"""

from __future__ import annotations

import pytest

from features.sandbox.command_matcher import (
    RuleType,
    parse_rule,
    matches_rule,
    contains_excluded_command,
    _split_compound_command,
    _strip_env_prefix,
)


class TestParseRule:
    def test_exact(self):
        rule = parse_rule("git")
        assert rule.type == RuleType.EXACT
        assert rule.pattern == "git"

    def test_prefix(self):
        rule = parse_rule("npm run")
        assert rule.type == RuleType.PREFIX
        assert rule.pattern == "npm run"

    def test_wildcard_star(self):
        rule = parse_rule("docker *")
        assert rule.type == RuleType.WILDCARD

    def test_wildcard_question(self):
        rule = parse_rule("test?")
        assert rule.type == RuleType.WILDCARD

    def test_strips_whitespace(self):
        rule = parse_rule("  git  ")
        assert rule.pattern == "git"


class TestMatchesRule:
    def test_exact_match(self):
        rule = parse_rule("git")
        assert matches_rule(rule, "git") is True

    def test_exact_no_match(self):
        rule = parse_rule("git")
        assert matches_rule(rule, "git log") is False

    def test_prefix_match(self):
        rule = parse_rule("npm run")
        assert matches_rule(rule, "npm run test") is True

    def test_prefix_exact(self):
        rule = parse_rule("npm run")
        assert matches_rule(rule, "npm run") is True

    def test_prefix_no_match(self):
        rule = parse_rule("npm run")
        assert matches_rule(rule, "npm install") is False

    def test_wildcard_match(self):
        rule = parse_rule("npm run test:*")
        assert matches_rule(rule, "npm run test:unit") is True

    def test_wildcard_no_match(self):
        rule = parse_rule("npm run *")
        assert matches_rule(rule, "pip install") is False


class TestSplitCompound:
    def test_simple(self):
        assert _split_compound_command("ls") == ["ls"]

    def test_double_ampersand(self):
        assert _split_compound_command("cd /tmp && npm test") == ["cd /tmp", "npm test"]

    def test_empty_parts(self):
        assert _split_compound_command("a &&  && b") == ["a", "b"]


class TestStripEnvPrefix:
    def test_single_env(self):
        assert _strip_env_prefix("FOO=bar npm test") == "npm test"

    def test_multiple_env(self):
        assert _strip_env_prefix("FOO=1 BAR=2 cmd arg") == "cmd arg"

    def test_no_env(self):
        assert _strip_env_prefix("npm test") == "npm test"

    def test_only_env(self):
        assert _strip_env_prefix("FOO=bar") == "FOO=bar"


class TestContainsExcludedCommand:
    def test_no_patterns(self):
        assert contains_excluded_command("anything", []) is False

    def test_exact_match(self):
        assert contains_excluded_command("git", ["git"]) is True

    def test_exact_no_match(self):
        assert contains_excluded_command("git log", ["git"]) is False

    def test_prefix_match(self):
        assert contains_excluded_command("npm run test", ["npm run"]) is True

    def test_wildcard_match(self):
        assert contains_excluded_command("docker build .", ["docker *"]) is True

    def test_compound_command_match(self):
        assert contains_excluded_command(
            "cd /tmp && npm test", ["npm test"]
        ) is True

    def test_env_prefix_strip(self):
        assert contains_excluded_command(
            "FOO=bar npm test", ["npm test"]
        ) is True

    def test_no_false_positive(self):
        assert contains_excluded_command("pip install", ["npm run *"]) is False

    def test_wildcard_star_all(self):
        assert contains_excluded_command("anything", ["*"]) is True
