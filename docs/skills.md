# Skills

Skills are one-command workflows. Type `/name` and the AI runs a full sequence of steps.

Eligible skills can also be invoked by the model through `SkillTool`. Manual
slash invocation remains available and follows the existing inline/fork behavior.

## Built-in Skills

| Command | What it does |
|---------|-------------|
| `/simplify` | Reviews changed code for duplication, quality, efficiency — **then fixes it** |
| `/review` | Reviews code changes and reports issues — **read-only, no edits** |
| `/commit` | Runs `git add`, generates a commit message, and commits |
| `/test` | Detects the test framework, runs it, and analyzes failures |

All skills accept optional arguments:

```
/simplify focus on security
/review only check the API routes
/commit fix login page styling
/test only run test_auth.py
```

## Example

```
> /review

Running skill: /review…
↳ Bash(git diff) …  ✓ done

## Code Review Report
### Warning
- fib_recursive() does not handle negative input
### Suggestion
- Consider adding @functools.lru_cache

> /simplify

Running skill: /simplify…
↳ Read(fib.py) …    ✓ done
↳ Edit(fib.py) …    ✓ done
Fixed: added negative check, type annotations, lru_cache...
```

## Custom Skills

**Step 1**: Create a directory under `.cc-mini/skills/`

```bash
mkdir -p .cc-mini/skills/deploy
```

**Step 2**: Write a `SKILL.md` file

```markdown
---
name: deploy
description: Deploy to staging environment
---

# Deploy

1. Run `git status` to check for uncommitted changes
2. Run `./scripts/deploy.sh $ARGUMENTS`
3. Report deployment status
```

`$ARGUMENTS` is replaced with whatever you type after the command.

**Step 3**: Use it

```
> /deploy staging
Running skill: /deploy…
```

## Discovery Locations

| Location | Scope |
|----------|-------|
| Built-in | 4 bundled skills, always available |
| `~/.cc-mini/skills/` | Personal skills, all projects |
| `<project>/.cc-mini/skills/` | Project skills, share with team |

## Project-Specific Skill Examples

Some custom skills are tied to a particular repository layout or CLI workflow.
Those are usually better shared as examples than as bundled built-in skills.

- [CitOrigin custom skill example](./examples/citorigin/README.md)

The CitOrigin example shows how a repository can ship a reusable custom skill
for a domain-specific claim-evidence auditing workflow.

## SKILL.md Frontmatter

```markdown
---
name: deploy
description: Deploy to staging
context: fork          # fork = isolated, inline = in conversation (default)
allowed-tools: Bash, Read
model-invocable: true  # opt in to SkillTool; false by default
arguments: target
---
```

`model-invocable: true` is effective only when `allowed-tools` is explicitly
declared, including an explicit empty list. The child agent always receives
`Read`, `Glob`, and `Grep` when those tools exist on the caller, plus the
declared tools. Meta tools such as Agent, plan, todo, and SkillTool are never
forwarded.

`context: fork` starts with no parent conversation. `context: inline` receives
a deep-copied parent snapshot ending immediately before the assistant response
that called SkillTool. Large inline snapshots may be compacted in the child;
the parent conversation is never modified.

Skill tool access does not bypass normal permission prompts or sandbox rules.
The Coordinator and Explore agents cannot invoke skills directly, and Plan
Mode removes SkillTool. A Coordinator may grant an immutable skill allowlist
to a general Worker when creating it.
