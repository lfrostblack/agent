"""Environment-driven configuration, validated at startup (fail fast)."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"Missing required environment variable: {name}")
    return val


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name} must be an integer, got {raw!r}"
        ) from exc


@dataclass(frozen=True)
class Config:
    # Required credentials
    slack_bot_token: str
    slack_app_token: str
    anthropic_api_key: str

    # Model
    agent_model: str
    max_turns: int

    # Warm session pool tuning
    session_max: int
    session_idle_ttl: int       # seconds since last use before eviction
    session_max_age: int        # seconds since creation before forced recycle
    session_sweep_interval: int  # seconds between sweeper passes

    # Behaviour
    log_level: str
    placeholder_text: str
    ack_emoji: str


def load_config() -> Config:
    # ANTHROPIC_API_KEY is consumed by the SDK directly from the environment; we
    # only validate its presence here so startup fails fast with a clear message.
    return Config(
        slack_bot_token=_require("SLACK_BOT_TOKEN"),
        slack_app_token=_require("SLACK_APP_TOKEN"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        agent_model=os.environ.get("AGENT_MODEL", "claude-sonnet-5"),
        max_turns=_get_int("AGENT_MAX_TURNS", 8),
        session_max=_get_int("SESSION_MAX", 200),
        session_idle_ttl=_get_int("SESSION_IDLE_TTL", 900),
        session_max_age=_get_int("SESSION_MAX_AGE", 3000),
        session_sweep_interval=_get_int("SESSION_SWEEP_INTERVAL", 60),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        placeholder_text=os.environ.get(
            "PLACEHOLDER_TEXT", "On it — looking into this… :hourglass_flowing_sand:"
        ),
        ack_emoji=os.environ.get("ACK_EMOJI", "eyes"),
    )
