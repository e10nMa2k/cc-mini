"""Excluded command matching logic.

Corresponds to shouldUseSandbox.ts containsExcludedCommand() (lines 21-128).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum


class RuleType(Enum):
    PREFIX = "prefix"
    EXACT = "exact"
    WILDCARD = "wildcard"


@dataclass
class MatchRule:
    type: RuleType
    pattern: str


def parse_rule(pattern: str) -> MatchRule:
    """Parse an exclusion pattern into a MatchRule.

    Rule determination (corresponds to bashPermissionRule):
    - contains '*' or '?' -> wildcard
    - contains space -> prefix ("npm run" matches "npm run xxx")
    - otherwise -> exact ("git" only matches "git")
    """
    stripped = pattern.strip()
    if "*" in stripped or "?" in stripped:
        return MatchRule(RuleType.WILDCARD, stripped)
    if " " in stripped:
        return MatchRule(RuleType.PREFIX, stripped)
    return MatchRule(RuleType.EXACT, stripped)


def matches_rule(rule: MatchRule, command: str) -> bool:
    """Check whether a command matches a rule."""
    if rule.type == RuleType.EXACT:
        return command == rule.pattern
    if rule.type == RuleType.PREFIX:
        return command == rule.pattern or command.startswith(rule.pattern + " ")
    if rule.type == RuleType.WILDCARD:
        return fnmatch.fnmatch(command, rule.pattern)
    return False


def _split_compound_command(command: str) -> list[str]:
    """Split compound commands on '&&'.

    Corresponds to splitCommand_DEPRECATED().
    Simple split — does not handle '&&' inside quotes.
    """
    return [part.strip() for part in command.split("&&") if part.strip()]


def _strip_env_prefix(command: str) -> str:
    """Strip leading environment variable assignments.

    E.g. "FOO=bar BAZ=1 npm test" -> "npm test"
    Corresponds to env var stripping in shouldUseSandbox.ts.
    """
    parts = command.split()
    i = 0
    while i < len(parts) and "=" in parts[i]:
        i += 1
    return " ".join(parts[i:]) if i < len(parts) else command


def contains_excluded_command(
    command: str,
    excluded_patterns: list[str],
) -> bool:
    """Determine whether a command should be excluded from sandbox.

    Corresponds to containsExcludedCommand (shouldUseSandbox.ts:21-128).
    Logic:
    1. Split on '&&' into sub-commands
    2. For each sub-command, try stripping env var prefix
    3. Match against each exclusion pattern
    4. Any sub-command matching any pattern -> True
    """
    if not excluded_patterns:
        return False

    rules = [parse_rule(p) for p in excluded_patterns]
    subcommands = _split_compound_command(command)

    for subcmd in subcommands:
        candidates = [subcmd, _strip_env_prefix(subcmd)]
        candidates = list(dict.fromkeys(candidates))  # deduplicate preserving order

        for rule in rules:
            for cand in candidates:
                if matches_rule(rule, cand):
                    return True
    return False
