"""Bundled skills — built-in skills shipped with cc-mini.

Modelled after claude-code's ``src/skills/bundled/`` directory.
Each skill is registered via ``register_skill()`` during startup.
"""

from __future__ import annotations

from .skills import Skill, register_skill


# ---------------------------------------------------------------------------
# /simplify — Code review and cleanup
# ---------------------------------------------------------------------------

_SIMPLIFY_PROMPT = """\
# Simplify: Code Review and Cleanup

Review all changed files for reuse, quality, and efficiency. Fix any issues found.

## Phase 1: Identify Changes

Run `git diff` (or `git diff HEAD` if there are staged changes) to see what \
changed. If there are no git changes, review the most recently modified files \
that the user mentioned or that you edited earlier in this conversation.

## Phase 2: Review

Examine each changed file for:

### Code Reuse
- Duplicated logic that should be extracted into shared functions
- Existing utilities or helpers in the codebase that could replace new code
- Patterns that appear more than twice and should be abstracted

### Code Quality
- Unclear variable or function names
- Missing or incorrect type annotations
- Overly complex logic that could be simplified
- Dead code or unused imports
- Inconsistent style with the rest of the codebase

### Efficiency
- Unnecessary allocations or copies
- N+1 patterns or repeated lookups
- Missing early returns or short-circuit evaluations
- Opportunities to use more efficient data structures

## Phase 3: Fix Issues

For each issue found, fix it directly in the code. Do not just list issues — \
apply the fixes. After fixing, run any relevant tests or linters to verify \
the changes don't break anything.

$ARGUMENTS\
"""


def _simplify_prompt(args: str) -> str:
    text = _SIMPLIFY_PROMPT
    if args:
        text = text.replace("$ARGUMENTS",
                            f"\n## Additional Focus\n\n{args}")
    else:
        text = text.replace("$ARGUMENTS", "")
    return text


# ---------------------------------------------------------------------------
# /review — Code review without auto-fix
# ---------------------------------------------------------------------------

_REVIEW_PROMPT = """\
# Code Review

Review the recent code changes and provide detailed feedback. Do NOT make \
changes — only analyze and report.

## Steps

1. Run `git diff` (or `git diff HEAD` for staged changes) to see what changed.
2. For each changed file, review for:
   - Correctness: logic errors, edge cases, off-by-one errors
   - Security: injection vulnerabilities, unsafe operations, exposed secrets
   - Performance: inefficient patterns, unnecessary work
   - Readability: unclear naming, missing context, complex logic
   - Style: consistency with codebase conventions
3. Provide a structured report with findings grouped by severity:
   - **Critical** — bugs or security issues that must be fixed
   - **Warning** — issues that should be addressed
   - **Suggestion** — improvements that would be nice to have

$ARGUMENTS\
"""


def _review_prompt(args: str) -> str:
    text = _REVIEW_PROMPT
    if args:
        text = text.replace("$ARGUMENTS",
                            f"\n## Additional Focus\n\n{args}")
    else:
        text = text.replace("$ARGUMENTS", "")
    return text


# ---------------------------------------------------------------------------
# /commit — Generate commit message and commit
# ---------------------------------------------------------------------------

_COMMIT_PROMPT = """\
# Git Commit

Create a well-structured git commit for the current staged changes.

## Steps

1. Run `git status` to see what is staged and unstaged.
2. Run `git diff --cached` to see staged changes. If nothing is staged, run \
`git diff` to see unstaged changes and inform the user.
3. Analyze the changes and create a commit message following conventional \
commit style:
   - First line: concise summary (50 chars max), imperative mood
   - Blank line
   - Body: explain what and why (not how), wrap at 72 chars
4. Run `git commit -m "<message>"` with the generated message.

If the user provided instructions, incorporate them into the commit message.

$ARGUMENTS\
"""


def _commit_prompt(args: str) -> str:
    text = _COMMIT_PROMPT
    if args:
        text = text.replace("$ARGUMENTS",
                            f"\n## User Instructions\n\n{args}")
    else:
        text = text.replace("$ARGUMENTS", "")
    return text


# ---------------------------------------------------------------------------
# /test — Run and analyze tests
# ---------------------------------------------------------------------------

_TEST_PROMPT = """\
# Run Tests

Find and run the project's test suite, then analyze the results.

## Steps

1. Identify the test framework:
   - Look for `pytest.ini`, `pyproject.toml` [tool.pytest], `setup.cfg`
   - Look for `package.json` scripts (test, jest, vitest)
   - Look for `Makefile` test targets
2. Run the appropriate test command.
3. If tests fail:
   - Analyze each failure
   - Identify the root cause
   - Suggest or apply fixes if the failures are in recently changed code

$ARGUMENTS\
"""


def _test_prompt(args: str) -> str:
    text = _TEST_PROMPT
    if args:
        text = text.replace("$ARGUMENTS",
                            f"\n## Specific Instructions\n\n{args}")
    else:
        text = text.replace("$ARGUMENTS", "")
    return text


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_bundled_skills() -> None:
    """Register all built-in skills. Called once at startup."""
    register_skill(Skill(
        name="simplify",
        description="Review changed code for reuse, quality, and efficiency, then fix issues found",
        when_to_use="After making code changes, to clean up and improve the code",
        user_invocable=True,
        argument_hint="focus",
        source="bundled",
        _prompt_fn=_simplify_prompt,
    ))

    register_skill(Skill(
        name="review",
        description="Review code changes and report issues without making fixes",
        when_to_use="To get feedback on code changes before committing",
        user_invocable=True,
        argument_hint="focus",
        source="bundled",
        _prompt_fn=_review_prompt,
    ))

    register_skill(Skill(
        name="commit",
        description="Stage changes and create a well-structured git commit",
        when_to_use="When ready to commit changes to git",
        user_invocable=True,
        argument_hint="message",
        source="bundled",
        _prompt_fn=_commit_prompt,
    ))

    register_skill(Skill(
        name="test",
        description="Run the project's test suite and analyze results",
        when_to_use="To verify code changes haven't broken anything",
        user_invocable=True,
        argument_hint="filter",
        source="bundled",
        _prompt_fn=_test_prompt,
    ))
