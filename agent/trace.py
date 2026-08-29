"""The decision trace — the Agent's append-only reasoning log.

Guardrail #5 (DESIGN §4.4): *every* agent action — the query it issued, a summary
of what came back, and the conclusions it drew — is logged to an append-only
trace. This is both observability and the interview artifact: you can walk someone
through exactly how the Agent reached its diagnosis.

Two kinds of entry, one timeline:

  * TOOL entries    — recorded automatically by the tool dispatcher on every call,
                      so the trace is complete even if the model never narrates.
  * DECISION entries — recorded when the model calls `log_decision` to state a
                      finding and its rationale (reasoning that isn't itself a query).

The trace is pure in-memory data (a list of dicts); persisting it — locally to
JSONL, live to `fin_close.agent.*` — is the audit layer's job, same split as
Phase 1's `write_dry_run` / `write_delta`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KIND_TOOL = "tool"
KIND_DECISION = "decision"


@dataclass
class DecisionTrace:
    """An ordered, append-only list of what the Agent did and concluded.

    Not frozen (it grows during an investigation), but only ever appended to —
    there is no method that edits or removes an entry.
    """

    entries: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def _next_seq(self) -> int:
        return len(self.entries) + 1

    def record_tool(self, tool: str, tool_input: dict, summary: str, *, is_error: bool = False) -> dict:
        """Log one tool call and a short summary of its result. Returns the entry."""
        entry = {
            "seq": self._next_seq(),
            "kind": KIND_TOOL,
            "tool": tool,
            "input": tool_input,
            "summary": summary,
            "is_error": is_error,
        }
        self.entries.append(entry)
        return entry

    def record_decision(self, *, finding: str, rationale: str, step: str = "") -> dict:
        """Log an explicit finding + rationale from the model. Returns the entry."""
        entry = {
            "seq": self._next_seq(),
            "kind": KIND_DECISION,
            "step": step,
            "finding": finding,
            "rationale": rationale,
        }
        self.entries.append(entry)
        return entry

    def tool_calls(self) -> list[dict]:
        """Just the TOOL entries — used to enforce the bounded-loop cap (DESIGN §4.4)."""
        return [e for e in self.entries if e["kind"] == KIND_TOOL]

    def to_rows(self) -> list[dict]:
        """A JSON-serializable copy of the timeline, for the audit/persistence layer."""
        return [dict(e) for e in self.entries]
