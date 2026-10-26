"""`blux agent`: run a task with the BLUX agent loop (Stage 18; what `ax agent` routes to).

    blux agent "summarize notes.txt" --tools fs.read [--workspace DIR] [-m MODEL] [--max-turns N] [--json]

The agent uses the logical model through the Model API (same routes and config as `blux ask`). Its tools are
the built-in tool capabilities confined to --workspace (default: the current directory). No approvals are
granted, so dangerous commands are refused. Policy, hooks and receipts apply to every step.
Exit codes: 0 answered, 1 the agent failed, 2 usage, 3 configuration.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, TextIO

from blux_cli import config as cfg
from blux_cli import direct
from blux_runtime.builtin import builtin_registry
from blux_runtime.executor import Runtime
from blux_runtime.extensions.agents import AgentError, AgentRunner
from blux_runtime.models import capability as model_capability
from blux_runtime.tools import builtin as tools
from blux_sdk import ids, model_api
from blux_sdk.serialization import dumps_json

TOOLS = ("fs.read", "fs.write", "shell.exec")


def add_parser(sub: Any) -> None:
    a = sub.add_parser("agent", help="run a task with the BLUX agent loop (tools confined to a workspace)")
    a.add_argument("task", nargs="+")
    a.add_argument("--tools", default="fs.read", help=f"comma-separated subset of {', '.join(TOOLS)} (default fs.read)")
    a.add_argument("--workspace", default=".", help="directory the tools are confined to (default: .)")
    a.add_argument("-m", "--model", choices=list(model_api.LOGICAL_MODELS))
    a.add_argument("--max-turns", type=int, default=8)
    a.add_argument("--routes", help="blux.model-routes table")
    a.add_argument("--json", action="store_true")


def run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    chosen = [t.strip() for t in args.tools.split(",") if t.strip()]
    unknown = sorted(set(chosen) - set(TOOLS))
    if unknown or not 1 <= args.max_turns <= 50:
        print(f"blux: unknown tools {unknown}" if unknown else "blux: --max-turns must be 1..50", file=err)
        return direct.USAGE
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"blux: workspace {workspace} is not a directory", file=err)
        return direct.USAGE
    try:
        settings = cfg.load()
        cfg.override(settings, "model", args.model, "--model")
        cfg.override(settings, "routes", args.routes, "--routes")
        registry = builtin_registry()
        model_capability.register(registry, direct.make_router(direct.load_routes(settings)))
    except cfg.ConfigError as exc:
        print(f"blux: {exc}", file=err)
        return direct.CONFIG
    except direct.Abort as exc:
        print(f"blux: {exc.message}", file=err)
        return exc.code
    tools.register(registry, workspace=workspace)
    policy = tools.tool_policy()
    policy["rules"] = [*policy["rules"], *model_capability.model_policy()["rules"]]
    runner = AgentRunner(Runtime(registry, policy), model=settings.model, max_turns=args.max_turns)
    try:
        result = runner.run(" ".join(args.task), tools=chosen, session_id=ids.new_session_id())
    except AgentError as exc:
        print(f"blux: agent failed: {exc}", file=err)
        return direct.FAILED
    if args.json:
        out.write(
            dumps_json(
                {
                    "agent_id": result.agent_id,
                    "text": result.text,
                    "turns": result.turns,
                    "tool_calls": result.tool_calls,
                    "operations": result.operations,
                }
            )
            + "\n"
        )
    else:
        out.write((result.text or "").rstrip("\n") + "\n")
        print(f"-- {result.turns} turn(s), {len(result.tool_calls)} tool call(s), agent {result.agent_id}", file=err)
    return direct.OK
