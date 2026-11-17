"""Provider-agnostic chat model construction.

Model specs are `"<model>"` (uses `LLM_PROVIDER`, default openai) or
`"<provider>:<model>"` (e.g. `anthropic:claude-sonnet-4-5`,
`google_genai:gemini-2.5-flash`). OpenAI models are built with `ChatOpenAI`
directly so behaviour is unchanged; other providers go through LangChain's
`init_chat_model`, which needs the matching integration package installed
(`langchain-anthropic`, `langchain-google-genai`, ...).

Nothing here enables a paid non-OpenAI provider by itself: the defaults stay
on OpenAI and a second provider is only used if `CHAT_MODEL` /
`LLM_FALLBACK_MODEL` explicitly name one.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from app.config import settings

_OPENAI_ALIASES = {"openai", "oai"}


def parse_model_spec(spec: str, default_provider: str | None = None) -> tuple[str, str]:
    """`"anthropic:claude-x"` -> ("anthropic", "claude-x"); `"gpt-4o"` -> (provider, "gpt-4o")."""
    spec = (spec or "").strip()
    provider = (default_provider or settings.llm_provider or "openai").strip().lower()
    if ":" in spec:
        head, tail = spec.split(":", 1)
        if head and tail and " " not in head and "/" not in head:
            return head.strip().lower(), tail.strip()
    return provider, spec


def model_name(spec: str) -> str:
    """Bare model name (no provider prefix) for pricing tables and logs."""
    return parse_model_spec(spec)[1]


def make_chat_model(spec: str, **kwargs: Any) -> BaseChatModel:
    provider, model = parse_model_spec(spec)
    if provider in _OPENAI_ALIASES:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=settings.openai_api_key, **kwargs)
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as e:  # pragma: no cover - langchain is a hard dependency
        raise RuntimeError("langchain is required for non-OpenAI providers") from e
    try:
        return init_chat_model(model, model_provider=provider, **kwargs)
    except ImportError as e:
        raise RuntimeError(
            f"LLM provider '{provider}' needs its LangChain integration package installed "
            f"(e.g. pip install langchain-{provider.replace('_', '-')}): {e}"
        ) from e


def with_fallback(primary, fallback):
    """`primary` with `fallback` tried on error; passthrough when no fallback."""
    if fallback is None:
        return primary
    return primary.with_fallbacks([fallback])
