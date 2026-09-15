"""
AI backend for the bot's auto-chat feature.

Supports two provider modes, chosen via provider setting:
- "anthropic" (default): the native Anthropic API. No permanent free tier;
  needs an API key and, eventually, billing set up.
- "openai_compatible": any provider that speaks the OpenAI chat-completions
  format. This covers several providers with genuinely free tiers and no
  credit card required, e.g.:

    Groq        AI_BASE_URL=https://api.groq.com/openai/v1
                AI_MODEL=llama-3.3-70b-versatile
    Gemini      AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
                AI_MODEL=gemini-2.5-flash
    OpenRouter  AI_BASE_URL=https://openrouter.ai/api/v1
                AI_MODEL=<pick any model with a ":free" suffix>

  In this mode set the api key to that provider's key (not the Anthropic key).
  Free tiers change often and carry rate limits - check the provider's
  current docs before relying on one in production.

Configuration precedence (highest first):
1. Values set at runtime by the admin via /setai (stored in the DB through
   storage.py, so they persist across restarts and survive redeploys as
   long as the SQLite file does).
2. Environment variables (AI_PROVIDER, AI_MODEL, AI_API_KEY / ANTHROPIC_API_KEY,
   AI_BASE_URL, AI_SYSTEM_PROMPT), set once at deploy time.
3. Hardcoded defaults below.
"""

import os

import storage

_DEFAULT_MODEL = "claude-sonnet-4-5"
_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant answering Telegram messages on behalf of "
    "the bot's owner while they're away. Be warm, concise, and clear that "
    "you're an AI stand-in if it comes up. For anything you can't actually "
    "answer or resolve, say a human will follow up soon."
)


def _cfg(setting_key: str, env_key: str, default: str | None = None) -> str | None:
    """DB setting takes priority over env var, which takes priority over default."""
    value = storage.get_setting(setting_key)
    if value:
        return value
    value = os.environ.get(env_key)
    if value:
        return value
    return default


def get_provider() -> str:
    return (_cfg("ai_provider", "AI_PROVIDER", "anthropic") or "anthropic").lower()


def get_model() -> str:
    return _cfg("ai_model", "AI_MODEL", _DEFAULT_MODEL)


def get_system_prompt() -> str:
    return _cfg("ai_system_prompt", "AI_SYSTEM_PROMPT", _DEFAULT_SYSTEM_PROMPT)


def get_base_url() -> str | None:
    return _cfg("ai_base_url", "AI_BASE_URL")


def _get_api_key(provider: str) -> str | None:
    if provider == "anthropic":
        return _cfg("ai_api_key", "ANTHROPIC_API_KEY")
    return _cfg("ai_api_key", "AI_API_KEY")


def is_configured() -> bool:
    """Whether we currently have what the active provider needs to run."""
    provider = get_provider()
    if provider == "anthropic":
        return bool(_get_api_key(provider))
    if provider == "openai_compatible":
        return bool(_get_api_key(provider)) and bool(get_base_url())
    return False


def current_config_summary() -> dict:
    """Masked snapshot of the active config, safe to show in chat."""
    provider = get_provider()
    key = _get_api_key(provider)
    masked = f"{key[:4]}...{key[-4:]}" if key and len(key) > 8 else ("set" if key else "not set")
    return {
        "provider": provider,
        "model": get_model(),
        "base_url": get_base_url() or "-",
        "api_key": masked,
    }


def _get_anthropic_client(api_key: str):
    from anthropic import Anthropic

    return Anthropic(api_key=api_key)


def _get_openai_compatible_client(api_key: str, base_url: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url)


def generate_reply(history: list[dict]) -> str:
    """
    history: list of {"role": "user"|"assistant", "content": str}, oldest
    message first, most recent last. Returns the generated reply text.

    Config (provider/model/key) is re-read on every call so that changes
    made via /setai take effect immediately, without restarting the bot.
    """
    provider = get_provider()
    model = get_model()
    system_prompt = get_system_prompt()
    api_key = _get_api_key(provider)

    if provider == "anthropic":
        if not api_key:
            raise RuntimeError("No Anthropic API key configured. Set it with /setai key=<key>.")
        client = _get_anthropic_client(api_key)
        response = client.messages.create(
            model=model,
            max_tokens=500,
            system=system_prompt,
            messages=history,
        )
        parts = [block.text for block in response.content if block.type == "text"]
        return "".join(parts).strip() or "…"

    if provider == "openai_compatible":
        base_url = get_base_url()
        if not api_key:
            raise RuntimeError("No AI API key configured. Set it with /setai key=<key>.")
        if not base_url:
            raise RuntimeError("No AI base URL configured. Set it with /setai base_url=<url>.")
        client = _get_openai_compatible_client(api_key, base_url)
        messages = [{"role": "system", "content": system_prompt}, *history]
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
        )
        return (response.choices[0].message.content or "").strip() or "…"

    raise RuntimeError(f"Unknown provider: {provider!r} (use 'anthropic' or 'openai_compatible')")
