from types import SimpleNamespace

import anthropic
import httpx
import pytest

from srx.claude_client import ClaudeClient, ClaudeError, Usage, pdf_document_block

TOOL_SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}


def fake_response(tool_input, in_tok=1000, out_tok=200):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record", input=tool_input)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class FakeSDK:
    def __init__(self, results):
        self.results = list(results)  # values or exceptions, popped in order
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def rate_limit_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=req), body=None
    )


def test_tool_call_returns_input_and_costed_usage():
    sdk = FakeSDK([fake_response({"x": 5})])
    c = ClaudeClient(client=sdk, pricing={"m1": (10.0, 50.0)})
    data, usage = c.tool_call(
        model="m1", system="s", messages=[{"role": "user", "content": "hi"}],
        tool_name="record", tool_schema=TOOL_SCHEMA,
    )
    assert data == {"x": 5}
    assert usage.input_tokens == 1000
    assert usage.cost_usd == pytest.approx(1000 / 1e6 * 10.0 + 200 / 1e6 * 50.0)
    call = sdk.calls[0]
    assert call["temperature"] == 0
    assert call["tool_choice"] == {"type": "tool", "name": "record"}
    assert call["tools"][0]["input_schema"] == TOOL_SCHEMA


def test_retries_then_succeeds():
    sdk = FakeSDK([rate_limit_error(), rate_limit_error(), fake_response({"x": 1})])
    slept = []
    c = ClaudeClient(client=sdk, pricing={}, sleep=slept.append)
    data, usage = c.tool_call(
        model="m?", system="s", messages=[{"role": "user", "content": "hi"}],
        tool_name="record", tool_schema=TOOL_SCHEMA,
    )
    assert data == {"x": 1}
    assert slept == [1, 2]          # 2**0, 2**1
    assert usage.cost_usd == 0.0    # unknown model prices at 0 (warned)


def test_retries_exhausted_raises():
    sdk = FakeSDK([rate_limit_error()] * 4)
    c = ClaudeClient(client=sdk, pricing={}, sleep=lambda s: None)
    with pytest.raises(ClaudeError, match="retries"):
        c.tool_call(
            model="m", system="s", messages=[{"role": "user", "content": "hi"}],
            tool_name="record", tool_schema=TOOL_SCHEMA, max_retries=4,
        )


def test_no_tool_use_block_raises():
    bad = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="chatter")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    c = ClaudeClient(client=FakeSDK([bad]), pricing={})
    with pytest.raises(ClaudeError, match="tool_use"):
        c.tool_call(
            model="m", system="s", messages=[{"role": "user", "content": "hi"}],
            tool_name="record", tool_schema=TOOL_SCHEMA,
        )


def test_usage_add_and_pdf_block():
    u = Usage(1, 2, 0.5)
    u.add(Usage(10, 20, 1.5))
    assert (u.input_tokens, u.output_tokens, u.cost_usd) == (11, 22, 2.0)
    block = pdf_document_block(b"%PDF-fake")
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["cache_control"] == {"type": "ephemeral"}
