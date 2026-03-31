from __future__ import annotations

import os
import tomllib
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-4"
_FALLBACK_MAX_TOKENS = 8192
_MODEL_ALIASES = {
    "claude-opus-4.1": "claude-opus-4-1",
    "claude-opus-4": "claude-opus-4-6",
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-3.7-sonnet": "claude-3-7-sonnet",
    "claude-3.5-sonnet": "claude-3-5-sonnet",
    "claude-3.5-haiku": "claude-3-5-haiku",
    "claude-3-haiku": "claude-3-haiku",
}
_MODEL_MAX_TOKENS = (
    ("claude-opus-4-1", 32000),
    ("claude-opus-4", 32000),
    ("claude-sonnet-4", 64000),
    ("claude-3-7-sonnet", 64000),
    ("claude-3-5-sonnet", 8192),
    ("claude-3-5-haiku", 8192),
    ("claude-3-haiku", 4096),
)
_ENV_MODEL = "CC_MINI_MODEL"
_ENV_MAX_TOKENS = "CC_MINI_MAX_TOKENS"
_ENV_MEMORY_DIR = "CC_MINI_MEMORY_DIR"
_DEFAULT_CONFIG_PATHS = (
    Path.home() / ".config" / "cc-mini" / "config.toml",
    Path.cwd() / ".cc-mini.toml",
)


@dataclass(frozen=True)
class AppConfig:
    api_key: str | None
    base_url: str | None
    model: str
    max_tokens: int
    memory_dir: Path = Path.home() / ".mini-claude" / "memory"
    dream_interval_hours: float = 24.0
    dream_min_sessions: int = 5
    auto_dream: bool = True
    config_paths: tuple[Path, ...] = ()


def resolve_model(model: str | None) -> str:
    if not model:
        return DEFAULT_MODEL
    normalized = model.strip()
    return _MODEL_ALIASES.get(normalized, normalized)


def default_max_tokens_for_model(model: str | None) -> int:
    resolved = resolve_model(model)
    for prefix, limit in _MODEL_MAX_TOKENS:
        if resolved.startswith(prefix):
            return limit
    return _FALLBACK_MAX_TOKENS


def load_app_config(args: Namespace) -> AppConfig:
    file_values, config_paths = _load_file_values(args.config)
    env_values = _load_env_values()

    raw_model = args.model or env_values.get("model") or file_values.get("model")
    model = resolve_model(raw_model)

    raw_max_tokens = (
        args.max_tokens
        if args.max_tokens is not None
        else env_values.get("max_tokens", file_values.get("max_tokens"))
    )
    max_tokens = _parse_max_tokens(raw_max_tokens, default=default_max_tokens_for_model(model))

    raw_memory_dir = (
        getattr(args, "memory_dir", None)
        or env_values.get("memory_dir")
        or file_values.get("memory_dir")
    )
    memory_dir = Path(raw_memory_dir).expanduser() if raw_memory_dir else Path.home() / ".mini-claude" / "memory"

    raw_dream_interval = getattr(args, "dream_interval", None)
    if raw_dream_interval is None:
        raw_dream_interval = env_values.get("dream_interval_hours", file_values.get("dream_interval_hours"))
    dream_interval = float(raw_dream_interval) if raw_dream_interval is not None else 24.0

    raw_dream_min = getattr(args, "dream_min_sessions", None)
    if raw_dream_min is None:
        raw_dream_min = env_values.get("dream_min_sessions", file_values.get("dream_min_sessions"))
    dream_min_sessions = int(raw_dream_min) if raw_dream_min is not None else 5
    auto_dream = True
    raw_auto_dream = env_values.get("auto_dream", file_values.get("auto_dream"))
    if raw_auto_dream is not None:
        auto_dream = str(raw_auto_dream).lower() not in ("false", "0", "no")
    if getattr(args, "no_auto_dream", False):
        auto_dream = False

    return AppConfig(
        api_key=args.api_key or env_values.get("api_key") or file_values.get("api_key"),
        base_url=args.base_url or env_values.get("base_url") or file_values.get("base_url"),
        model=model,
        max_tokens=max_tokens,
        memory_dir=memory_dir,
        dream_interval_hours=dream_interval,
        dream_min_sessions=dream_min_sessions,
        auto_dream=auto_dream,
        config_paths=config_paths,
    )


def _load_file_values(explicit_path: str | None) -> tuple[dict[str, Any], tuple[Path, ...]]:
    values: dict[str, Any] = {}
    loaded_paths: list[Path] = []

    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise ValueError(f"Config file not found: {path}")
        values.update(_read_config_file(path))
        loaded_paths.append(path)
        return values, tuple(loaded_paths)

    for path in _DEFAULT_CONFIG_PATHS:
        if not path.exists():
            continue
        values.update(_read_config_file(path))
        loaded_paths.append(path)

    return values, tuple(loaded_paths)


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in config file {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read config file {path}: {exc}") from exc

    values: dict[str, Any] = {}
    anthropic_section = data.get("anthropic", {})
    if isinstance(anthropic_section, dict):
        values.update(anthropic_section)

    for key in ("api_key", "base_url", "model", "max_tokens", "memory_dir",
                 "dream_interval_hours", "dream_min_sessions", "auto_dream"):
        if key in data:
            values[key] = data[key]

    return values


def _load_env_values() -> dict[str, Any]:
    values: dict[str, Any] = {}
    if os.getenv("ANTHROPIC_API_KEY"):
        values["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("ANTHROPIC_BASE_URL"):
        values["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
    if os.getenv(_ENV_MODEL):
        values["model"] = os.environ[_ENV_MODEL]
    if os.getenv(_ENV_MAX_TOKENS):
        values["max_tokens"] = os.environ[_ENV_MAX_TOKENS]
    if os.getenv(_ENV_MEMORY_DIR):
        values["memory_dir"] = os.environ[_ENV_MEMORY_DIR]
    return values


def _parse_max_tokens(raw_value: Any, default: int) -> int:
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid max_tokens value: {raw_value!r}") from exc

    if value <= 0:
        raise ValueError("max_tokens must be a positive integer")
    return value
