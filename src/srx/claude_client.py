"""Anthropic SDK wrapper (spec §5, §8): forced tool use for structured output,
exponential-backoff retry, and cache-aware per-call cost accounting. No sampling
parameters are sent (spec §5 amendment, 2026-07-10).
Tests inject a fake SDK; nothing here may be network-hit in tests."""
from __future__ import annotations

import base64
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Optional

import anthropic

# USD per million tokens (input, output). Verify against
# https://docs.anthropic.com/en/docs/about-claude/pricing and edit as needed;
# projects can override via config.yaml "pricing:".
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

RETRY_STATUSES = {429, 500, 502, 503, 529}


class ClaudeError(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens


def pdf_document_block(pdf_bytes: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
        "cache_control": {"type": "ephemeral"},
    }


class ClaudeClient:
    def __init__(
        self,
        client: Optional[Any] = None,
        pricing: Optional[dict[str, tuple[float, float]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client if client is not None else anthropic.Anthropic()
        self._pricing = DEFAULT_PRICING if pricing is None else pricing
        self._sleep = sleep

    def _cost(self, model: str, usage: "Usage") -> float:
        if model not in self._pricing:
            warnings.warn(f"no pricing for model '{model}'; costing 0.0 — edit pricing config")
            return 0.0
        in_price, out_price = self._pricing[model]
        # Cache tokens are excluded from usage.input_tokens by the API and are
        # billed at 1.25x (creation) / 0.1x (read) of the input price — without
        # them the cost cap fails open on PDF-heavy runs.
        effective_in = (
            usage.input_tokens
            + 1.25 * usage.cache_creation_input_tokens
            + 0.1 * usage.cache_read_input_tokens
        )
        return effective_in / 1e6 * in_price + usage.output_tokens / 1e6 * out_price

    def tool_call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 8192,
        max_retries: int = 4,
    ) -> tuple[dict, Usage]:
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                # No sampling params: the current API rejects non-default temperature
                # on the Fable/Sonnet 5 models (400). Reproducibility comes from
                # prompt-version pinning + benchmark regression (spec §5, amended
                # 2026-07-10), not from temperature pinning.
                resp = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    tools=[{
                        "name": tool_name,
                        "description": "Record the structured result.",
                        "input_schema": tool_schema,
                    }],
                    tool_choice={"type": "tool", "name": tool_name},
                )
            except anthropic.APIStatusError as e:
                if e.status_code not in RETRY_STATUSES:
                    raise ClaudeError(f"API error {e.status_code}: {e}") from e
                last_err = e
                self._sleep(2 ** attempt)
                continue
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_err = e
                self._sleep(2 ** attempt)
                continue
            usage = Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            )
            usage.cost_usd = self._cost(model, usage)
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    return dict(block.input), usage
            raise ClaudeError(f"model '{model}' returned no tool_use block")
        raise ClaudeError(f"retries exhausted after {max_retries} attempts: {last_err}")
