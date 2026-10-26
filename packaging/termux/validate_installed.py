"""Validate an installed (or extracted) BLUX package by exercising every runtime area (Stage 15).

    <python> -I packaging/termux/validate_installed.py --prefix <root>/data/data/com.termux/files/usr [--extra-site DIR]

Run it with the interpreter that will run BLUX: Termux's Bionic python3 on a device, or a glibc Python for
sandbox tests. Everything happens under a throwaway HOME with fake credentials and local mock services.
The real HOME, the network and installed packages are never touched. It prints one JSON report with
PASS / FAIL / SKIP per area and exits 0 only when nothing failed.

`--extra-site` is for test roots only: it adds a directory holding native dependencies (python-rpds-py)
that a real Termux installation provides through APT.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESULTS: dict[str, dict[str, Any]] = {}
FAKE_KEY = "sk-proj-VALIDATEINSTALLED000000000000"


def check(area: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
    def wrap(fn: Callable[[], str]) -> Callable[[], str]:
        try:
            detail = fn()
            RESULTS[area] = {"status": "SKIP" if detail.startswith("SKIP") else "PASS", "detail": detail}
        except Exception as exc:  # noqa: BLE001 - every failure becomes a report line
            RESULTS[area] = {
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}"[:400],
                "trace": traceback.format_exc()[-1200:],
            }
        return fn

    return wrap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", type=Path, required=True)
    ap.add_argument("--extra-site", type=Path)
    args = ap.parse_args()
    prefix = args.prefix.resolve()
    site = prefix / "lib" / "blux" / "site"
    sys.path[:0] = [str(site), *([str(args.extra_site)] if args.extra_site else [])]
    work = Path(tempfile.mkdtemp(prefix="blux-validate-", dir=os.environ.get("TMPDIR")))
    home = work / "home"
    home.mkdir()
    os.environ.update(
        {
            "HOME": str(home),
            "BLUX_CONFIG": str(home / "blux.toml"),
            "BLUXGPT_CONFIG": str(home / "gpt.env"),
            "OPENAI_API_KEY": FAKE_KEY,
            "TMPDIR": str(work),
        }
    )
    for k in ("BLUX_HOME", "HERMES_HOME", "XDG_STATE_HOME", "BLUX_MODEL_ROUTES"):
        os.environ.pop(k, None)
    py = sys.executable
    launcher = prefix / "bin" / "bluxgpt"

    import io

    def run_blux(*argv: str, stdin: str = "") -> tuple[int, str, str]:
        from blux_cli.main import main as blux  # imported per call: a broken install fails a check, not the report

        out, err = io.StringIO(), io.StringIO()
        try:
            code = blux(list(argv), stdin=io.StringIO(stdin), stdout=out, stderr=err)
        except SystemExit as exc:
            code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    routes = work / "routes.json"
    try:
        from blux_sdk import model_api
    except Exception as exc:  # noqa: BLE001 - report instead of crashing
        print(
            json.dumps(
                {
                    "contract": "blux.install-validation",
                    "pass": False,
                    "platform": sys.platform,
                    "machine": platform.machine(),
                    "results": {"payload": {"status": "FAIL", "detail": repr(exc)}},
                }
            )
        )
        return 1

    def mock(rid: str, behaviour: str) -> dict[str, Any]:
        return {
            "route_id": rid,
            "backend": "bluxgpt",
            "provider": "mock",
            "provider_model": behaviour,
            "locality": "local",
            "tier": "primary",
            "capabilities": ["streaming", "tools", "structured_output", "deterministic"],
            "determinism": {"temperature": True, "seed": True},
            "credential_env": ["OPENAI_API_KEY"],
        }

    routes.write_text(
        json.dumps(
            {
                "contract": "blux.model-routes",
                "model_api_version": "1.0",
                "routes_id": "validate",
                "backends": {"bluxgpt": {"kind": "subprocess", "entrypoint": f"{py} -I {launcher} blux-model-backend"}},
                "models": {m: {"routes": [mock(f"{m.split('-')[1]}.echo", "echo")]} for m in model_api.LOGICAL_MODELS},
                "defaults": {"max_attempts": 3, "allow_fallback": True, "timeout_ms": 30000},
            }
        )
    )

    @check("payload")
    def _payload() -> str:
        import bluxgpt

        import blux_cli
        import blux_runtime
        import blux_sdk

        for mod in (blux_runtime, blux_cli, blux_sdk, bluxgpt):
            where = Path(mod.__file__ or "").resolve()
            assert site in where.parents, f"{mod.__name__} imported from {where}, not from the package"
        return "BLUX, the SDK and BluxGPT import from the installed package"

    @check("cli")
    def _cli() -> str:
        code, out, _ = run_blux("version")
        assert code == 0, out
        return f"version {json.loads(out)['blux_runtime']}"

    @check("state_paths")
    def _state() -> str:
        from blux_runtime import state

        assert state.blux_home() == home / ".local" / "state" / "blux"
        return str(state.blux_home()).replace(str(home), "$HOME")

    @check("providers")
    def _providers() -> str:
        code, out, err = run_blux("--routes", str(routes), "hello", "device")
        assert (code, out) == (0, "echo: hello device\n"), err
        code, out, err = run_blux("doctor", "--routes", str(routes), "--json")
        assert code in (0, 3), err  # 3: bluxgpt is invoked by full path in routes, not on PATH
        return "Model API -> bundled BluxGPT (mock provider) answered"

    @check("tui")
    def _tui() -> str:
        code, out, err = run_blux("chat", "--routes", str(routes), stdin="one\ntwo\n/exit\n")
        assert code == 0 and out == "echo: one\necho: two\n", err
        return "blux chat (piped) two turns"

    def tool_runtime() -> Any:
        from blux_runtime.executor import Runtime
        from blux_runtime.registry import CapabilityRegistry
        from blux_runtime.tools import builtin

        reg = CapabilityRegistry()
        ws = work / "ws"
        ws.mkdir(exist_ok=True)
        builtin.register(reg, workspace=ws)
        return Runtime(reg, builtin.tool_policy()), ws

    @check("tools")
    def _tools() -> str:
        from blux_sdk import envelopes

        rt, ws = tool_runtime()
        actor = {"type": "user", "id": "v"}
        ok = rt.execute(envelopes.execution_request("shell.exec", {"command": "echo hi; env"}, actor=actor)).result
        assert (
            ok["status"] == "succeeded"
            and ok["output"]["stdout"].startswith("hi")
            and FAKE_KEY not in ok["output"]["stdout"]
        )
        (ws / "build").mkdir()
        denied = rt.execute(envelopes.execution_request("shell.exec", {"command": "rm -rf build"}, actor=actor)).result
        assert denied["status"] == "failed" and (ws / "build").exists()
        return "shell.exec safe run without credentials; dangerous command held for approval"

    @check("skills")
    def _skills() -> str:
        from blux_runtime.extensions.skills import SkillLibrary

        d = work / "skills" / "demo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\nlicense: MIT\n---\nHello.\n")
        lib = SkillLibrary([work / "skills"])
        assert list(lib.skills) == ["demo"]
        return "SKILL.md loaded with licence check"

    @check("plugins")
    def _plugins() -> str:
        from blux_runtime.executor import Runtime
        from blux_runtime.extensions import plugins
        from blux_runtime.registry import CapabilityRegistry
        from blux_sdk import envelopes

        d = work / "plugin"
        d.mkdir()
        (d / "cap.py").write_text(
            "import json,os,sys\njson.load(sys.stdin)\nprint(json.dumps({'leak': os.environ.get('OPENAI_API_KEY')}))\n"
        )
        cap = {
            "contract": "blux.capability",
            "contract_version": "1.0",
            "capability_id": "demo.cap",
            "version": "1.0.0",
            "summary": "x",
            "contract_versions": ["1.0"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "side_effects": "none",
            "required_permissions": [],
            "idempotent": True,
            "timeout_ms": 10000,
            "provider": {"repository": "x/y", "kind": "subprocess", "entrypoint": f"{py} cap.py"},
        }
        (d / "blux-plugin.json").write_text(
            json.dumps(
                {
                    "contract": "blux.plugin",
                    "plugin_id": "demo",
                    "version": "1",
                    "license": "MIT",
                    "capabilities": [cap],
                }
            )
        )
        reg = CapabilityRegistry()
        plugins.load(d, reg)
        pol = {
            "contract": "blux.policy",
            "contract_version": "1.0",
            "policy_id": "v",
            "default_effect": "deny",
            "rules": [
                {
                    "rule_id": "a",
                    "effect": "allow",
                    "capabilities": ["demo.*"],
                    "actor_types": ["user"],
                    "max_side_effects": "none",
                    "grants": [],
                }
            ],
        }
        res = (
            Runtime(reg, pol)
            .execute(envelopes.execution_request("demo.cap", {}, actor={"type": "user", "id": "v"}))
            .result
        )
        assert res["output"] == {"leak": None}, res
        return "out-of-process plugin capability ran without credentials"

    @check("mcp")
    def _mcp() -> str:
        from blux_runtime.extensions import mcp
        from blux_runtime.registry import CapabilityRegistry

        server = work / "mcp.py"
        server.write_text(
            textwrap.dedent("""
            import json, sys
            for line in sys.stdin:
                m = json.loads(line)
                if "id" not in m: continue
                if m["method"] == "initialize":
                    r = {"protocolVersion": m["params"]["protocolVersion"], "capabilities": {}}
                elif m["method"] == "tools/list": r = {"tools": [{"name": "ping", "inputSchema": {"type": "object"}}]}
                else: r = {"content": [{"type": "text", "text": "pong"}]}
                print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
        """)
        )
        srv = mcp.McpServer(mcp.ServerConfig("demo", f"{py} {server}"))
        try:
            assert mcp.register(CapabilityRegistry(), srv) == ["mcp.demo.ping"]
            assert srv.call("ping", {}, 10)["content"][0]["text"] == "pong"
        finally:
            srv.close()
        return "stdio MCP server tool listed and called"

    @check("subagents")
    def _subagents() -> str:
        from blux_runtime.builtin import builtin_registry
        from blux_runtime.executor import Runtime
        from blux_runtime.extensions.agents import AgentError, AgentRunner
        from blux_runtime.models import ModelRouter
        from blux_runtime.models import capability as mc

        reg = builtin_registry()
        mc.register(reg, ModelRouter(json.loads(routes.read_text())))
        runner = AgentRunner(Runtime(reg, mc.model_policy()), max_depth=1)
        res = runner.delegate(["echo.test"], [], "say hi", depth=0)
        assert res.text == "echo: say hi", res.text
        try:
            runner.delegate([], ["echo.test"], "x", depth=0)
        except AgentError:
            return "subagent answered through the real backend; tool widening refused"
        raise AssertionError("subagent gained a tool")

    @check("cron")
    def _cron() -> str:
        from blux_runtime.extensions import scheduler

        rt, _ = tool_runtime()
        store = scheduler.JobStore(work / "jobs.json")
        now = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)
        store.add("every 30m", "fs.read", {"path": "missing"}, now=now)
        ran = scheduler.run_due(store, rt.execute, now=now + dt.timedelta(minutes=31))
        assert len(ran) == 1 and ran[0]["receipt_id"]
        return "scheduled job ran unattended with a receipt"

    @check("gateway")
    def _gateway() -> str:
        import urllib.request

        from blux_runtime.builtin import builtin_registry, reference_policy
        from blux_runtime.executor import Runtime
        from blux_runtime.extensions.gateway import Gateway

        gw = Gateway(Runtime(builtin_registry(), reference_policy()), "g" * 40).start()
        try:
            host, port = gw.address
            with urllib.request.urlopen(f"http://{host}:{port}/v1/health", timeout=10) as resp:
                assert json.loads(resp.read()) == {"status": "ok"}
            try:
                urllib.request.urlopen(f"http://{host}:{port}/v1/capabilities", timeout=10)
                raise AssertionError("unauthenticated request accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 401
        finally:
            gw.stop()
        return "loopback gateway: health ok, unauthenticated request refused"

    @check("legacy_migration")
    def _legacy() -> str:
        legacy = home / ".hermes"
        legacy.mkdir()
        db = sqlite3.connect(legacy / "state.db")
        db.executescript(
            "CREATE TABLE sessions (id TEXT, source TEXT, title TEXT, model TEXT, started_at REAL, ended_at REAL,"
            " end_reason TEXT); CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
            " content TEXT, tool_name TEXT, timestamp REAL, reasoning TEXT);"
            "INSERT INTO sessions VALUES ('s','cli',NULL,NULL,1,NULL,NULL);"
            "INSERT INTO messages VALUES (1,'s','user','hi',NULL,1,'HIDDEN');"
        )
        db.commit()
        db.close()
        code, out, err = run_blux("migrate", "hermes", "--apply", "--json")
        doc = json.loads(out)
        assert code == 0 and doc["applied"]["imported_sessions"] == 1, err
        raw = (home / ".local" / "state" / "blux" / "sessions.db").read_bytes()
        assert b"HIDDEN" not in raw
        return "legacy session imported without hidden reasoning"

    @check("launcher")
    def _launcher() -> str:
        try:
            import rpds  # noqa: F401
        except ImportError:
            return "SKIP: rpds not importable from the interpreter's own site-packages (test root)"
        if args.extra_site:
            return "SKIP: native dependency provided via --extra-site (test root)"
        proc = subprocess.run(
            [py, "-I", str(prefix / "bin" / "blux"), "version"], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        return "bin/blux launcher runs"

    report = {
        "contract": "blux.install-validation",
        "interpreter": sys.executable,
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "prefix": str(prefix),
        "results": RESULTS,
        "pass": all(r["status"] != "FAIL" for r in RESULTS.values()),
    }
    sys.stdout.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
