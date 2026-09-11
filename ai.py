"""
AI backend for the bot's auto-chat feature.

Supports two provider modes, chosen via AI_PROVIDER:

- "anthropic" (default): the native Anthropic API. No permanent free tier;
  needs ANTHROPIC_API_KEY and, eventually, billing set up.

- "openai_compatible": any provider that speaks the OpenAI chat-completions
  format. This covers several providers with genuinely free tiers and no
  credit card required, e.g.:

    Groq        AI_BASE_URL=https://api.groq.com/openai/v1
                AI_MODEL=llama-3.3-70b-versatile

    Gemini      AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
                AI_MODEL=gemini-2.5-flash

    OpenRouter  AI_BASE_URL=https://openrouter.ai/api/v1
                AI_MODEL=<pick any model with a ":free" suffix>

  In this mode set AI_API_KEY to that provider's key (not ANTHROPIC_API_KEY).

Free tiers change often and carry rate limits — check the provider's
current docs before relying on one in production.
"""

import os

AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()
AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-4-5")
AI_SYSTEM_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    "You are a helpful assistant answering Telegram messages on behalf of "
    "the bot's owner while they're away. Be warm, concise, and clear that "
    "you're an AI stand-in if it comes up. For anything you can't actually "
    "answer or resolve, say a human will follow up soon.",
)

_client = None


def is_configured() -> bool:
    """Whether the env has what this provider mode needs to run at all."""
    if AI_PROVIDER == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if AI_PROVIDER == "openai_compatible":
        return bool(os.environ.get("AI_API_KEY")) and bool(os.environ.get("AI_BASE_URL"))
    return False


def _get_anthropic_client():
    from anthropic import Anthropic

    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic(api_key=api_key)
    return _client


def _get_openai_compatible_client():
    from openai import OpenAI

    global _client
    if _client is None:
        api_key = os.environ.get("AI_API_KEY")
        base_url = os.environ.get("AI_BASE_URL")
        if not api_key:
            raise RuntimeError("AI_API_KEY is not set")
        if not base_url:
            raise RuntimeError("AI_BASE_URL is not set")
        _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def generate_reply(history: list[dict]) -> str:
    """
    history: list of {"role": "user"|"assistant", "content": str}, oldest
    message first, most recent last. Returns the generated reply text.
    """
    if AI_PROVIDER == "anthropic":
        client = _get_anthropic_client()
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=500,
            system=AI_SYSTEM_PROMPT,
            messages=history,
        )
        parts = [block.text for block in response.content if block.type == "text"]
        return "".join(parts).strip() or "…"

    if AI_PROVIDER == "openai_compatible":
        client = _get_openai_compatible_client()
        messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}, *history]
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            max_tokens=500,
        )
        return (response.choices[0].message.content or "").strip() or "…"

    raise RuntimeError(f"Unknown AI_PROVIDER: {AI_PROVIDER!r} (use 'anthropic' or 'openai_compatible')")
