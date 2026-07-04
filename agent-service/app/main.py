"""Entrypoint: wire config, logging, the warm session pool, and the Slack
Socket Mode listener, then run the always-on worker loop."""

from __future__ import annotations

import asyncio
import logging
import os

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .agent import build_options
from .config import load_config
from .logging_setup import setup_logging
from .sessions import SessionPool
from .slack_app import build_slack_app

logger = logging.getLogger(__name__)


async def _amain() -> None:
    # Set up logging before anything else so config errors are emitted as JSON.
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    config = load_config()

    logger.info(
        "starting",
        extra={
            "extra_fields": {
                "model": config.agent_model,
                "session_max": config.session_max,
                "session_idle_ttl": config.session_idle_ttl,
                "session_max_age": config.session_max_age,
            }
        },
    )

    pool = SessionPool(
        options_factory=lambda: build_options(config),
        max_sessions=config.session_max,
        idle_ttl=config.session_idle_ttl,
        max_age=config.session_max_age,
    )
    await pool.start_sweeper(config.session_sweep_interval)

    app = build_slack_app(config, pool)
    handler = AsyncSocketModeHandler(app, config.slack_app_token)

    try:
        await handler.start_async()  # runs until the process is stopped
    finally:
        await pool.aclose()


def main() -> None:
    try:
        asyncio.run(_amain())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
