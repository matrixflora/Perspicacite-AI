"""Adapter for LLM clients to provide simple complete() interface."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from perspicacite.llm import AsyncLLMClient


class LLMAdapter:
    """Simple adapter that wraps AsyncLLMClient with complete(prompt) interface."""

    def __init__(
        self,
        client: "AsyncLLMClient",
        model: str | None = None,
        provider: str | None = None
    ):
        self.client = client
        self.model = model
        self.provider = provider

    async def complete(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        *,
        chunks: Any = None,
        config: Any = None,
    ) -> str:
        """
        Simple completion interface.

        Args:
            prompt: The prompt text
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            chunks: Optional iterable of DocumentChunk objects whose figure_refs may
                be wrapped into the user message as image attachments (multimodal).
            config: Optional Perspicacité Config (required alongside ``chunks`` to
                enable the multimodal wrap path).

        Returns:
            Generated text
        """
        base_messages = [{"role": "user", "content": prompt}]
        messages = base_messages

        if chunks is not None and config is not None:
            # Best-effort multimodal wrap; falls back to text when feature is
            # disabled, model isn't vision-capable, or no figures resolve.
            from perspicacite.rag.multimodal import wrap_messages_for_chunks
            messages = wrap_messages_for_chunks(
                base_messages=base_messages,
                chunks=chunks,
                model=self.model,
                config=config,
            )

        return await self.client.complete(
            messages=messages,
            model=self.model,
            provider=self.provider,
            temperature=temperature,
            max_tokens=max_tokens
        )
