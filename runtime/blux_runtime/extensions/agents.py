"""Agent loop and subagents (Stage 14; parity matrix BLH-001 and BLH-014).

An agent turn calls `model.generate` with tools, executes the tool calls it receives as ordinary capability
executions, and repeats until the model answers without tool calls or the turn limit is hit. Each step goes
through the runtime (policy, approvals, hooks, receipts). The model is never trusted to run anything
directly.

Subagents (`delegate`):
- Each child runs with a subset of its parent's tools and its own depth.
- `max_depth` (default 2) bounds nesting.
- Children share the session and run as actor `agent`.

Delegation never widens permissions. The agent loop emits `agent.spawned`, `agent.completed` and
`agent.failed`.

Clean-room implementation; no upstream code. Only explicit model output (text and tool calls) is kept;
there is no hidden reasoning to record.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from blux_runtime.executor import Runtime
from blux_runtime.hooks.bus import EventContext, HookError
from blux_sdk import envelopes, model_api


class AgentError(RuntimeError):
    pass


def tool_name(capability_id: str) -> str:
    return capability_id.replace(".", "__")[:64]


@dataclass
class AgentResult:
    agent_id: str
    text: str | None
    turns: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    # Operations whose receipt the runtime's sink failed to record durably (the result itself still stands).
    unrecorded: list[str] = field(default_factory=list)


class AgentRunner:
    def __init__(self, runtime: Runtime, *, model: str = "blux-main", max_turns: int = 8, max_depth: int = 2) -> None:
        self.runtime, self.model, self.max_turns, self.max_depth = runtime, model, max_turns, max_depth

    def _tools(self, capability_ids: Iterable[str]) -> list[dict[str, Any]]:
        out = []
        for cid in capability_ids:
            d = self.runtime.registry.get(cid).descriptor
            out.append({"name": tool_name(cid), "description": d.summary[:2048], "input_schema": d.input_schema})
        return out

    def _emit(self, name: str, payload: dict[str, Any], session_id: str | None) -> None:
        if self.runtime.hooks is not None:
            with contextlib.suppress(HookError):
                self.runtime.hooks.emit(name, payload, context=EventContext(session_id=session_id, actor_type="agent"))

    def run(
        self,
        task: str,
        *,
        tools: Iterable[str] = (),
        session_id: str | None = None,
        depth: int = 0,
        parent_agent_id: str | None = None,
        system: str | None = None,
    ) -> AgentResult:
        if depth > self.max_depth:
            raise AgentError(f"delegation depth {depth} exceeds max_depth {self.max_depth}")
        allowed = list(dict.fromkeys(tools))
        by_name = {tool_name(c): c for c in allowed}
        agent_id = f"agt_{uuid.uuid4().hex[:20]}"
        self._emit(
            "agent.spawned",
            {"agent_id": agent_id, "parent_agent_id": parent_agent_id, "depth": depth, "model": self.model},
            session_id,
        )
        result = AgentResult(agent_id, None, 0)
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        actor = {"type": "agent", "id": agent_id}
        try:
            for turn in range(1, self.max_turns + 1):
                result.turns = turn
                req = model_api.request(
                    self.model, messages=messages, system=system, tools=self._tools(allowed) or None
                )
                gen = self.runtime.execute(
                    envelopes.execution_request("model.generate", req, actor=actor, session_id=session_id)
                )
                result.operations.append(gen.result["operation_id"])
                if gen.sink_error is not None:
                    result.unrecorded.append(gen.result["operation_id"])
                if gen.result["status"] != "succeeded":
                    raise AgentError(f"model call failed: {(gen.result['error'] or {}).get('code')}")
                output = gen.result["output"]["output"]
                calls = output["tool_calls"]
                messages.append(
                    {"role": "assistant", "content": output["text"] or "", **({"tool_calls": calls} if calls else {})}
                )
                if not calls:
                    result.text = output["text"]
                    self._emit("agent.completed", {"agent_id": agent_id, "turns": turn}, session_id)
                    return result
                for call in calls:
                    cid = by_name.get(call["name"])
                    if cid is None:
                        content = json.dumps({"error": "tool not available to this agent"})
                    else:
                        out = self.runtime.execute(
                            envelopes.execution_request(
                                cid,
                                call["arguments"],
                                actor=actor,
                                session_id=session_id,
                                parent_operation_id=gen.result["operation_id"],  # the model call that asked for it
                            )
                        )
                        result.operations.append(out.result["operation_id"])
                        if out.sink_error is not None:
                            result.unrecorded.append(out.result["operation_id"])
                        content = json.dumps(
                            out.result["output"]
                            if out.result["status"] == "succeeded"
                            else {"error": out.result["error"]["code"], "details": out.result["error"].get("details")}
                        )
                    result.tool_calls.append({"name": call["name"], "capability_id": cid})
                    messages.append({"role": "tool", "content": content[:100000], "tool_call_id": call["id"]})
            raise AgentError(f"no final answer within {self.max_turns} turns")
        except AgentError as exc:
            self._emit("agent.failed", {"agent_id": agent_id, "error_code": str(exc)[:64] or "agent_error"}, session_id)
            raise

    def delegate(
        self,
        parent_tools: Iterable[str],
        child_tools: Iterable[str],
        task: str,
        *,
        depth: int,
        parent_agent_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        """Run a subagent with child_tools, which must be a subset of the parent's tools."""
        parent, child = set(parent_tools), list(child_tools)
        extra = sorted(set(child) - parent)
        if extra:
            raise AgentError(f"a subagent cannot gain tools its parent lacks: {extra}")
        return self.run(task, tools=child, session_id=session_id, depth=depth + 1, parent_agent_id=parent_agent_id)
