"""LLM provider abstraction layer.

Factory for creating OpenAI-compatible clients across different backends
(OpenRouter, Ollama, OpenAI, or any custom endpoint).
"""

import os

from openai import AsyncOpenAI, OpenAI
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

PROVIDERS = {
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "env_key": "OPENROUTER_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "env_key": "OPENAI_API_KEY"},
}


def create_clients(
    provider: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    service_id: str = "default",
) -> tuple[OpenAI, AsyncOpenAI, OpenAIChatCompletion]:
    """Create sync client, async client, and SK chat completion service.

    Args:
        provider: Provider name ("ollama", "openrouter", "openai") or any string
                  if base_url and api_key are both provided.
        model: Model identifier (e.g. "llama3.2-vision", "openai/gpt-4o-mini").
        api_key: Override the API key (skips env var lookup).
        base_url: Override the base URL from the provider preset.
        service_id: Semantic Kernel service ID.

    Returns:
        Tuple of (OpenAI, AsyncOpenAI, OpenAIChatCompletion).
    """
    preset = PROVIDERS.get(provider, {})

    resolved_base_url = base_url or preset.get("base_url")
    if resolved_base_url is None:
        raise ValueError(
            f"Unknown provider {provider!r} and no base_url supplied. "
            f"Known providers: {', '.join(PROVIDERS)}"
        )

    if api_key is None:
        # Static key (e.g. ollama) or env var lookup
        api_key = preset.get("api_key") or os.environ.get(preset.get("env_key", ""), "")
        if not api_key:
            env_key = preset.get("env_key", "")
            raise ValueError(
                f"No api_key provided and ${env_key} is not set."
            )

    sync_client = OpenAI(api_key=api_key, base_url=resolved_base_url)
    async_client = AsyncOpenAI(api_key=api_key, base_url=resolved_base_url)
    chat_completion_service = OpenAIChatCompletion(
        service_id=service_id,
        ai_model_id=model,
        async_client=async_client,
    )

    return sync_client, async_client, chat_completion_service
