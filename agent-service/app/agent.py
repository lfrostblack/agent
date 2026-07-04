"""Claude Agent SDK wiring: options builder + response extraction.

Minimal phase (steps 1-2): the agent has NO tools and answers from the model
directly. Later phases extend this module:
  - step 3: add the in-process `create_ticket` tool + PreToolUse audit hook and
            switch `allowed_tools` to the explicit tool allowlist.
  - step 5: register the Nominal HTTP MCP server + the routing system prompt.
"""

from __future__ import annotations

import logging

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from .config import Config

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are Warpspeed's internal support assistant, operating inside Slack.

Help teammates by answering their questions clearly, accurately, and concisely.
Keep answers short and skimmable — this is a chat surface, not a document. Use
light Slack formatting (bullets, `code`, *bold*) where it aids readability. Never
invent facts; if you are unsure or lack the information, say so plainly.

(Knowledge-base search and ticket filing are connected in later phases.)
"""


def build_options(config: Config) -> ClaudeAgentOptions:
    """Construct the locked-down options for a support session.

    `allowed_tools=[]` restricts the session to that (empty) list, which blocks
    the built-in Bash/Read/Write/Edit tools. We also deliberately do NOT set
    `setting_sources`, so no filesystem settings, CLAUDE.md, or project tools are
    loaded into the session. Together these give the no-filesystem posture from
    day one. Steps 3 & 5 replace the empty allowlist with the explicit Nominal +
    ticket tool names and attach `mcp_servers` / `hooks`.
    """
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=config.agent_model,
        allowed_tools=[],  # no tools yet; locks out built-ins
        max_turns=config.max_turns,
    )


async def collect_reply(client: ClaudeSDKClient) -> tuple[str, dict]:
    """Drain one response turn into (text, result_metadata).

    `receive_response()` yields messages until (and including) the terminating
    ResultMessage. We concatenate the text blocks and capture cost/usage metadata
    for logging.
    """
    chunks: list[str] = []
    meta: dict = {}
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
        elif isinstance(msg, ResultMessage):
            meta = {
                "total_cost_usd": getattr(msg, "total_cost_usd", None),
                "duration_ms": getattr(msg, "duration_ms", None),
                "num_turns": getattr(msg, "num_turns", None),
                "is_error": getattr(msg, "is_error", None),
            }
    return "".join(chunks).strip(), meta
