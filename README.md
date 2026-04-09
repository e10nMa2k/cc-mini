<div align="center">

# cc-mini

**Ultra-light Harness scaffolding for AI agents**

**Agentic** &nbsp;·&nbsp; **Built to Extend** &nbsp;·&nbsp; **From Claude Code**
<br>

The entire core is `~1000 lines of Python`

</div>

---

### **NEW: Buddy — AI Companion with Custom Sprites**

> Your coding companion lives in the terminal. Type `/buddy` to hatch it. Supports custom ASCII species — bring your own Pikachu!

![Custom Pikachu buddy companion](assets/buddy-pikachu.jpg)

[Full Buddy docs &rarr;](docs/buddy.md)

---

## Features

### Core

- **Interactive REPL** with streaming output, command history, slash command autocomplete
- **Agentic tool loop** — Claude calls tools autonomously until the task is complete
- **9 built-in tools**: `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash`, `AskUser`, `EnterPlanMode`, `ExitPlanMode`
- **Plan mode** — parallel subagents explore codebase before you implement, with permission isolation
- **Permission system** — mode-aware (default/plan), reads auto-approved, writes/bash ask for confirmation
- **Session persistence** — auto-save conversations, `/resume` to continue later
- **Context compression** — auto-compact when approaching token limits
- **Anthropic + OpenAI compatible** — works with any compatible API endpoint

### Advanced (from unreleased Claude Code features)

| Feature | Description | Docs |
|---------|-------------|------|
| **Coordinator Mode** | Background workers for parallel research and implementation | [docs &rarr;](docs/coordinator.md) |
| **Buddy** | Tamagotchi AI pet with personality, stats, mood, and speech bubbles | [docs &rarr;](docs/buddy.md) |
| **KAIROS Memory** | Cross-session memory with auto-consolidation | [docs &rarr;](docs/memory.md) |
| **Skills** | One-command workflows: `/review`, `/commit`, `/test`, `/simplify` | [docs &rarr;](docs/skills.md) |
| **Sandbox** | Bubblewrap isolation for bash commands | [docs &rarr;](docs/sandbox.md) |

---

## Quick Start

### Requirements

- Python 3.10+ (3.11+ recommended)
- An API key for [Anthropic](https://console.anthropic.com/) or any OpenAI-compatible provider

### Install

```bash
# One-line install (recommended)
curl -fsSL https://raw.githubusercontent.com/e10nMa2k/cc-mini/main/install.sh | bash

# Or manual
git clone https://github.com/e10nMa2k/cc-mini.git
cd cc-mini
pip install -e ".[dev]"
```

### Set API Key

```bash
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Or OpenAI-compatible
export CC_MINI_PROVIDER=openai # protocol type, not vendor name
# Azure AI Foundry and other OpenAI-compatible gateways still use "openai"
# (do not set provider to "foundry", "bedrock", etc.)
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://your-gateway.example.com/v1
export CC_MINI_MODEL=gpt-... # optional, default is "gpt-5.1-codex"
```

### Run

```bash
cc-mini                              # Interactive REPL
cc-mini "what tests exist?"          # One-shot prompt
cc-mini -p "summarize this codebase" # Print and exit
cc-mini --auto-approve               # Skip permission prompts
cc-mini --resume 1                   # Resume previous session
cc-mini --coordinator                # Coordinator mode
```

### First Session Demo

```
cc-mini

> list all python files in this project
↳ Glob(**/*.py) ✓
Found 12 Python files...

> read engine.py and explain the tool loop
↳ Read(src/core/engine.py) ✓
The submit() method implements an agentic loop...

> /buddy
Hatching your companion...
✨ SHINY LEGENDARY DUCK
Glitch Quack hatched! ★★★★★

> /buddy mood
Glitch Quack's mood:
  Happy      ████████████████░░░░  65 (high)
  Bored      ██████████░░░░░░░░░░  50 (neutral)

> /review
Running skill: /review…
↳ Bash(git diff) … ✓ done
## Code Review: no issues found ✓
```

[Full configuration docs &rarr;](docs/configuration.md)

---

## Tools

| Tool | Description | Permission |
|------|-------------|------------|
| `Read` | Read file contents | auto-approved |
| `Glob` | Find files by pattern | auto-approved |
| `Grep` | Search file contents | auto-approved |
| `Edit` | Edit file (string replacement) | requires confirmation |
| `Write` | Write/create file | requires confirmation |
| `Bash` | Run shell command | requires confirmation |
| `AskUser` | Ask user a question | auto-approved |
| `EnterPlanMode` | Enter plan mode | auto-approved |
| `ExitPlanMode` | Exit plan mode | auto-approved |

Coordinator mode adds: `Agent` (spawn worker), `SendMessage` (continue worker), `TaskStop` (stop worker). Plan mode also uses `Agent` to launch parallel read-only explore/plan subagents. See [coordinator docs](docs/coordinator.md).

---

## Data Paths

| Data | Path |
|------|------|
| Installation (source code) | `~/.cc-mini/` |
| Sessions | `~/.config/cc-mini/sessions/` |
| Memory (KAIROS) | `~/.config/cc-mini/memory/` |
| Plans | `~/.config/cc-mini/plans/` |
| REPL history | `~/.config/cc-mini/history` |
| Companion data | `~/.config/cc-mini/companion.json` |
| User skills | `~/.cc-mini/skills/` |
| Project skills | `{cwd}/.cc-mini/skills/` |
| Project config | `.cc-mini.toml` |

---

## Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/compact` | Compress conversation context |
| `/resume` | Resume a past session |
| `/history` | List saved sessions |
| `/clear` | Clear conversation, start new session |
| `/skills` | List all available skills |
| `/buddy` | Companion pet — hatch, pet, stats, mood |
| `/buddy help` | Show all buddy commands and gameplay guide |
| `/review` | Code review (skill) |
| `/commit` | Git commit (skill) |
| `/test` | Run tests (skill) |
| `/simplify` | Review and fix code (skill) |

Type `/` to see autocomplete suggestions.

---

## Project Structure

```
src/
├── core/                  # Pure harness — engine, LLM, config
│   ├── engine.py          # Streaming API loop + tool execution
│   ├── llm.py             # LLM client (Anthropic + OpenAI)
│   ├── config.py          # Configuration (CLI, env, TOML)
│   ├── context.py         # System prompt builder
│   ├── tool.py            # Base Tool protocol + ToolResult
│   ├── permissions.py     # Permission checker
│   └── session.py         # Session persistence
│
├── tools/                 # Tool implementations (one per file)
│   ├── bash.py            # Shell command execution
│   ├── file_read.py       # Read files
│   ├── file_edit.py       # Edit files (string replacement)
│   ├── file_write.py      # Write/create files
│   ├── glob_tool.py       # Find files by pattern
│   ├── grep_tool.py       # Search file contents
│   ├── ask_user.py        # Ask user questions
│   ├── plan_tools.py      # EnterPlanMode / ExitPlanMode
│   └── agent.py           # Coordinator agent tools
│
├── features/              # Pluggable capabilities
│   ├── compact.py         # Context compression
│   ├── coordinator.py     # Coordinator mode
│   ├── worker_manager.py  # Background worker lifecycle
│   ├── cost_tracker.py    # Token usage tracking
│   ├── memory.py          # KAIROS memory system
│   ├── plan.py            # Plan mode logic
│   ├── skills.py          # Skill loader and registry
│   ├── skills_bundled.py  # Built-in skills (review, commit, test, simplify)
│   └── sandbox/           # Bubblewrap sandbox subsystem
│
├── tui/                   # Terminal UI
│   ├── app.py             # CLI entry point + REPL
│   ├── query.py           # Query submission + streaming display
│   ├── rendering.py       # Rich console rendering
│   ├── prompt.py          # Input prompt
│   ├── input_parser.py    # Input parsing
│   ├── shell.py           # Shell integration
│   └── keylistener.py     # Esc/Ctrl+C detection
│
├── commands/              # Slash command handlers
└── buddy/                 # AI companion pet system
```

## Running Tests

```bash
pytest tests/ -v
pytest tests/ -v -k "not integration"  # skip bwrap tests
```

---

## Documentation

| Topic | Link |
|-------|------|
| Configuration (API keys, TOML, CLI flags) | [docs/configuration.md](docs/configuration.md) |
| Buddy (AI companion pet) | [docs/buddy.md](docs/buddy.md) |
| Coordinator Mode (background workers) | [docs/coordinator.md](docs/coordinator.md) |
| KAIROS Memory System | [docs/memory.md](docs/memory.md) |
| Skills (custom workflows) | [docs/skills.md](docs/skills.md) |
| Sandbox (bash isolation) | [docs/sandbox.md](docs/sandbox.md) |
