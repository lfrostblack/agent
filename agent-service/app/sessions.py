"""Warm, per-thread ClaudeSDKClient pool.

This is the single biggest latency win over one-shot `query()`: each Slack thread
(`channel:thread_ts`) keeps a persistent, connected `ClaudeSDKClient` — so the CLI
subprocess stays warm and the client carries that thread's conversation context.

Design notes:
  - A per-key `asyncio.Lock` serialises messages within a thread. A single client
    cannot service concurrent queries, and we want to preserve message order.
  - The global map guard is held only for cheap map operations. The expensive
    `connect()` happens lazily under the per-key lock (outside the guard), so
    creating a session for one thread never blocks other threads.
  - An LRU cap bounds the number of live subprocesses.
  - A background sweeper evicts idle sessions and force-recycles sessions older
    than `max_age`, so we stay ahead of external MCP token expiry (relevant once
    Nominal is wired in). Disconnects are scheduled off the hot path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    key: str
    client: ClaudeSDKClient
    connected: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)


class SessionPool:
    def __init__(
        self,
        options_factory: Callable[[], ClaudeAgentOptions],
        *,
        max_sessions: int,
        idle_ttl: float,
        max_age: float,
    ) -> None:
        self._options_factory = options_factory
        self._max_sessions = max_sessions
        self._idle_ttl = idle_ttl
        self._max_age = max_age
        self._entries: dict[str, SessionEntry] = {}
        self._guard = asyncio.Lock()  # protects _entries
        self._sweeper: Optional[asyncio.Task] = None
        self._closing = False

    async def acquire(self, key: str) -> SessionEntry:
        """Return the entry for `key`, creating (but not yet connecting) one.

        Construction is cheap; the caller must `ensure_connected()` under the
        entry's lock before using the client.
        """
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = SessionEntry(
                    key=key, client=ClaudeSDKClient(options=self._options_factory())
                )
                self._entries[key] = entry
                entry.last_used = time.monotonic()
                self._evict_lru_locked()
            else:
                entry.last_used = time.monotonic()
            return entry

    async def ensure_connected(self, entry: SessionEntry) -> None:
        """Connect the entry's client if not already connected.

        Must be called while holding `entry.lock`.
        """
        if entry.connected:
            return
        await entry.client.connect()
        entry.connected = True
        logger.info(
            "session_connected",
            extra={"extra_fields": {"session_key": entry.key, "live": len(self._entries)}},
        )

    async def discard(self, key: str) -> None:
        """Drop and disconnect a session (e.g. after an error), so it is rebuilt."""
        async with self._guard:
            entry = self._entries.pop(key, None)
        if entry is not None:
            await self._disconnect(entry)

    # ---- internals ----

    def _evict_lru_locked(self) -> None:
        # Caller holds self._guard. Evict least-recently-used unlocked sessions
        # until we're back under the cap. Disconnect off the hot path.
        while len(self._entries) > self._max_sessions:
            victim = next(
                (
                    e
                    for e in sorted(self._entries.values(), key=lambda e: e.last_used)
                    if not e.lock.locked()
                ),
                None,
            )
            if victim is None:
                break  # everything busy; allow a brief overshoot
            self._entries.pop(victim.key, None)
            asyncio.create_task(self._disconnect(victim))
            logger.info("session_evicted_lru", extra={"extra_fields": {"session_key": victim.key}})

    async def _disconnect(self, entry: SessionEntry) -> None:
        if not entry.connected:
            return
        entry.connected = False
        try:
            await entry.client.disconnect()
        except Exception:
            logger.warning(
                "session_disconnect_failed",
                extra={"extra_fields": {"session_key": entry.key}},
                exc_info=True,
            )
        else:
            logger.info("session_closed", extra={"extra_fields": {"session_key": entry.key}})

    async def _sweep_once(self) -> None:
        now = time.monotonic()
        expired: list[SessionEntry] = []
        async with self._guard:
            for entry in list(self._entries.values()):
                if entry.lock.locked():
                    continue
                idle = now - entry.last_used > self._idle_ttl
                aged = now - entry.created_at > self._max_age
                if idle or aged:
                    self._entries.pop(entry.key, None)
                    expired.append(entry)
        for entry in expired:
            await self._disconnect(entry)
        if expired:
            logger.info(
                "session_sweep",
                extra={"extra_fields": {"evicted": len(expired), "live": len(self._entries)}},
            )

    async def start_sweeper(self, interval: float) -> None:
        async def _run() -> None:
            while not self._closing:
                try:
                    await asyncio.sleep(interval)
                    await self._sweep_once()
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.warning("session_sweep_error", exc_info=True)

        self._sweeper = asyncio.create_task(_run())

    async def aclose(self) -> None:
        self._closing = True
        if self._sweeper is not None:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except asyncio.CancelledError:
                pass
        async with self._guard:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await self._disconnect(entry)
