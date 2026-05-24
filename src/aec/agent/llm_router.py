"""LLM router — resolves a PersonaSpec to a working transport and runs the completion."""
from __future__ import annotations

import logging

from aec.agent.models import PersonaSpec
from aec.agent.transports import CompletionResult, Transport
from aec.agent.transports.anthropic_api import AnthropicAPITransport
from aec.agent.transports.anthropic_cli import AnthropicCLITransport
from aec.agent.transports.gemini_api import GeminiAPITransport
from aec.agent.transports.gemini_cli import GeminiCLITransport
from aec.agent.transports.openai_api import OpenAIAPITransport
from aec.agent.transports.openai_cli import OpenAICLITransport
from aec.agent.transports.openrouter_api import OpenRouterAPITransport

log = logging.getLogger(__name__)

TRANSPORT_REGISTRY: dict[str, type[Transport]] = {
    "anthropic-cli": AnthropicCLITransport,
    "anthropic-api": AnthropicAPITransport,
    "openai-cli": OpenAICLITransport,
    "openai-api": OpenAIAPITransport,
    "gemini-cli": GeminiCLITransport,
    "gemini-api": GeminiAPITransport,
    "openrouter-api": OpenRouterAPITransport,
}

_transport_cache: dict[str, Transport] = {}


def _get_transport(name: str) -> Transport:
    if name not in _transport_cache:
        cls = TRANSPORT_REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown transport: {name!r}")
        _transport_cache[name] = cls()
    return _transport_cache[name]


async def detect_available() -> dict[str, bool]:
    """Probe all transports and return availability map."""
    result: dict[str, bool] = {}
    for name in TRANSPORT_REGISTRY:
        transport = _get_transport(name)
        try:
            result[name] = await transport.available()
        except Exception:
            result[name] = False
    return result


async def complete(
    persona: PersonaSpec,
    user_prompt: str,
) -> tuple[CompletionResult, bool]:
    """Try each transport in the persona's chain; return (result, fallback_used).

    Raises RuntimeError if every transport in the chain fails.
    """
    errors: list[tuple[str, str]] = []
    for i, tspec in enumerate(persona.transports):
        transport = _get_transport(tspec.name)
        try:
            ok = await transport.available()
            if not ok:
                errors.append((tspec.name, "not available"))
                log.info("Transport %s not available for %s, trying next", tspec.name, persona.persona)
                continue
        except Exception as exc:
            errors.append((tspec.name, f"availability check failed: {exc}"))
            continue

        model = tspec.config.get("model", "")
        try:
            result = await transport.complete(
                system_prompt=persona.system_prompt,
                user_prompt=user_prompt,
                model=model,
                temperature=persona.temperature,
            )
            fallback_used = i > 0
            if fallback_used:
                log.info(
                    "Persona %s used fallback transport %s (primary was %s)",
                    persona.persona,
                    tspec.name,
                    persona.transports[0].name,
                )
            return result, fallback_used
        except Exception as exc:
            errors.append((tspec.name, str(exc)))
            log.warning(
                "Transport %s failed for %s: %s",
                tspec.name,
                persona.persona,
                exc,
            )
            continue

    error_summary = "; ".join(f"{n}: {e}" for n, e in errors)
    raise RuntimeError(
        f"All transports exhausted for persona {persona.persona!r}: {error_summary}"
    )
