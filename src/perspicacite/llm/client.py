"""Async LLM client using LiteLLM."""

import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.logging import get_logger

logger = get_logger("perspicacite.llm")


class LLMClient(Protocol):
    """Protocol for LLM clients."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        provider: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str: ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        provider: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...


class AsyncLLMClient:
    """
    Async LLM client using LiteLLM.

    Features:
    - Multi-provider support (OpenAI, Anthropic, DeepSeek, Gemini, etc.)
    - Automatic retries with exponential backoff
    - Streaming and non-streaming completion
    - Structured logging of all calls
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._litellm = None

    def _get_litellm(self) -> Any:
        """Lazy import litellm."""
        if self._litellm is None:
            import litellm

            # Configure litellm
            litellm.set_verbose = False
            litellm.drop_params = True  # Drop unsupported params
            self._litellm = litellm
        return self._litellm

    def _get_provider_config(self, provider: str) -> LLMProviderConfig:
        """Get configuration for a provider."""
        if provider not in self.config.providers:
            raise ValueError(f"Unknown provider: {provider}")
        return self.config.providers[provider]

    def _build_model_string(self, provider: str, model: str) -> str:
        """Build the model string for LiteLLM (e.g., 'anthropic/claude-3-5-sonnet')."""
        # LiteLLM format: provider/model
        # For Minimax, the actual API call uses minimax/{model} format directly
        return f"{provider}/{model}"

    @retry(
        retry=retry_if_exception_type((
            Exception,  # LiteLLM raises generic exceptions for API errors
        )),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """
        Complete a conversation with the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name (e.g., 'claude-3-5-sonnet-20241022'). Uses default if None.
            provider: Provider name (e.g., 'anthropic'). Uses default if None.
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional parameters for the provider

        Returns:
            Generated text

        Raises:
            Exception: If the API call fails after retries
        """
        if provider is None:
            provider = self.config.default_provider
        if model is None:
            model = self.config.default_model

        stage_label = kwargs.pop("stage", "llm")

        provider_config = self._get_provider_config(provider)
        model_str = self._build_model_string(provider, model)

        logger.info(
            "llm_completion_start",
            provider=provider,
            model=model,
            message_count=len(messages),
            temperature=temperature,
        )

        try:
            litellm = self._get_litellm()

            # Prepare API call parameters
            completion_kwargs = {
                "model": model_str,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": provider_config.timeout,
            }

            # TODO: Minimax implementation needs fixes
            # There are response parsing issues with the Anthropic-compatible API
            # Consider using DeepSeek or other providers as alternative
            # Special handling for Minimax (Anthropic-compatible API via LiteLLM)
            if provider == "minimax":
                import os
                # For Minimax, use standard acompletion with api_base
                # Model format: minimax/MiniMax-M2.7 (or just MiniMax-M2.7 with custom api_base)
                minimax_api_key = os.environ.get("MINIMAX_API_KEY")
                if not minimax_api_key:
                    raise ValueError("MINIMAX_API_KEY environment variable not set")

                t0 = time.monotonic()
                response = await litellm.acompletion(
                    model=f"minimax/{model}",  # Use minimax/MiniMax-M2.7 format
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=provider_config.timeout,
                    api_key=minimax_api_key,
                    api_base=provider_config.base_url,
                    **kwargs,
                )
                latency_ms = (time.monotonic() - t0) * 1000.0
                # Standard OpenAI-compatible response format
                content = response.choices[0].message.content
                usage = response.get("usage", {})

                logger.info(
                    "llm_completion_success",
                    provider=provider,
                    model=model,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    content_length=len(content),
                )
                from perspicacite.provenance.context import get_collector
                _c = get_collector()
                if _c is not None:
                    _c.add_llm_call(
                        stage_label=stage_label,
                        provider=provider,
                        model=model,
                        prompt_messages=messages,
                        response_text=content or "",
                        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                        latency_ms=latency_ms,
                    )
                return content

            completion_kwargs.update(kwargs)

            t0 = time.monotonic()
            response = await litellm.acompletion(**completion_kwargs)
            latency_ms = (time.monotonic() - t0) * 1000.0

            content = response.choices[0].message.content
            usage = response.get("usage", {})

            logger.info(
                "llm_completion_success",
                provider=provider,
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                content_length=len(content),
            )
            from perspicacite.provenance.context import get_collector
            _c = get_collector()
            if _c is not None:
                _c.add_llm_call(
                    stage_label=stage_label,
                    provider=provider,
                    model=model,
                    prompt_messages=messages,
                    response_text=content or "",
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    latency_ms=latency_ms,
                )

            return content

        except Exception as e:
            logger.error(
                "llm_completion_error",
                provider=provider,
                model=model,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        Stream completion from the LLM.

        Args:
            messages: List of message dicts
            model: Model name
            provider: Provider name. Uses default if None.
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional parameters

        Yields:
            Text chunks as they are generated
        """
        if provider is None:
            provider = self.config.default_provider

        stage_label = kwargs.pop("stage", "llm")

        provider_config = self._get_provider_config(provider)
        model_str = self._build_model_string(provider, model)

        logger.info(
            "llm_stream_start",
            provider=provider,
            model=model,
            message_count=len(messages),
        )

        try:
            litellm = self._get_litellm()

            # TODO: Minimax implementation needs fixes
            # Special handling for Minimax
            if provider == "minimax":
                import os
                # Use standard acompletion with api_base for streaming
                minimax_api_key = os.environ.get("MINIMAX_API_KEY")
                if not minimax_api_key:
                    raise ValueError("MINIMAX_API_KEY environment variable not set")

                t0 = time.monotonic()
                stream = await litellm.acompletion(
                    model=f"minimax/{model}",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=provider_config.timeout,
                    api_key=minimax_api_key,
                    api_base=provider_config.base_url,
                    stream=True,
                    **kwargs,
                )

                accum: list[str] = []
                async for chunk in stream:
                    piece = chunk.choices[0].delta.content
                    if piece:
                        accum.append(piece)
                        yield piece

                latency_ms = (time.monotonic() - t0) * 1000.0
                logger.info("llm_stream_complete", provider=provider, model=model)
                from perspicacite.provenance.context import get_collector
                _c = get_collector()
                if _c is not None:
                    _c.add_llm_call(
                        stage_label=stage_label,
                        provider=provider,
                        model=model,
                        prompt_messages=messages,
                        response_text="".join(accum),
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency_ms,
                    )
                return

            # Standard OpenAI-compatible streaming
            completion_kwargs = {
                "model": model_str,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": provider_config.timeout,
                "stream": True,
            }
            completion_kwargs.update(kwargs)

            t0 = time.monotonic()
            response = await litellm.acompletion(**completion_kwargs)

            accum2: list[str] = []
            async for chunk in response:
                piece = chunk.choices[0].delta.content
                if piece:
                    accum2.append(piece)
                    yield piece

            latency_ms = (time.monotonic() - t0) * 1000.0
            logger.info("llm_stream_complete", provider=provider, model=model)
            from perspicacite.provenance.context import get_collector
            _c = get_collector()
            if _c is not None:
                _c.add_llm_call(
                    stage_label=stage_label,
                    provider=provider,
                    model=model,
                    prompt_messages=messages,
                    response_text="".join(accum2),
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=latency_ms,
                )

        except Exception as e:
            logger.error(
                "llm_stream_error",
                provider=provider,
                model=model,
                error=str(e),
            )
            raise

    async def complete_with_fallback(
        self,
        messages: list[dict[str, str]],
        primary_model: str,
        primary_provider: str | None = None,
        fallback_model: str | None = None,
        fallback_provider: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Complete with automatic fallback on failure.

        Args:
            messages: List of messages
            primary_model: Primary model to try
            primary_provider: Primary provider
            fallback_model: Fallback model if primary fails
            fallback_provider: Fallback provider
            **kwargs: Additional parameters

        Returns:
            Generated text from primary or fallback
        """
        try:
            return await self.complete(
                messages=messages,
                model=primary_model,
                provider=primary_provider,
                **kwargs,
            )
        except Exception as e:
            logger.warning(
                "llm_primary_failed",
                primary_model=primary_model,
                error=str(e),
                fallback_model=fallback_model,
            )

            if fallback_model is None:
                raise

            return await self.complete(
                messages=messages,
                model=fallback_model,
                provider=fallback_provider,
                **kwargs,
            )
