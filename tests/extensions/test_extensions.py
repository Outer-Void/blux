"""Stage 14 HARD GATE: migrated extensions (skills, plugins, MCP, agents/subagents, scheduler, gateway).

Each subsystem is tested for explicit permissions, secret handling, failure isolation and receipts, and the
whole set runs without the fork. Fixtures are synthetic. The compatibility test against the fork's own skills
skips when the fork is absent.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry
from blux_runtime.executor import InMemoryReceiptSink, Runtime
from blux_runtime.extensions import agents, mcp, plugins, scheduler, skills
from blux_runtime.extensions.gateway import Gateway, GatewayError
from blux_runtime.hooks import HookBus, HookPolicy, HookSource
from blux_runtime.models import CallableBackend, ModelRouter
from blux_runtime.models import capability as model_capability
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.tools import builtin as tools
from blux_sdk import envelopes
from tests.conftest import ROOT
from tests.models.test_model_api import Script, reply, route, table

FAKE_KEY = "sk-proj-EXTENSIONSFAKE00000000000000"
USER = {"type": "user", "id": "u"}
GOLDEN = ROOT / "migration" / "blhermes" / "characterization" / "golden"


def policy(*rules: tuple[str, list[str], list[str], str]) -> dict[str, Any]:
    return {
        "contract": "blux.policy",
        "contract_version": "1.0",
        "policy_id": "ext-test",
        "default_effect": "deny",
        "rules": [
            {
                "rule_id": f"r{i}",
                "effect": "allow",
                "capabilities": caps,
                "actor_types": actors,
                "max_side_effects": side,
                "grants": grants,
            }
            for i, (side, caps, actors, *rest) in enumerate(rules)
            for grants in [rest[0] if rest else []]
        ],
    }


def execute(rt: Runtime, cap: str, payload: dict[str, Any], actor: dict[str, str] = USER) -> Any:
    return rt.execute(
        envelopes.execution_request(cap, payload, actor=actor, session_id="ses_01J00000000000000000000000")
    )


# skills --------------------------------------------------------------------------------------------------


def write_skill(root: Path, name: str, front: str, body: str = "Do the thing.") -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{front}\n---\n{body}\n")


def test_skills_need_a_licence_declare_permissions_and_are_scrubbed(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "notes",
        "name: notes\ndescription: Notes\nversion: 1.0.0\nlicense: MIT\n"
        "metadata:\n  hermes:\n    tags: [x]\n  blux:\n    capabilities: [shell.exec]",
        f"Use the key {FAKE_KEY} carefully.",
    )
    write_skill(tmp_path, "nolicence", "name: nolicence\ndescription: x")
    write_skill(tmp_path, "bad", "name: Bad Name\nlicense: MIT")
    lib = skills.SkillLibrary([tmp_path])
    assert sorted(lib.skills) == ["notes"] and len(lib.rejected) == 2
    s = lib.skills["notes"]
    assert s.capabilities == ("shell.exec",) and s.legacy_metadata
    reg = CapabilityRegistry()
    lib.register(reg)
    rt = Runtime(reg, policy(("read", ["skill.*"], ["user", "agent"])))
    got = execute(rt, "skill.get", {"name": "notes"}).result
    assert got["status"] == "succeeded" and FAKE_KEY not in got["output"]["instructions"]
    assert execute(rt, "skill.list", {}).result["output"]["rejected"] == 2


def test_fork_skills_parse_in_the_blux_format() -> None:
    fork = ROOT.parent / "blhermes-agent"
    if not (fork / "skills").is_dir():
        pytest.skip("fork not present (format compatibility check only)")
    lib = skills.SkillLibrary([fork / "skills"])
    assert len(lib.skills) > 50
    missing_licence = [k for k, v in lib.rejected.items() if "license" in v]
    assert len(lib.skills) >= 4 * len(missing_licence)  # the format is compatible; licence review rejects the rest


# plugins -------------------------------------------------------------------------------------------------


def make_plugin(tmp_path: Path, *, kind: str = "subprocess", licence: str | None = "MIT", hooks: bool = False) -> Path:
    d = tmp_path / "acme"
    d.mkdir(parents=True)
    (d / "cap.py").write_text(
        textwrap.dedent("""
        import json, os, sys
        req = json.load(sys.stdin)
        if req["input"].get("crash"):
            sys.exit(3)
        print(json.dumps({"echo": req["input"].get("text", ""), "leak": os.environ.get("OPENAI_API_KEY")}))
    """)
    )
    (d / "hook.py").write_text(
        "import json,sys\njson.load(sys.stdin)\nprint(json.dumps({'action': 'veto', 'reason': 'acme says no'}))\n"
    )
    cap = {
        "contract": "blux.capability",
        "contract_version": "1.0",
        "capability_id": "acme.echo",
        "version": "1.0.0",
        "summary": "Echo.",
        "contract_versions": ["1.0"],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "side_effects": "none",
        "required_permissions": [],
        "idempotent": True,
        "timeout_ms": 5000,
        "provider": {"repository": "Acme/plugin", "kind": kind, "entrypoint": f"{sys.executable} cap.py"},
    }
    manifest: dict[str, Any] = {
        "contract": "blux.plugin",
        "plugin_id": "acme",
        "version": "1.0.0",
        "capabilities": [cap],
    }
    if licence:
        manifest["license"] = licence
    if hooks:
        manifest["hooks"] = {
            "events": ["tool.before_execute"],
            "permissions": ["veto:tool.before_execute"],
            "command": f"{sys.executable} {d / 'hook.py'}",
            "timeout_ms": 5000,
        }
    (d / "blux-plugin.json").write_text(json.dumps(manifest))
    return d


def test_plugin_capabilities_run_out_of_process_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    reg = CapabilityRegistry()
    loaded = plugins.load(make_plugin(tmp_path), reg)
    assert loaded.capabilities == ["acme.echo"]
    rt = Runtime(reg, policy(("none", ["acme.*"], ["user"])))
    out = execute(rt, "acme.echo", {"text": "hi"}).result
    assert out["output"] == {"echo": "hi", "leak": None}
    crashed = execute(rt, "acme.echo", {"crash": True})
    assert (
        crashed.result["status"] == "failed"
        and execute(rt, "acme.echo", {"text": "again"}).result["status"] == "succeeded"
    )


def test_plugin_admission_rules(tmp_path: Path) -> None:
    with pytest.raises(plugins.PluginError, match="license"):
        plugins.load(make_plugin(tmp_path, licence=None), CapabilityRegistry())
    with pytest.raises(plugins.PluginError, match="subprocess"):
        plugins.load(make_plugin(tmp_path / "b", kind="builtin"), CapabilityRegistry())


def test_plugin_hooks_need_an_operator_grant(tmp_path: Path) -> None:
    with pytest.raises(plugins.PluginError, match="refused"):
        plugins.load(make_plugin(tmp_path / "x", hooks=True), CapabilityRegistry(), hooks=HookBus())
    bus = HookBus(
        policy=HookPolicy(
            {"builtin:*": ("observe:*",), "plugin:acme": ("observe:tool.before_execute", "veto:tool.before_execute")}
        )
    )
    reg = builtin_registry()
    plugins.load(make_plugin(tmp_path / "y", hooks=True), reg, hooks=bus)
    tools.register(reg, workspace=tmp_path)
    rt = Runtime(reg, tools.tool_policy(), hooks=bus)
    res = execute(rt, "fs.write", {"path": "a.txt", "content": "x"}).result
    assert res["status"] == "rejected" and res["error"]["details"]["hook_id"] == "plugin.acme"


# MCP ----------------------------------------------------------------------------------------------------

MCP_SERVER = textwrap.dedent("""
    import json, os, sys, time
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        m, rid = msg["method"], msg["id"]
        if m == "initialize":
            res = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                   "serverInfo": {"name": "t"}}
        elif m == "tools/list":
            obj = {"type": "object"}
            text_schema = {"type": "object", "properties": {"text": {"type": "string"}}}
            res = {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": text_schema},
                             {"name": "env", "inputSchema": obj}, {"name": "hang", "inputSchema": obj},
                             {"name": "fail", "inputSchema": obj}]}
        elif m == "tools/call":
            name, args = msg["params"]["name"], msg["params"]["arguments"]
            if name == "hang":
                time.sleep(30)
            env = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "OPENAI_API_KEY")}
            text = args.get("text", "") if name == "echo" else json.dumps(env)
            res = {"content": [{"type": "text", "text": text}], "isError": name == "fail"}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}), flush=True)
""")


@pytest.fixture
def mcp_runtime(tmp_path: Path) -> Any:
    script = tmp_path / "server.py"
    script.write_text(MCP_SERVER)
    env = {**os.environ, "GITHUB_TOKEN": "ghp_" + "B" * 36, "OPENAI_API_KEY": FAKE_KEY}
    server = mcp.McpServer(
        mcp.ServerConfig(
            "gh",
            f"{sys.executable} {script}",
            side_effects="external",
            credential_env=["GITHUB_TOKEN"],
            timeout_ms=3000,
        ),
        environ=env,
    )
    reg = CapabilityRegistry()
    ids = mcp.register(reg, server)
    rt = Runtime(reg, policy(("external", ["mcp.gh.*"], ["user"], ["mcp:gh"])))
    yield rt, ids, server
    server.close()


def test_mcp_tools_become_permissioned_capabilities(mcp_runtime: Any) -> None:
    rt, ids, _ = mcp_runtime
    assert ids == ["mcp.gh.echo", "mcp.gh.env", "mcp.gh.hang", "mcp.gh.fail"]
    assert execute(rt, "mcp.gh.echo", {"text": f"hello {FAKE_KEY}"}).result["output"]["text"] == "hello [redacted]"
    denied = Runtime(rt.registry, policy(("external", ["mcp.gh.*"], ["user"], [])))
    assert execute(denied, "mcp.gh.echo", {"text": "x"}).result["error"]["code"] == "POLICY_DENIED"


def test_mcp_servers_get_only_declared_credentials_and_recover_from_hangs(mcp_runtime: Any) -> None:
    rt, _, _ = mcp_runtime
    env = json.loads(execute(rt, "mcp.gh.env", {}).result["output"]["text"])
    assert env == {"GITHUB_TOKEN": "[redacted]", "OPENAI_API_KEY": None}  # declared one passed (and redacted), rest not
    hung = rt.execute(envelopes.execution_request("mcp.gh.hang", {}, actor=USER, timeout_ms=1000)).result
    assert hung["status"] == "failed"
    assert execute(rt, "mcp.gh.echo", {"text": "back"}).result["output"]["text"] == "back"  # restarted
    assert execute(rt, "mcp.gh.fail", {}).result["error"]["details"]["reason"] == "tool_error"


# agents and subagents -------------------------------------------------------------------------------------


def agent_runtime(tmp_path: Path, script: Script, bus: HookBus | None = None) -> Runtime:
    reg = builtin_registry()
    model_capability.register(reg, ModelRouter(table(route("main.a")), {"test": CallableBackend(script)}, hooks=bus))
    tools.register(reg, workspace=tmp_path)
    pol = tools.tool_policy()
    pol["rules"] += model_capability.model_policy()["rules"]
    return Runtime(reg, pol, hooks=bus)


def scripted_agent(steps: list[dict[str, Any]]) -> Script:
    it = iter(steps)

    def answer(request: dict[str, Any], rt: dict[str, Any], emit: Any) -> dict[str, Any]:
        step = next(it)
        return reply(request, rt, text=step.get("text"), tool_calls=step.get("calls"))

    return Script(main_a=answer)


def test_agent_loop_runs_tools_through_the_runtime_and_emits_events(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("the answer is 42")
    bus = HookBus()
    names: list[str] = []
    bus.register("agent.*", lambda e: names.append(e.name), source=HookSource("builtin", "t"))
    script = scripted_agent(
        [
            {"text": None, "calls": [{"id": "c1", "name": "fs__read", "arguments": {"path": "notes.txt"}}]},
            {"text": "It is 42."},
        ]
    )
    rt = agent_runtime(tmp_path, script, bus)
    result = agents.AgentRunner(rt).run(
        "what is the answer?", tools=["fs.read"], session_id="ses_01J00000000000000000000000"
    )
    assert (
        result.text == "It is 42."
        and result.turns == 2
        and result.tool_calls == [{"name": "fs__read", "capability_id": "fs.read"}]
    )
    second = script.seen[1][1]["messages"]
    assert second[-1]["role"] == "tool" and "42" in second[-1]["content"]
    assert names == ["agent.spawned", "agent.completed"] and len(result.operations) == 3


def test_agents_cannot_skip_approvals(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    script = scripted_agent(
        [
            {"calls": [{"id": "c1", "name": "shell__exec", "arguments": {"command": "rm -rf build"}}]},
            {"text": "done"},
        ]
    )
    agents.AgentRunner(agent_runtime(tmp_path, script)).run("clean up", tools=["shell.exec"])
    tool_msg = next(m for m in script.seen[1][1]["messages"] if m["role"] == "tool")
    assert "approval_required" in tool_msg["content"] and (tmp_path / "build").exists()


def test_agents_cannot_call_tools_they_were_not_given(tmp_path: Path) -> None:
    script = scripted_agent(
        [{"calls": [{"id": "c1", "name": "fs__write", "arguments": {"path": "x", "content": "y"}}]}]
    )
    with pytest.raises(agents.AgentError, match="model call failed"):  # the router rejects undeclared tool calls
        agents.AgentRunner(agent_runtime(tmp_path, script)).run("write", tools=["shell.exec"])
    assert not (tmp_path / "x").exists()


def test_subagents_are_bounded_and_never_gain_tools(tmp_path: Path) -> None:
    runner = agents.AgentRunner(agent_runtime(tmp_path, scripted_agent([{"text": "child done"}])), max_depth=1)
    assert runner.delegate(["fs.read", "fs.write"], ["fs.read"], "read it", depth=0).text == "child done"
    with pytest.raises(agents.AgentError, match="cannot gain"):
        runner.delegate(["fs.read"], ["fs.read", "shell.exec"], "x", depth=0)
    with pytest.raises(agents.AgentError, match="depth"):
        runner.delegate(["fs.read"], ["fs.read"], "x", depth=1)


# scheduler -----------------------------------------------------------------------------------------------


def test_schedule_parsing_matches_the_cron_golden() -> None:
    for row in json.loads((GOLDEN / "cron_schedules.json").read_text()):
        assert scheduler.parse_schedule(row["input"]) == row["parsed"], row["input"]
    with pytest.raises(scheduler.ScheduleError):
        scheduler.parse_schedule("61 * * * *")
    now = dt.datetime(2026, 10, 26, 8, 59, tzinfo=dt.timezone.utc)
    assert scheduler.next_run(scheduler.parse_schedule("0 9 * * *"), now) == now.replace(minute=0, hour=9)
    assert scheduler.next_run(scheduler.parse_schedule("*/15 * * * *"), now) == now.replace(hour=9, minute=0)


def test_scheduled_jobs_run_unattended_without_approvals_and_are_isolated(tmp_path: Path) -> None:
    sink = InMemoryReceiptSink()
    reg = CapabilityRegistry()
    tools.register(reg, workspace=tmp_path)
    pol = tools.tool_policy()
    pol["rules"].append(
        {
            "rule_id": "sched",
            "effect": "allow",
            "capabilities": ["fs.write", "shell.exec"],
            "actor_types": ["service"],
            "max_side_effects": "destructive",
            "grants": ["tool:fs.write", "tool:shell.exec"],
        }
    )
    rt = Runtime(reg, pol, receipt_sink=sink)
    store = scheduler.JobStore(tmp_path / "jobs.json")
    now = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)
    store.add("every 30m", "fs.write", {"path": "tick.txt", "content": "x", "append": True}, now=now)
    (tmp_path / "build").mkdir()
    store.add("every 30m", "shell.exec", {"command": "rm -rf build"}, now=now)
    store.add("2026-10-26T09:10:00", "nope.missing", {}, now=now)
    ran = scheduler.run_due(store, rt.execute, now=now + dt.timedelta(minutes=31))
    assert [r["status"] for r in ran] == ["succeeded", "failed", "rejected"]
    assert (tmp_path / "tick.txt").read_text() == "x" and (tmp_path / "build").exists()
    assert all(r["receipt_id"] for r in ran) and len(sink.receipts) == 3
    jobs = store.load()
    assert jobs[2].enabled is False and oct((tmp_path / "jobs.json").stat().st_mode & 0o777) == "0o600"
    assert sink.receipts[0]["actor"] == {"type": "service", "id": "scheduler"}


# gateway ------------------------------------------------------------------------------------------------

TOKEN = "t" * 40


def http(url: str, *, token: str | None = None, body: bytes | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_gateway_authenticates_and_never_lets_callers_choose_an_identity(tmp_path: Path) -> None:
    sink = InMemoryReceiptSink()
    pol = policy(("none", ["echo.test"], ["service"]))
    gw = Gateway(Runtime(builtin_registry(), pol, receipt_sink=sink), TOKEN).start()
    host, port = gw.address
    base = f"http://{host}:{port}"
    try:
        assert http(f"{base}/v1/health") == (200, {"status": "ok"})
        assert http(f"{base}/v1/capabilities")[0] == 401
        req = envelopes.execution_request("echo.test", {"message": "hi"}, actor={"type": "user", "id": "root"})
        assert http(f"{base}/v1/execute", token="wrong" * 10, body=json.dumps(req).encode())[0] == 401
        status, result = http(f"{base}/v1/execute", token=TOKEN, body=json.dumps(req).encode())
        assert status == 200 and result["status"] == "succeeded"
        assert sink.receipts[-1]["actor"] == {"type": "service", "id": "gateway"}
        before = len(sink.receipts)
        try:
            refused = http(f"{base}/v1/execute", token=TOKEN, body=b"x" * (1024 * 1024 + 1))[0] == 413
        except (urllib.error.URLError, ConnectionError):
            refused = True  # the server answered 413 and closed before the upload finished
        assert refused and len(sink.receipts) == before
        assert http(f"{base}/v1/capabilities", token=TOKEN)[0] == 200
    finally:
        gw.stop()
    with pytest.raises(GatewayError, match="loopback"):
        Gateway(Runtime(builtin_registry(), pol), TOKEN, host="0.0.0.0")
    with pytest.raises(GatewayError, match="32 characters"):
        Gateway(Runtime(builtin_registry(), pol), "short")


# hard gate: no fork at runtime -------------------------------------------------------------------------------


def test_extensions_run_without_the_fork() -> None:
    code = (
        "import sys, json\n"
        "import blux_runtime.extensions.skills, blux_runtime.extensions.plugins, blux_runtime.extensions.mcp\n"
        "import blux_runtime.extensions.agents, blux_runtime.extensions.scheduler, blux_runtime.extensions.gateway\n"
        "fork = {'agent', 'hermes_cli', 'hermes_state', 'hermes_constants', 'toolsets', 'model_tools', 'gateway',\n"
        "        'cron', 'run_agent', 'tui_gateway', 'acp_adapter', 'hermes_platform', 'utils', 'plugins',\n"
        "        'mcp_serve'}\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in fork)))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/", check=True)
    assert json.loads(proc.stdout) == []
