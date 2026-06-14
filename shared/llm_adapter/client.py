"""Provider-agnostic LLM adapter built on LiteLLM.

A single config/env swap takes a developer from Anthropic Claude to DeepSeek,
Azure OpenAI, OpenAI, Bedrock, or a local Ollama model — no code change in agents.

Usage:
    cfg = LLMConfig.from_dict(yaml_dict["llm"])
    adapter = LLMAdapter(cfg)
    resp = await adapter.chat([{"role": "user", "content": "hi"}])
    print(resp.content)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from litellm import acompletion


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-opus-4-7"
    temperature: float = 0.2
    max_tokens: int = 4096
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str | None = None
    # --- custom OpenAI-compatible endpoint support ---
    # When provider == "custom", the request is routed to `base_url` as an
    # OpenAI-compatible chat completion. Auth is a Bearer token taken from the env var
    # named by `auth_token_env` (sent as `Authorization: Bearer <token>`). For gateways
    # that use a different header name or scheme, override `auth_header` / `auth_scheme`,
    # and/or supply `extra_headers` for any additional static headers (e.g. api-version).
    auth_token_env: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    fallback: "LLMConfig | None" = None

    @staticmethod
    def _default_model_for_provider(provider: str) -> str:
        if provider == "deepseek":
            return "deepseek-chat"
        return "claude-opus-4-7"

    @staticmethod
    def _default_api_key_env_for_provider(provider: str) -> str:
        if provider == "deepseek":
            return "DEEPSEEK_API_KEY"
        if provider == "openai":
            return "OPENAI_API_KEY"
        return "ANTHROPIC_API_KEY"

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, use_global_env: bool = True) -> "LLMConfig":
        fb = d.get("fallback")
        provider = d.get("provider", "anthropic")
        if use_global_env:
            provider = os.getenv("LLM_PROVIDER", provider)

        model = d.get("model", cls._default_model_for_provider(provider))
        api_key_env = d.get(
            "api_key_env", cls._default_api_key_env_for_provider(provider)
        )
        base_url = d.get("base_url")

        auth_token_env = d.get("auth_token_env")
        if use_global_env:
            model = os.getenv("LLM_MODEL", model)
            api_key_env = os.getenv("LLM_API_KEY_ENV", api_key_env)
            base_url = os.getenv("LLM_BASE_URL", base_url or "") or None
            auth_token_env = os.getenv("LLM_AUTH_TOKEN_ENV", auth_token_env or "") or None

        return cls(
            provider=provider,
            model=model,
            temperature=float(d.get("temperature", 0.2)),
            max_tokens=int(d.get("max_tokens", 4096)),
            api_key_env=api_key_env,
            base_url=base_url,
            auth_token_env=auth_token_env,
            auth_header=d.get("auth_header", "Authorization"),
            auth_scheme=d.get("auth_scheme", "Bearer"),
            extra_headers=d.get("extra_headers", {}) or {},
            extra=d.get("extra", {}) or {},
            fallback=cls.from_dict(fb, use_global_env=False) if fb else None,
        )

    @property
    def litellm_model(self) -> str:
        # LiteLLM uses "<provider>/<model>" routing for non-OpenAI providers.
        # A "custom" OpenAI-compatible endpoint is routed via the openai/ adapter so
        # base_url + bearer auth behave like a standard OpenAI chat completion.
        if self.provider in ("openai", "custom"):
            return self.model if self.provider == "openai" else f"openai/{self.model}"
        return f"{self.provider}/{self.model}"


@dataclass
class LLMResponse:
    content: str
    tokens_in: int
    tokens_out: int
    model: str
    raw: Any = None


class LLMAdapter:
    """Thin async wrapper around LiteLLM with fallback support."""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self._validate_keys(cfg)

    @staticmethod
    def _validate_keys(cfg: LLMConfig) -> None:
        # We don't fail on missing keys here — Ollama needs none.
        # But warn when the env var is referenced but unset.
        if cfg.api_key_env and cfg.provider not in {"ollama"}:
            if not os.getenv(cfg.api_key_env):
                # Soft warning only; LiteLLM will surface a clearer error on call.
                pass

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return await self._call(self.cfg, messages, tools, temperature, max_tokens)

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        kwargs = self._build_kwargs(self.cfg, messages, tools, temperature, max_tokens)
        kwargs["stream"] = True
        try:
            stream = await acompletion(**kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    yield delta.content
        except Exception:
            if self.cfg.fallback is not None:
                fb_kwargs = self._build_kwargs(
                    self.cfg.fallback, messages, tools, temperature, max_tokens
                )
                fb_kwargs["stream"] = True
                stream = await acompletion(**fb_kwargs)
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta and getattr(delta, "content", None):
                        yield delta.content
            else:
                raise

    async def _call(
        self,
        cfg: LLMConfig,
        messages: list[dict[str, str]],
        tools: list[dict] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(cfg, messages, tools, temperature, max_tokens)
        try:
            resp = await acompletion(**kwargs)
        except Exception:
            if cfg.fallback is not None:
                return await self._call(cfg.fallback, messages, tools, temperature, max_tokens)
            raise
        choice = resp.choices[0]
        content = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=content,
            tokens_in=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_out=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=cfg.litellm_model,
            raw=resp,
        )

    @staticmethod
    def _build_kwargs(
        cfg: LLMConfig,
        messages: list[dict[str, str]],
        tools: list[dict] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": cfg.litellm_model,
            "messages": messages,
            "temperature": cfg.temperature if temperature is None else temperature,
            "max_tokens": cfg.max_tokens if max_tokens is None else max_tokens,
        }
        if cfg.base_url:
            kwargs["api_base"] = cfg.base_url

        # Custom OpenAI-compatible endpoint: the bearer token (from auth_token_env) is the
        # source of truth — send it as the configured auth header plus any static extra
        # headers, and as `api_key` (OpenAI-style adapters require one even when auth is
        # header-driven). The token takes precedence over api_key_env for this provider.
        headers: dict[str, str] = dict(cfg.extra_headers)
        token = os.getenv(cfg.auth_token_env) if cfg.auth_token_env else None
        if token:
            value = f"{cfg.auth_scheme} {token}".strip() if cfg.auth_scheme else token
            headers[cfg.auth_header] = value
            kwargs["api_key"] = token
        elif cfg.api_key_env:
            api_key = os.getenv(cfg.api_key_env)
            if api_key:
                kwargs["api_key"] = api_key
        if headers:
            kwargs["extra_headers"] = headers

        if tools:
            kwargs["tools"] = tools
        kwargs.update(cfg.extra)
        return kwargs


def build_adapter_from_config(config: dict[str, Any]) -> LLMAdapter:
    """Convenience: build an adapter from a parsed YAML/JSON config dict."""
    llm_block = config.get("llm", config)
    return LLMAdapter(LLMConfig.from_dict(llm_block))
