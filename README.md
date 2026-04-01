# cc-mini

A lightweight Python implementation of [Claude Code](https://claude.ai/code) for the community — a terminal-based AI coding assistant that runs an agentic tool loop via the Anthropic API.

## Features

- **Interactive REPL** with command history
- **Streaming responses** — text appears as it is generated
- **Agentic tool loop** — multiple tool calls per turn
- **6 built-in tools**: file read, file edit, file write, glob, grep, bash
- **Permission system** — reads auto-approved, writes/bash ask for confirmation
- **Conversation management** — session persistence, context compression, resume
- **Skill system** — built-in and custom SKILL.md-based reusable prompts
- **Sandbox** — run bash commands in a bubblewrap (bwrap) sandbox for filesystem and network isolation

## Requirements

- Python 3.10+ (3.11+ recommended)
- [Anthropic API key](https://console.anthropic.com/)
- Linux with [bubblewrap](https://github.com/containers/bubblewrap) (`apt install bubblewrap`) for sandbox support (optional)

## Installation

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/e10nMa2k/cc-mini/main/install.sh | bash
```

This clones the repo to `~/.cc-mini`, creates an isolated venv, and places a `cc-mini` launcher in `~/.local/bin`.  No `sudo` required.

**Options** (set as env vars before the command):

| Variable | Default | Description |
|---|---|---|
| `CC_MINI_INSTALL_DIR` | `~/.cc-mini` | Where to clone the repo |
| `CC_MINI_BIN_DIR` | `~/.local/bin` | Where to put the `cc-mini` launcher |
| `CC_MINI_BRANCH` | `main` | Git branch to install |

Example with custom dir:
```bash
CC_MINI_INSTALL_DIR=~/tools/cc-mini curl -fsSL https://raw.githubusercontent.com/e10nMa2k/cc-mini/main/install.sh | bash
```

To update to the latest version, just re-run the same command.

### Manual install (from source)

```bash
git clone https://github.com/e10nMa2k/cc-mini.git
cd cc-mini
pip install -e ".[dev]"
```

## Usage

### Set API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Custom API base URL (useful for proxies or compatible endpoints):

```bash
export ANTHROPIC_BASE_URL=https://your-gateway.example.com
```

Optional environment variables for runtime defaults:

```bash
export CC_MINI_MODEL=claude-sonnet-4-5
export CC_MINI_MAX_TOKENS=64000
```

### Interactive REPL

```bash
cc-mini
```

```
cc-mini  type 'exit' or Ctrl+C to quit

> list all python files in this project
↳ Glob(**/*.py) ✓
Here are all the .py files...

> read engine.py and explain how the tool loop works
↳ Read(src/core/engine.py) ✓
The submit() method implements an agentic loop...
```

Type `exit` or press `Ctrl+C` to quit.

### One-shot prompt

```bash
cc-mini "what tests exist in this project?"
```

### Non-interactive / scripted mode

Use `-p` to print the response and exit:

```bash
cc-mini -p "summarize this codebase in 3 bullets"
```

Pipe input:

```bash
echo "what does engine.py do?" | cc-mini -p
```

### Auto-approve permissions

Skip permission prompts for all tools (use with care):

```bash
cc-mini --auto-approve
```

### Configure API endpoint and model from CLI

```bash
cc-mini \
  --base-url https://your-gateway.example.com \
  --api-key sk-ant-... \
  --model claude-sonnet-4
```

`max_tokens` follows the selected model by default. Override when you need a tighter cap:

```bash
cc-mini --model claude-3-5-haiku --max-tokens 2048
```

### Configure with a TOML file

Config files are loaded in this order:

1. `~/.config/cc-mini/config.toml`
2. `.cc-mini.toml` in the current working directory

The project-local file overrides the home config. Point to a specific file with `--config`.

Example:

```toml
[anthropic]
api_key = "sk-ant-..."
base_url = "https://your-gateway.example.com"
model = "claude-sonnet-4"
```

Top-level keys are also supported:

```toml
api_key = "sk-ant-..."
base_url = "https://your-gateway.example.com"
model = "claude-3-7-sonnet"
max_tokens = 64000
```

## Tools

| Tool | Name | Permission |
|------|------|------------|
| Read file | `Read` | auto-approved |
| Find files | `Glob` | auto-approved |
| Search content | `Grep` | auto-approved |
| Edit file | `Edit` | requires confirmation |
| Write file | `Write` | requires confirmation |
| Run command | `Bash` | requires confirmation |

### Permission prompt

When the assistant wants to run a write or bash tool, you'll see:

```
Permission required: Bash
  command: pytest tests/ -v

  Allow? [y]es / [n]o / [a]lways:
```

- `y` — allow once
- `n` — deny
- `a` — always allow this tool for the rest of the session

## Conversation Management

cc-mini automatically saves conversations and can compress long contexts to stay within token limits. Modelled after claude-code's session storage and compact system.

### Session Persistence

Every conversation is saved as a JSONL file under `~/.mini-claude/sessions/`. Messages are persisted incrementally — each message is appended as it happens, so nothing is lost even if the process crashes.

```bash
# Resume a previous session by index or ID
cc-mini --resume 1

# Or use slash commands inside the REPL
> /history              # List saved sessions
> /resume 2             # Resume session #2
> /resume a3f2b         # Resume by session ID prefix
```

### Context Compression

When conversations grow long, the context window fills up. cc-mini can compress older messages into a structured summary, keeping recent messages intact.

```bash
# Manual compact
> /compact                          # Compress with default prompt
> /compact focus on the auth work   # Compress with custom instructions

# Auto-compact triggers automatically when token estimate exceeds 100k
```

How it works:
1. Messages are split into **history** (to summarize) and **recent** (to keep)
2. History is sent to the API with a structured summary prompt (Primary Request, Key Technical Concepts, Files and Code, Current Work, etc.)
3. The summary replaces the old messages; recent messages are preserved intact
4. Tool-use / tool-result pairs are never split across the boundary

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/compact [instructions]` | Compress conversation context |
| `/resume [number\|id]` | Resume a past session |
| `/history` | List saved sessions for this directory |
| `/clear` | Clear conversation, start a new session |
| `/skills` | List all available skills |

## Skills

Skills are one-command workflows. Type `/name` and the AI runs a full sequence of steps for you — no need to explain what to do each time.

### Built-in Skills

| Command | What it does |
|---------|-------------|
| `/simplify` | Reviews your changed code for duplication, quality, and efficiency — **then fixes it** |
| `/review` | Reviews code changes and reports issues — **read-only, no edits** |
| `/commit` | Runs `git add`, generates a clean commit message, and commits |
| `/test` | Detects the project's test framework, runs it, and analyzes failures |

All skills accept optional arguments passed as extra instructions to the AI:

```
/simplify focus on security
/review only check the API routes
/commit fix login page styling
/test only run test_auth.py
```

### Example

```
cc-mini

> write a fibonacci function to fib.py

↳ Write(fib.py) …
  ✓ done
Created fib.py with recursive and iterative implementations...

> /review                          ← one-command review

Running skill: /review…
↳ Bash(git diff) …
  ✓ done

## Code Review Report
### Warning
- fib_recursive() does not handle negative input
- Missing type annotations
### Suggestion
- Consider adding @functools.lru_cache

> /simplify                        ← one-command fix

Running skill: /simplify…
↳ Bash(git diff) …  ✓ done
↳ Read(fib.py) …    ✓ done
↳ Edit(fib.py) …    ✓ done
Fixed: added negative check, type annotations, lru_cache...

> /test                            ← one-command test

Running skill: /test…
↳ Bash(python -m pytest tests/ -v) …
  ✓ done
All 3 tests passed ✓

> /commit add fibonacci            ← one-command commit

Running skill: /commit…
↳ Bash(git add fib.py && git commit -m "feat: add fibonacci") …
  ✓ done
```

### Custom Skills

You can create your own skills for project-specific workflows.

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

`$ARGUMENTS` is replaced with whatever you type after the command. For example, `/deploy production` replaces `$ARGUMENTS` with `production`.

**Step 3**: Use it

```
> /deploy staging
Running skill: /deploy…
```

Skills auto-complete when you type `/`. Use `/skills` to see everything available:

```
> /skills
┌──────────────┬─────────┬──────────────────────────┐
│ Command      │ Source  │ Description              │
├──────────────┼─────────┼──────────────────────────┤
│ /simplify    │ bundled │ Review and fix code       │
│ /review      │ bundled │ Review code (read-only)   │
│ /commit      │ bundled │ Generate commit and push  │
│ /test        │ bundled │ Run tests and analyze     │
│ /deploy      │ project │ Deploy to staging         │
└──────────────┴─────────┴──────────────────────────┘
```

### Where skills are discovered

cc-mini looks for skills in these locations (in order):

| Location | Scope |
|----------|-------|
| Built-in | 4 bundled skills, always available |
| `~/.cc-mini/skills/` | Personal skills, available in all projects |
| `<project>/.cc-mini/skills/` | Project skills, commit to git and share with your team |

### Advanced: SKILL.md options

The `---` block at the top of a SKILL.md (YAML frontmatter) supports these fields:

```markdown
---
name: deploy                    # Skill name (defaults to folder name)
description: Deploy to staging  # Short description
context: fork                   # fork = runs in isolation, won't affect
                                #   your current conversation
                                # inline = injected into conversation (default)
allowed-tools: Bash, Read       # Restrict which tools the skill can use
arguments: target               # Argument hint shown in /skills list
---

Your prompt goes here...
Use $ARGUMENTS for user-provided arguments.
Use ${CLAUDE_SKILL_DIR} for the skill's directory path.
```

## Buddy — AI Companion Pet

cc-mini includes **Buddy**, a Tamagotchi-style AI companion that lives in your terminal. Each user gets a unique pet determined by a seeded PRNG — same user always gets the same species, rarity, and stats.

### Quick start

```
> /buddy              # Hatch your companion (first time)
> /buddy              # Show companion card (after hatching)
> /buddy pet          # Pet your companion
> /buddy mute         # Mute companion reactions
> /buddy unmute       # Unmute reactions
```

### How it works

- **18 species**: duck, goose, blob, cat, dragon, octopus, owl, penguin, turtle, snail, ghost, axolotl, capybara, cactus, robot, rabbit, mushroom, chonk
- **5 rarities**: Common (60%), Uncommon (25%), Rare (10%), Epic (4%), Legendary (1%) — plus 1% shiny chance
- **5 stats** (0–100): Debugging, Patience, Chaos, Wisdom, Snark — these shape how your companion talks
- **ASCII sprite** with idle animation (blinking, fidgeting) displayed in the terminal toolbar
- **Automatic reactions**: after each Claude response, your companion comments in a speech bubble
- **Direct conversation**: address your companion by name and it replies directly (with conversation memory)

### Example

```
> help me fix this bug

Found the issue — off-by-one error in the loop...

(×>) Glitch Honker: Off-by-one again, classic.

> Glitch what do you think of this code?

(×>) Glitch Honker: If it runs, don't ask me philosophical questions.
```

The companion's personality is generated by Claude on first hatch and persists permanently. Stats influence behavior: high Snark = sarcastic, high Patience = supportive, high Chaos = unpredictable.

## KAIROS

The assistant can remember information across sessions, and automatically consolidate memories over time.

**Slash commands:**

| Command | Description |
|---------|-------------|
| `/remember <text>` | Save a note to the daily log |
| `/memory` | Show current memory index |
| `/dream` | Manually consolidate daily logs into organized topic files |

**Auto-dream** runs automatically after a turn when ≥ 24 hours and ≥ 5 new sessions have passed since the last consolidation. Configurable via `--dream-interval`, `--dream-min-sessions`, or `--no-auto-dream`.

**Try it out:**

```bash
# 1. Save some notes and manually consolidate
cc-mini --auto-approve
> /remember I prefer Python over JavaScript
> /remember Our project uses gRPC + PostgreSQL
> /dream                    # reads daily logs, creates topic files + MEMORY.md
> /memory                   # verify the memory index

# 2. Start a new session — the model should recall your preferences
cc-mini
> What do you know about my preferences?

# 3. Test auto-dream (default: 24h + 5 sessions, use flags to lower for testing)
#    First, accumulate a few sessions:
for i in $(seq 1 3); do cc-mini "session $i"; done
#    Then start with lowered thresholds:
cc-mini --dream-interval 0 --dream-min-sessions 1 --auto-approve
> hello
#    Auto-dream triggers after the response
```

Data is stored in `~/.mini-claude/` (memory in `memory/`, sessions in `sessions/`).

## Sandbox

The sandbox feature runs BashTool commands inside a [bubblewrap (bwrap)](https://github.com/containers/bubblewrap) sandbox on Linux, restricting filesystem writes and network access. This prevents accidental or malicious destructive operations.

### How it works

When sandbox is enabled, every Bash command is wrapped with bwrap:

- The entire filesystem is mounted **read-only** (`--ro-bind / /`)
- Only the current working directory is **writable** (`--bind $CWD $CWD`)
- Network access is **isolated** by default (`--unshare-net`)
- Configuration files (`.cc-mini.toml`, `CLAUDE.md`) are **protected** from modification
- PID namespace is isolated (`--unshare-pid`)

### Sandbox modes

| Mode | Behavior |
|------|----------|
| `auto-allow` | Sandbox enabled, bash commands auto-approved (no permission prompt) |
| `regular` | Sandbox enabled, bash commands still require confirmation |
| `disabled` | No sandbox (default) |

### Configure via REPL

Use the `/sandbox` command in the interactive REPL:

```
> /sandbox                     # interactive mode selector
> /sandbox status              # show current status and dependency check
> /sandbox mode auto-allow     # enable sandbox with auto-allow
> /sandbox mode regular        # enable sandbox with manual approval
> /sandbox mode disabled       # disable sandbox
> /sandbox exclude "docker *"  # skip sandbox for matching commands
```

### Configure via TOML

Add a `[sandbox]` section to your config file (`.cc-mini.toml` or `~/.config/cc-mini/config.toml`):

```toml
[sandbox]
enabled = true
auto_allow_bash = true
allow_unsandboxed = false
excluded_commands = ["docker *", "npm run *"]
unshare_net = true

[sandbox.filesystem]
allow_write = ["."]
deny_write = []
deny_read = []
```

### Excluded commands

Some commands need to run outside the sandbox (e.g., Docker, package managers). Add patterns to `excluded_commands`:

- **Exact**: `"git"` matches only `git`
- **Prefix**: `"npm run"` matches `npm run test`, `npm run build`, etc.
- **Wildcard**: `"docker *"` matches `docker build .`, `docker run ...`, etc.

Excluded commands still require the normal permission prompt even in auto-allow mode.

### Graceful degradation

If bwrap is not installed or unavailable (non-Linux, Docker without user namespaces), sandbox is automatically disabled. Check with `/sandbox status`:

```
Sandbox Status
  Mode: auto-allow
  Enabled: no
Dependency errors:
  bubblewrap (bwrap) not found. Install: apt install bubblewrap
```

## Project structure

```
src/core/
├── main.py           # CLI entry point + REPL
├── engine.py         # Streaming API loop + tool execution
├── context.py        # System prompt builder (git status, date, memory)
├── config.py         # Configuration (CLI, env, TOML)
├── commands.py       # Slash command system + skill dispatch
├── session.py        # Session persistence (JSONL)
├── compact.py        # Context window compaction
├── skills.py         # Skill loader, registry, and discovery
├── skills_bundled.py # Built-in skills (simplify, review, commit, test)
├── memory.py         # KAIROS memory system (logs, dream, sessions)
├── permissions.py    # Permission checker + sandbox auto-allow
├── _keylistener.py   # Esc/Ctrl+C detection
├── sandbox/          # Sandbox subsystem (bwrap isolation)
│   ├── config.py         # SandboxConfig dataclass + TOML persistence
│   ├── checker.py        # Dependency checking (bwrap, user namespaces)
│   ├── command_matcher.py # Excluded command pattern matching
│   ├── wrapper.py        # bwrap command line generator
│   └── manager.py        # Unified sandbox manager interface
├── tools/
│   ├── base.py       # Tool ABC + ToolResult
│   ├── file_read.py
│   ├── file_edit.py
│   ├── file_write.py
│   ├── glob_tool.py
│   ├── grep_tool.py
│   └── bash.py       # Bash tool with sandbox integration
└── buddy/
    ├── types.py      # Data model: species, rarity, stats
    ├── companion.py  # Mulberry32 PRNG + deterministic generation
    ├── sprites.py    # ASCII art for 18 species (3 frames each)
    ├── animator.py   # Real-time tick-based animation engine
    ├── render.py     # Rich terminal rendering (cards, bubbles)
    ├── commands.py   # /buddy command handler
    ├── observer.py   # Post-response reaction generator
    ├── prompt.py     # System prompt integration
    └── storage.py    # JSON persistence
```

## Running tests

```bash
# All tests
pytest tests/ -v

# Sandbox tests only
pytest tests/test_sandbox*.py -v

# Skip integration tests that require bwrap
pytest tests/ -v -k "not integration"
```

## Tips

- Place a `CLAUDE.md` file in your project root — it will be included in the system prompt automatically
- Use `--auto-approve` when running non-interactively or for trusted tasks
- Use `/history` to list past sessions, `/resume` to continue one
- Use `/simplify` after making changes to auto-review and clean up code
- Create project-specific skills in `.cc-mini/skills/` for repeatable workflows
- Conversations auto-compact when approaching 100k tokens; use `/compact` to trigger manually
- Memories persist across sessions in `~/.mini-claude/memory/`; run `/dream` to consolidate
- Type `/buddy` to hatch your AI companion — it watches your coding sessions and comments from the sideline
