"""The live model adapter — the real `ModelClient`, backed by the Anthropic API.

This is the ONLY place the agent talks to a live LLM. Everywhere else (the loop,
the tools, the tests) is model-agnostic and runs offline, so this module is the
Phase-2 analog of the deferred-live tail in Phase 1 (`audit.write_delta`,
`verdict._default_output_getter`): unit-tested with a fake, exercised for real
only when someone deliberately supplies credentials.

--- KEY DISCIPLINE (read this) -------------------------------------------------
The API key is NEVER in this repo. It is read from the environment
(`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`) by the SDK, mirroring the
Databricks-secret discipline (CLAUDE.md, DESIGN §9). Two guarantees follow:

  1. Import-safe & keyless: importing this module does NOT import the SDK, build a
     client, or touch the network. Nothing here runs a live call as a side effect.
  2. No key => no spend: if no credentials are present, `build_anthropic_client`
     raises `LLMConfigError` *before* any request. A clone on GitHub (or CI with
     no secret) therefore cannot call the API and cannot incur charges. Running
     live is an explicit, deliberate act — you set a key and construct the client.

The `anthropic` package is a runtime-only dependency for THIS path; it is imported
lazily, so the offline suite needs neither the package nor a key.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.investigate import AssistantTurn, ToolUse

# Model + generation settings. Non-secret; safe to keep in code/config. The model
# is Anthropic's flagship by default — change it in config/system.yaml (agent.llm)
# if you want a cheaper/faster model for a run; nothing here spends until you do.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 8000
_SYSTEM_CONFIG = Path(__file__).resolve().parent.parent / "config" / "system.yaml"


class LLMConfigError(RuntimeError):
    """No usable Anthropic credentials/config — surfaced BEFORE any API call.

    Deliberately fail-closed: the agent must never fall back to some ambient path
    and quietly spend. If you see this, set ANTHROPIC_API_KEY (or configure the
    client explicitly) — that is the only way a live call happens.
    """


def has_credentials() -> bool:
    """True iff an Anthropic credential is present in the environment."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def build_anthropic_client():
    """Construct a real Anthropic client — the ONLY function that can enable spend.

    Raises `LLMConfigError` when no credential is present, so a keyless environment
    (a fresh clone, CI without the secret) cannot make a request. Imports the SDK
    lazily so merely importing this module never pulls it in.
    """
    if not has_credentials():
        raise LLMConfigError(
            "No Anthropic credentials found (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN). "
            "The agent will not call the API without an explicit key — this is deliberate. "
            "Set the key in your environment (or a Databricks secret) to run live."
        )
    import anthropic  # lazy: offline tests need neither the package nor a key

    return anthropic.Anthropic()


class AnthropicModel:
    """A `ModelClient` (see agent.investigate) backed by the Anthropic Messages API.

    Drop-in interchangeable with the tests' ScriptedModel: the loop cannot tell
    them apart. `client` is injectable so unit tests pass a fake and never need the
    SDK, a key, or the network. In production, leave `client=None` and it is built
    by `build_anthropic_client()` — which refuses to run without a key.
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS, client=None):
        self.model = model
        self.max_tokens = max_tokens
        self._client = client  # None until first use; built lazily & guarded

    @classmethod
    def from_config(cls, path: str | Path | None = None, *, client=None) -> "AnthropicModel":
        """Build from config/system.yaml's `agent.llm` block (model, max_tokens).

        The config carries only non-secret settings — never a key.
        """
        model, max_tokens = DEFAULT_MODEL, DEFAULT_MAX_TOKENS
        cfg_path = Path(path) if path is not None else _SYSTEM_CONFIG
        if cfg_path.exists():
            import yaml

            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            llm = ((raw.get("agent") or {}).get("llm")) or {}
            model = llm.get("model", model)
            max_tokens = int(llm.get("max_tokens", max_tokens))
        return cls(model=model, max_tokens=max_tokens, client=client)

    def _get_client(self):
        if self._client is None:
            self._client = build_anthropic_client()  # raises if no key
        return self._client

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        """One model turn: send the conversation + tools, normalize the reply.

        Returns an `AssistantTurn`. `raw_content` is the SDK's own content blocks,
        so thinking blocks etc. are echoed back verbatim on the next turn (the loop
        appends `raw_content` when present).
        """
        response = self._get_client().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
        )
        return _to_turn(response)


def _to_turn(response) -> AssistantTurn:
    """Normalize an Anthropic Message into the loop's AssistantTurn."""
    text_parts: list[str] = []
    tool_uses: list[ToolUse] = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            # input JSON escaping can vary; the SDK already gives us a parsed dict.
            tool_uses.append(ToolUse(id=block.id, name=block.name, input=dict(block.input or {})))
    return AssistantTurn(
        text="".join(text_parts),
        tool_uses=tool_uses,
        stop_reason=getattr(response, "stop_reason", "end_turn") or "end_turn",
        raw_content=list(response.content),
    )
