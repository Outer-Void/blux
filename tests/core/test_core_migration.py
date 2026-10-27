"""Stage 13 HARD GATE: the migrated BLHermes core runs from BLUX, matches the characterization goldens,
keeps its compatibility path explicit and time-bounded, and passes the security tests.

Goldens: migration/blhermes/characterization/golden/*.json (recorded from the fork in Stage 11). Where BLUX
is deliberately stricter than BLHermes, the difference is listed explicitly and asserted.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blux_cli.main import main
from blux_runtime import state
from blux_runtime.compat import REGISTRY, hermes
from blux_runtime.executor import Runtime
from blux_runtime.hooks import HookBus, HookResult, HookSource
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.security import SessionApprovals, child_env, detect, redact, write_denied, write_needs_approval
from blux_runtime.sessions import SessionStore
from blux_runtime.tools import builtin as tools
from blux_sdk import envelopes
from tests.conftest import ROOT

GOLDEN = ROOT / "migration" / "blhermes" / "characterization" / "golden"
FAKE_KEY = "sk-proj-COREMIGRATIONFAKE000000000000"
USER = {"type": "user", "id": "u"}


def golden(name: str) -> Any:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


# 1-2. state root, profiles, compatibility ----------------------------------------------------------------------


def test_blux_home_and_profiles_follow_the_characterized_semantics(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    assert state.blux_home(env) == tmp_path / ".local" / "state" / "blux"
    assert state.blux_home({**env, "XDG_STATE_HOME": str(tmp_path / "x")}) == tmp_path / "x" / "blux"
    assert state.blux_home({**env, "BLUX_HOME": str(tmp_path / "b")}) == tmp_path / "b"
    # paths golden: the root is the "default" profile; a named profile lives under profiles/<name>.
    g = golden("paths")
    assert (
        g["named_profile_home(work)"] == "<HERMES_HOME>/profiles/work" and g["profile_name_for_home(root)"] == "default"
    )
    root = state.blux_home(env)
    work = state.profile_home("work", env)
    assert work == root / "profiles" / "work"
    work.mkdir(parents=True)
    root.mkdir(parents=True, exist_ok=True)
    assert state.profile_name_for_home(root, env) == "default"
    assert state.profile_name_for_home(work, env) == "work"
    assert state.profile_name_for_home(tmp_path, env) is None
    with pytest.raises(state.StateError):
        state.profile_home("../escape", env)


def test_blux_never_uses_hermes_home_as_its_state(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / ".hermes")}
    assert ".hermes" not in str(state.blux_home(env)) and ".hermes" not in str(state.active_layout(env).home)


def test_compatibility_paths_are_explicit_and_time_bounded() -> None:
    assert [r[0] for r in REGISTRY] == ["hermes-home-import"]
    for compat_id, module, expires, reads in REGISTRY:
        assert module.startswith("blux_runtime.compat.") and reads
        assert dt.date.today() <= expires, f"compatibility path {compat_id} expired on {expires}: remove it"
        assert expires <= dt.date(2026, 10, 26) + dt.timedelta(days=200)  # bounded: at most ~6 months
    hermes.check_not_expired(hermes.COMPAT_EXPIRES)
    with pytest.raises(hermes.CompatExpired):
        hermes.check_not_expired(hermes.COMPAT_EXPIRES + dt.timedelta(days=1))


def legacy_home(tmp_path: Path) -> Path:
    home = tmp_path / "legacy"
    home.mkdir()
    (home / ".env").write_text(
        f"OPENAI_API_KEY={FAKE_KEY}\nexport DEEPSEEK_API_KEY='dk-{FAKE_KEY}'\nTELEGRAM_BOT_TOKEN=1:x\n"
    )
    db = sqlite3.connect(home / "state.db")
    db.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, model TEXT, started_at REAL,"
        " ended_at REAL, end_reason TEXT, billing_provider TEXT);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,"
        " tool_name TEXT, timestamp REAL, reasoning TEXT, reasoning_content TEXT);"
    )
    db.execute("INSERT INTO sessions VALUES ('s1', 'cli', 'Quarterly', 'gpt', 1.0, 2.0, 'user_exit', 'nous')")
    db.execute("INSERT INTO sessions VALUES ('s2', 'telegram', NULL, NULL, 3.0, NULL, NULL, NULL)")
    rows = [
        ("s1", "user", "find the quarterly report", None, 1.1, None, None),
        ("s1", "assistant", "It is in reports/q3.pdf", None, 1.2, "SECRET-REASONING-1", "SECRET-REASONING-2"),
        ("s2", "user", f"my key is {FAKE_KEY}", None, 3.1, None, None),
    ]
    db.executemany(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp, reasoning, reasoning_content)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    db.close()
    return home


def test_legacy_import_is_read_only_idempotent_and_never_takes_hidden_reasoning(tmp_path: Path) -> None:
    home = legacy_home(tmp_path)
    before = {p.name: p.read_bytes() for p in home.iterdir()}
    store = SessionStore(tmp_path / "blux" / "sessions.db")
    env = {"HOME": str(tmp_path), "HERMES_HOME": str(home)}
    p = hermes.plan(store, env)
    assert (p.sessions, p.messages, p.credential_names) == (2, 3, ["OPENAI_API_KEY", "DEEPSEEK_API_KEY"])
    assert p.skipped_credentials == ["TELEGRAM_BOT_TOKEN"] and FAKE_KEY not in json.dumps(p.to_document())
    result = hermes.apply(store, env)
    assert (result["imported_sessions"], result["imported_messages"]) == (2, 3)
    assert hermes.apply(store, env)["imported_sessions"] == 0  # idempotent
    assert [m["role"] for m in store.messages("hermes-s1")] == ["user", "assistant"]
    assert store.get_session("hermes-s1")["end_reason"] == "user_exit"  # type: ignore[index]
    assert FAKE_KEY not in store.messages("hermes-s2")[0]["content"]  # redacted on the way in
    store.close()
    raw = (tmp_path / "blux" / "sessions.db").read_bytes()
    assert b"SECRET-REASONING" not in raw and b"nous" not in raw
    assert {p.name: p.read_bytes() for p in home.iterdir()} == before  # the legacy home is untouched


def test_credential_import_writes_names_privately_and_keeps_existing_values(tmp_path: Path) -> None:
    home = legacy_home(tmp_path)
    target = tmp_path / "bluxgpt" / ".env"
    target.parent.mkdir()
    target.write_text("DEEPSEEK_API_KEY=keep-me\n")
    added = hermes.import_credentials(home, target)
    assert added == ["OPENAI_API_KEY"]
    text = target.read_text()
    assert "DEEPSEEK_API_KEY=keep-me" in text and f"OPENAI_API_KEY={FAKE_KEY}" in text
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert hermes.import_credentials(home, target) == []


def test_blux_migrate_cli_dry_run_then_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = legacy_home(tmp_path)
    for k, v in {
        "HOME": str(tmp_path),
        "HERMES_HOME": str(home),
        "BLUX_HOME": str(tmp_path / "bh"),
        "BLUXGPT_CONFIG": str(tmp_path / "gpt.env"),
        "BLUX_CONFIG": str(tmp_path / "c.toml"),
    }.items():
        monkeypatch.setenv(k, v)
    out, err = io.StringIO(), io.StringIO()
    assert main(["migrate", "hermes"], stdout=out, stderr=err) == 0
    assert "2 to import" in out.getvalue() and "dry run" in out.getvalue() and FAKE_KEY not in out.getvalue()
    assert not (tmp_path / "gpt.env").exists()
    out = io.StringIO()
    assert main(["migrate", "hermes", "--apply", "--credentials", "--json"], stdout=out, stderr=err) == 0
    doc = json.loads(out.getvalue())
    assert doc["applied"]["imported_sessions"] == 2 and doc["applied"]["imported_credentials"] == [
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ]
    assert FAKE_KEY not in out.getvalue()
    assert main(["migrate", "hermes", "--credentials"], stdout=io.StringIO(), stderr=io.StringIO()) == 2


# 3. provider/model abstraction ------------------------------------------------------------------------------


def test_legacy_provider_credentials_map_onto_bluxgpt_names() -> None:
    providers = {p["id"]: p for p in golden("provider_registry")}
    assert "DEEPSEEK_API_KEY" in providers["deepseek"]["api_key_env_vars"]
    assert "ANTHROPIC_API_KEY" in providers["anthropic"]["api_key_env_vars"]
    assert set(hermes.PROVIDER_KEYS) == {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"}
    # The Nous Portal provider is not migrated (BLH-003 REMOVE); BluxGPT reads exactly these key names.
    assert not any(k.startswith("NOUS") for k in hermes.PROVIDER_KEYS)
    gpt = ROOT.parent / "blux-gpt" / "bluxgpt" / "config.py"
    if gpt.exists():
        assert all(k in gpt.read_text() for k in hermes.PROVIDER_KEYS)


# 7. sessions ---------------------------------------------------------------------------------------------------


def test_session_store_matches_the_session_golden_and_emits_events(tmp_path: Path) -> None:
    g = golden("session_store")
    seen: list[str] = []
    bus = HookBus()
    bus.register("session.*", lambda e: seen.append(e.name), source=HookSource("builtin", "t"))
    store = SessionStore(tmp_path / "s.db", hooks=bus)
    sid = store.create_session("char-1", "cli")
    store.append_message(sid, role="user", content="find the quarterly report")
    store.append_message(sid, role="assistant", content="The quarterly report is in reports/q3.pdf")
    hits = store.search_messages("quarterly")
    store.load_session(sid)
    store.end_session(sid, "user_exit")
    ended = store.get_session(sid) or {}
    assert (sid, len(hits), sorted({h["role"] for h in hits})) == (g["created_id"], g["search_hits"], g["search_roles"])
    assert (ended["end_reason"], ended["ended_at"] is not None, ended["message_count"]) == (
        g["end_reason"],
        g["ended"],
        g["message_count"],
    )
    assert seen == ["session.created", "session.loaded", "session.closed"]
    assert oct((tmp_path / "s.db").stat().st_mode & 0o777) == "0o600"


# 4-6. security -------------------------------------------------------------------------------------------------

STRICTER_COMMANDS = {"shutdown -h now", "kill -9 1", "fastboot flashing unlock"}


def test_dangerous_command_detection_covers_the_golden_and_is_stricter_only_where_listed() -> None:
    for row in golden("approval_detection"):
        d = detect(row["command"])
        if row["dangerous"] or row["command"] in STRICTER_COMMANDS:
            assert d.dangerous, row["command"]
        else:
            assert not d.dangerous, row["command"]
    assert detect("fastboot flashing unlock").device and detect("adb shell rm -rf /sdcard").device
    assert detect("adb shell recovery --wipe_data").device and detect("adb shell recovery --wipe_cache").dangerous
    assert detect("echo 'unterminated").dangerous and detect("x" * 20000).dangerous


def test_session_approvals_match_the_golden_and_never_cover_device_commands() -> None:
    g, a, s = golden("approval_session_state"), SessionApprovals(), "char-session"
    observed = {"initial_yolo": a.yolo(s), "initial_approved": a.is_approved(s, "rm -rf")}
    a.approve_session(s, "rm -rf")
    observed["after_approve_session"] = a.is_approved(s, "rm -rf")
    observed["other_session_sees_it"] = a.is_approved("other-session", "rm -rf")
    a.enable_yolo(s)
    observed["yolo_enabled"] = a.yolo(s)
    a.clear(s)
    observed["after_clear"] = {"yolo": a.yolo(s), "approved": a.is_approved(s, "rm -rf")}
    assert observed == g
    unlock = detect("fastboot flashing unlock")
    a.enable_yolo(s)
    a.approve_session(s, unlock.rule or "")
    assert not a.decide(s, "fastboot flashing unlock", unlock).allowed  # neither YOLO nor a session approval
    a.approve_once(s, "fastboot flashing unlock")
    assert a.decide(s, "fastboot flashing unlock", unlock).basis == "one-time-approval"
    assert not a.decide(s, "fastboot flashing unlock", unlock).allowed  # consumed


STRICTER_WRITES = {"$HERMES_HOME/auth.json": ("denied",), "~/.bashrc": ("approval",), "~/.gitconfig": ("approval",)}


def test_file_write_safety_covers_the_golden_and_is_stricter_only_where_listed(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / ".hermes")}
    for row in golden("file_safety"):
        path = row["target"].replace("~", str(tmp_path), 1).replace("$HERMES_HOME", str(tmp_path / ".hermes"), 1)
        denied, approval = write_denied(path, env), write_needs_approval(path, env)
        extra = STRICTER_WRITES.get(row["target"], ())
        assert denied == (row["write_denied"] or "denied" in extra), row["target"]
        assert approval == (row["approval_required"] or "approval" in extra), row["target"]
    link = tmp_path / "innocent.txt"
    (tmp_path / ".ssh").mkdir()
    link.symlink_to(tmp_path / ".ssh" / "authorized_keys")
    assert write_denied(link, env)  # symlinks are resolved first


def test_child_environment_is_an_allow_list_closing_the_characterized_gap() -> None:
    g = golden("child_env_scrub")
    assert {"AWS_SECRET_ACCESS_KEY", "DATABASE_URL"} <= set(g["scrubbed_keys"])  # what BLHermes let through
    base = {
        "PATH": "/usr/bin",
        "HOME": "/h",
        "LANG": "C.UTF-8",
        "OPENAI_API_KEY": FAKE_KEY,
        "AWS_SECRET_ACCESS_KEY": "x" * 40,
        "DATABASE_URL": "postgres://u:p@h/db",
        "MY_APP_SETTING": "v",
        "GITHUB_TOKEN": "ghp_" + "A" * 36,
    }
    assert sorted(child_env(base)) == ["HOME", "LANG", "PATH"]


def test_redaction_is_at_least_as_strong_as_the_golden() -> None:
    from importlib import util

    spec = util.spec_from_file_location("probe", ROOT / "migration" / "blhermes" / "characterization" / "probe.py")
    assert spec and spec.loader
    probe = util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    secrets = [
        "CHARACTERIZATIONFAKE",
        "abcdefghijklmnopqrstuvwxyz0123456789",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab",
        "hunter2secret",
        "AKIAIOSFODNN7EXAMPLE",
        "correct horse battery staple",
    ]
    for sample in probe.REDACT_SAMPLES:
        out = redact(sample)
        assert not any(s in out for s in secrets), out
    assert redact("nothing secret here") == "nothing secret here"


# 4. tool execution through the runtime --------------------------------------------------------------------


def tool_runtime(workspace: Path, bus: HookBus | None = None) -> tuple[Runtime, SessionApprovals]:
    reg = CapabilityRegistry()
    gate = tools.register(reg, workspace=workspace)
    return Runtime(reg, tools.tool_policy(), hooks=bus), gate


def call(rt: Runtime, cap: str, payload: dict[str, Any], session: str = "ses_01J00000000000000000000000") -> Any:
    return rt.execute(envelopes.execution_request(cap, payload, actor=USER, session_id=session)).result


def test_shell_exec_runs_safe_commands_with_a_credential_free_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    rt, _ = tool_runtime(tmp_path)
    res = call(rt, "shell.exec", {"command": "echo hi; env"})
    out = res["output"]
    assert res["status"] == "succeeded" and out["stdout"].startswith("hi") and out["approval"] == "safe"
    assert FAKE_KEY not in out["stdout"] and "OPENAI_API_KEY" not in out["stdout"]
    res = call(rt, "shell.exec", {"command": f"echo export TOKEN={FAKE_KEY}"})
    assert FAKE_KEY not in res["output"]["stdout"]  # output is redacted


def test_shell_exec_requires_approval_for_dangerous_and_device_commands(tmp_path: Path) -> None:
    rt, gate = tool_runtime(tmp_path)
    (tmp_path / "build").mkdir()
    res = call(rt, "shell.exec", {"command": "rm -rf build"})
    assert res["status"] == "failed" and res["error"]["details"]["reason"] == "approval_required"
    assert (tmp_path / "build").exists()
    gate.approve_session("ses_01J00000000000000000000000", "delete.recursive")
    assert call(rt, "shell.exec", {"command": "rm -rf build"})["output"]["approval"] == "session-approval"
    assert not (tmp_path / "build").exists()
    assert (
        call(rt, "shell.exec", {"command": "rm -rf build"}, session="ses_01J00000000000000000000001")["status"]
        == "failed"
    )
    gate.enable_yolo("ses_01J00000000000000000000000")
    res = call(rt, "shell.exec", {"command": "fastboot flashing unlock"})
    assert res["status"] == "failed" and res["error"]["details"]["device"] is True


def test_tools_are_confined_and_honour_write_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rt, gate = tool_runtime(tmp_path)
    assert call(rt, "fs.write", {"path": "notes/a.txt", "content": "hi"})["status"] == "succeeded"
    assert call(rt, "fs.read", {"path": "notes/a.txt"})["output"]["content"] == "hi"
    assert call(rt, "fs.read", {"path": "../../etc/passwd"})["error"]["details"]["reason"] == "outside_workspace"
    assert call(rt, "shell.exec", {"command": "pwd", "cwd": ".."})["error"]["details"]["reason"] == "outside_workspace"
    assert (
        call(rt, "fs.write", {"path": ".ssh/authorized_keys", "content": "x"})["error"]["details"]["reason"]
        == "denied_path"
    )
    res = call(rt, "fs.write", {"path": ".bashrc", "content": "alias x=y"})
    assert res["error"]["details"]["reason"] == "approval_required" and not (tmp_path / ".bashrc").exists()
    gate.approve_session("ses_01J00000000000000000000000", res["error"]["details"]["rule"])
    assert call(rt, "fs.write", {"path": ".bashrc", "content": "alias x=y"})["output"]["approval"] == "session-approval"


def test_tool_calls_emit_hook_events_and_a_hook_can_stop_them(tmp_path: Path) -> None:
    bus = HookBus()
    names: list[str] = []
    bus.register("tool.*", lambda e: names.append(e.name), source=HookSource("builtin", "obs"))
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.veto("frozen") if e.payload["tool"] == "shell.exec" else None,
        source=HookSource("builtin", "freeze"),
        permissions=["veto:tool.before_execute"],
    )
    rt, _ = tool_runtime(tmp_path, bus)
    assert call(rt, "fs.write", {"path": "a", "content": "x"})["status"] == "succeeded"
    res = call(rt, "shell.exec", {"command": "echo hi"})
    assert res["status"] == "rejected" and res["error"]["details"]["denied_by"] == "hook"
    assert names[:5] == [
        "tool.before_authorize",
        "tool.authorized",
        "tool.before_execute",
        "tool.after_execute",
        "tool.before_authorize",
    ]


def test_a_hung_command_is_killed_at_the_deadline(tmp_path: Path) -> None:
    rt, _ = tool_runtime(tmp_path)
    req = envelopes.execution_request("shell.exec", {"command": "sleep 30"}, actor=USER, timeout_ms=1500)
    import time

    t0 = time.monotonic()
    res = rt.execute(req).result
    assert time.monotonic() - t0 < 10 and res["status"] == "failed"


# hard gate: no fork at runtime -------------------------------------------------------------------------------


def test_migrated_core_runs_without_the_fork() -> None:
    code = (
        "import sys, json\n"
        "import blux_runtime.state, blux_runtime.sessions, blux_runtime.compat.hermes, blux_runtime.security\n"
        "import blux_runtime.tools.builtin, blux_runtime.executor, blux_runtime.hooks\n"
        "import blux_cli.main, blux_cli.migrate\n"
        "fork = {'agent', 'hermes_cli', 'hermes_state', 'hermes_constants', 'toolsets', 'model_tools',\n"
        "        'gateway', 'cron',\n"
        "        'run_agent', 'tui_gateway', 'acp_adapter', 'hermes_platform', 'utils'}\n"
        "print(json.dumps({'fork_modules': sorted(m for m in sys.modules if m.split('.')[0] in fork),\n"
        "                  'fork_paths': sorted({getattr(m, '__file__', '') or '' for m in list(sys.modules.values())\n"
        "                                        if 'blhermes-agent' in (getattr(m, '__file__', '') or '')})}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "HERMES_HOME")}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd="/", check=True)
    assert json.loads(proc.stdout) == {"fork_modules": [], "fork_paths": []}


def test_hook_events_name_no_upstream_hook_names() -> None:
    names = {"pre_tool_call", "post_tool_call", "on_session_start", "on_session_end", "pre_llm_call", "post_llm_call"}
    from blux_runtime.hooks import event_catalog

    assert names.isdisjoint(event_catalog())
