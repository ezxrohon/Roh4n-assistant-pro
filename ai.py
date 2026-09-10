"""
Thin wrapper around the Anthropic API for the optional AI auto-chat feature.

Requires the ANTHROPIC_API_KEY environment variable. If it's not set, AI
chat mode simply can't be turned on (bot.py checks for this before
enabling it).
"""

import os

from anthropic import Anthropic

AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-4-5")
AI_SYSTEM_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    "You are a helpful assistant answering Telegram messages on behalf of "
    "the bot's owner while they're away. Be warm, concise, and clear that "
    "you're an AI stand-in if it comes up. For anything you can't actually "
    "answer or resolve, say a human will follow up soon.",
)

_client = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic(api_key=api_key)
    return _client


def generate_reply(history: list[dict]) -> str:
    """
    history: list of {"role": "user"|"assistant", "content": str}, oldest
    message first, most recent last. Returns the generated reply text.
    """
    client = _get_client()
    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=500,
        system=AI_SYSTEM_PROMPT,
        messages=history,
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts).strip() or "…"
