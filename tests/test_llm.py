"""Unit tests for agent.llm — the live Anthropic adapter. NO key, NO network, NO SDK.

Everything here runs offline with a fake client injected in place of the real
Anthropic SDK. The tests pin the two safety properties that matter for putting
this on GitHub: (1) with no credentials the adapter REFUSES to run rather than
spending, and (2) importing the module never pulls in the SDK or makes a call.
"""

import sys
from types import SimpleNamespace

import pytest

from agent.investigate import investigate
from agent.llm import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    AnthropicModel,
    LLMConfigError,
    build_anthropic_client,
    has_credentials,
)

# Reuse the loop's context/provider and the canned diagnosis input.
from test_investigate import CTX, IC
from test_diagnosis import SUBMITTED
from agent.provider import LocalGLProvider
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "gl"


# --- fake Anthropic SDK ---------------------------------------------------- #
def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool(id, name, inp):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=inp)


def _resp(blocks, stop_reason="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


@pytest.fixture()
def provider():
    return LocalGLProvider(FIXTURE_ROOT, "intercompany_out_of_balance")


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """Ensure the test environment never carries a real key into these tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


# --- response normalization ------------------------------------------------ #
def test_maps_text_and_tool_use_blocks_to_a_turn():
    resp = _resp([_text("Let me check the nets."),
                  _tool("t1", "query_failing_table", {"group_by": "main_account"})])
    model = AnthropicModel(client=FakeClient([resp]))

    turn = model.create(system="sys", messages=[], tools=[])
    assert turn.text == "Let me check the nets."
    assert turn.tool_uses[0].name == "query_failing_table"
    assert turn.tool_uses[0].input == {"group_by": "main_account"}
    assert turn.stop_reason == "tool_use"
    assert turn.raw_content == resp.content        # echoed back verbatim (thinking-safe)


def test_passes_model_settings_and_tools_through():
    model = AnthropicModel(client=FakeClient([_resp([_text("ok")], stop_reason="end_turn")]))
    model.create(system="SYS", messages=[{"role": "user", "content": "hi"}], tools=[{"name": "x"}])

    sent = model._client.messages.calls[0]
    assert sent["model"] == DEFAULT_MODEL and sent["max_tokens"] == DEFAULT_MAX_TOKENS
    assert sent["system"] == "SYS"
    assert sent["tools"] == [{"name": "x"}]
    assert sent["thinking"] == {"type": "adaptive"}


# --- the whole point: a true drop-in for the mock -------------------------- #
def test_adapter_drives_the_real_loop_end_to_end(provider):
    responses = [
        _resp([_tool("a", "get_gate_verdict", {})]),
        _resp([_tool("b", "query_failing_table", {"filters": {"main_account": IC}, "group_by": "main_account"})]),
        _resp([_tool("c", "submit_diagnosis", SUBMITTED)]),
    ]
    model = AnthropicModel(client=FakeClient(responses))
    result = investigate(context=CTX, provider=provider, model=model)

    assert result.diagnosis is not None
    assert result.diagnosis.dollar_impact == 3000.0
    assert set(result.diagnosis.offending_vouchers) == {"USMI260600105", "USMI260700105"}


# --- KEY DISCIPLINE: no key => no run -------------------------------------- #
def test_no_credentials_reported():
    assert has_credentials() is False


def test_build_client_refuses_without_a_key():
    with pytest.raises(LLMConfigError, match="deliberate"):
        build_anthropic_client()


def test_model_with_no_client_and_no_key_cannot_call(provider):
    # A real run with no key must raise BEFORE any request — never silently spend.
    model = AnthropicModel()          # client=None -> would be built on first use
    with pytest.raises(LLMConfigError):
        model.create(system="s", messages=[], tools=[])


def test_credentials_detected_when_env_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-test-presence-only-not-a-real-key")
    assert has_credentials() is True


# --- import safety --------------------------------------------------------- #
def test_importing_llm_does_not_import_the_sdk():
    # Merely importing the module must not pull in `anthropic` or make a call.
    assert "anthropic" not in sys.modules


# --- config (non-secret) --------------------------------------------------- #
def test_from_config_reads_model_settings(tmp_path):
    cfg = tmp_path / "system.yaml"
    cfg.write_text("agent:\n  llm:\n    model: claude-sonnet-5\n    max_tokens: 4096\n", encoding="utf-8")
    model = AnthropicModel.from_config(cfg, client=FakeClient([]))
    assert model.model == "claude-sonnet-5" and model.max_tokens == 4096


def test_from_config_falls_back_to_defaults(tmp_path):
    model = AnthropicModel.from_config(tmp_path / "missing.yaml", client=FakeClient([]))
    assert model.model == DEFAULT_MODEL and model.max_tokens == DEFAULT_MAX_TOKENS
