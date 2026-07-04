"""Slack Socket Mode listener.

Handles `app_mention` (channels) and `message.im` (DMs). For each incoming
question:
  1. 👀 reaction as an immediate ack.
  2. Post a placeholder message, then edit it with the answer (post-then-edit)
     so it feels responsive without hammering `chat.update`.
  3. Route the question through the thread's warm ClaudeSDKClient session.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict

from slack_bolt.app.async_app import AsyncApp

from .agent import collect_reply
from .config import Config
from .sessions import SessionPool

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_SEEN_MAX = 2000


def _clean_text(text: str) -> str:
    """Strip Slack user mentions (e.g. the bot @mention) and surrounding space."""
    return _MENTION_RE.sub("", text or "").strip()


def build_slack_app(config: Config, pool: SessionPool) -> AsyncApp:
    app = AsyncApp(token=config.slack_bot_token)

    # De-dupe events. Slack can deliver the same event twice (e.g. a DM that also
    # @mentions the bot fires both message.im and app_mention), and Socket Mode
    # may redeliver on retry. Bounded FIFO of recently handled event keys.
    seen: "OrderedDict[str, None]" = OrderedDict()

    def _already_seen(event: dict) -> bool:
        key = event.get("client_msg_id") or f"{event.get('channel')}:{event.get('ts')}"
        if key in seen:
            return True
        seen[key] = None
        if len(seen) > _SEEN_MAX:
            seen.popitem(last=False)
        return False

    async def _handle(event: dict, client) -> None:
        if _already_seen(event):
            return

        channel = event["channel"]
        user = event.get("user")
        message_ts = event["ts"]
        # Reply in-thread; a top-level message starts a thread on itself.
        thread_ts = event.get("thread_ts") or message_ts
        session_key = f"{channel}:{thread_ts}"
        question = _clean_text(event.get("text", ""))

        if not question:
            return

        # 1. 👀 ack (best-effort — never fail the request over a reaction).
        try:
            await client.reactions_add(channel=channel, timestamp=message_ts, name=config.ack_emoji)
        except Exception:
            logger.debug("reaction_add_failed", exc_info=True)

        # 2. Placeholder we will edit with the answer.
        placeholder = await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=config.placeholder_text
        )
        reply_ts = placeholder["ts"]

        # 3. Route through the thread's warm session.
        entry = await pool.acquire(session_key)
        try:
            async with entry.lock:
                await pool.ensure_connected(entry)
                await entry.client.query(question)
                reply, meta = await collect_reply(entry.client)

            logger.info(
                "answered",
                extra={
                    "extra_fields": {
                        "session_key": session_key,
                        "user": user,
                        "question_chars": len(question),
                        "reply_chars": len(reply),
                        **(meta or {}),
                    }
                },
            )
            if not reply:
                reply = "I couldn't produce an answer for that — mind rephrasing?"
            await client.chat_update(channel=channel, ts=reply_ts, text=reply)
        except Exception:
            logger.exception(
                "answer_failed",
                extra={"extra_fields": {"session_key": session_key, "user": user}},
            )
            # Rebuild this thread's session next time; a broken client won't recover.
            await pool.discard(session_key)
            try:
                await client.chat_update(
                    channel=channel,
                    ts=reply_ts,
                    text=":warning: Sorry — I hit an error answering that. Please try again.",
                )
            except Exception:
                logger.debug("error_update_failed", exc_info=True)

    @app.event("app_mention")
    async def on_app_mention(event, client):
        await _handle(event, client)

    @app.event("message")
    async def on_message(event, client):
        # Only genuine user DMs. Ignore channel messages (we act on @mentions
        # there), the bot's own echoes, and edits/joins/other subtypes.
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        await _handle(event, client)

    return app
