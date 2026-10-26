"""HARD GATE (Stage 10): the installed `blux` command against the BLUX Model API and the real BluxGPT process.

- `blux "..."`, piped stdin, `blux chat`, `--model`, `--json` work end to end.
- Provider and configuration failures end cleanly: an explanation on stderr, a clear exit code, no traceback.
- Receipts land in BLUX System, linked by session, without prompts, input or credentials.

MOCKED by default (BluxGPT's `mock` provider behind the real subprocess protocol). With BLUX_REAL_MODELS=1
one test calls a REAL provider through the packaged default routes. Needs `blux`, `bluxgpt` and
`blux-system`; skips unless present, or fails if BLUX_REQUIRE_BLUXGPT=1.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from blux_sdk import model_api


def _exe(name: str) -> str | None:
    local = Path(sys.executable).with_name(name)
    return str(local) if local.exists() else shutil.which(name)


BLUX, BLUXGPT, SYSTEM = _exe("blux"), _exe("bluxgpt"), _exe("blux-system")
missing = [n for n, ok in (("blux", BLUX), ("bluxgpt", BLUXGPT), ("blux-system", SYSTEM)) if not ok]
if missing:
    if os.environ.get("BLUX_REQUIRE_BLUXGPT") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_BLUXGPT=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert BLUX is not None and BLUXGPT is not None and SYSTEM is not None
FAKE_KEY = "sk-proj-CLIGATEFAKEKEY000000000000"
PROMPT = "PRIVATE-PROMPT about the outage"
PIPED = "PRIVATE-LOG line 42: segfault"


def mock_route(route_id: str, behaviour: str, *, tier: str = "primary") -> dict[str, Any]:
    return {
        "route_id": route_id,
        "backend": "bluxgpt",
        "provider": "mock",
        "provider_model": behaviour,
        "locality": "local",
        "tier": tier,
        "capabilities": ["streaming", "tools", "structured_output", "deterministic"],
        "determinism": {"temperature": True, "seed": True},
        "credential_env": ["OPENAI_API_KEY"],
    }


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    path = os.pathsep.join([str(Path(BLUXGPT).parent), str(Path(SYSTEM).parent), os.environ["PATH"]])
    return {
        **os.environ,
        "PATH": path,
        "OPENAI_API_KEY": FAKE_KEY,
        "BLUX_CONFIG": str(tmp_path / "config.toml"),
        "BLUXGPT_CONFIG": str(tmp_path / "no-bluxgpt.env"),
    }


def routes(tmp_path: Path, main: list[dict[str, Any]], *, entry: str | None = None, timeout_ms: int = 30000) -> str:
    table = {
        "contract": "blux.model-routes",
        "model_api_version": "1.0",
        "routes_id": "cli-gate",
        "backends": {"bluxgpt": {"kind": "subprocess", "entrypoint": entry or f"{BLUXGPT} blux-model-backend"}},
        "models": {m: {"routes": [mock_route(f"{m.split('-')[1]}.echo", "echo")]} for m in model_api.LOGICAL_MODELS},
        "defaults": {"max_attempts": 3, "allow_fallback": True, "timeout_ms": timeout_ms},
    }
    table["models"]["blux-main"]["routes"] = main
    path = tmp_path / f"routes-{len(list(tmp_path.glob('routes-*')))}.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return str(path)


def blux(env: dict[str, str], *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [BLUX, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=None if stdin is not None else subprocess.DEVNULL,
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc


def test_hard_gate_direct_use_against_the_model_api(env: dict[str, str], tmp_path: Path) -> None:
    table = routes(tmp_path, [mock_route("main.echo", "echo")])
    p = blux(env, "--routes", table, "explain", "this", "error")
    assert (p.returncode, p.stdout) == (0, "echo: explain this error\n"), p.stderr
    p = blux(env, "--routes", table, "diagnose", stdin="ERROR: disk full\n")
    assert p.returncode == 0 and p.stdout.startswith("echo: diagnose") and "ERROR: disk full" in p.stdout
    p = blux(env, "--routes", table, "--model", "blux-code", "--json", "review")
    doc = json.loads(p.stdout)
    assert p.returncode == 0 and doc["status"] == "succeeded" and doc["output"]["model"] == "blux-code"
    assert doc["output"]["provider"]["route_id"] == "code.echo"
    p = blux(env, "chat", "--routes", table, stdin="hello\n/model blux-fast\nagain\n/exit\n")
    assert p.returncode == 0 and p.stdout == "echo: hello\necho: again\n", p.stderr


def test_hard_gate_provider_failure_degrades_visibly(env: dict[str, str], tmp_path: Path) -> None:
    table = routes(
        tmp_path,
        [
            mock_route("main.down", "fail-provider_unavailable"),
            mock_route("main.broke", "fail-provider_quota_exceeded"),
            mock_route("main.backup", "echo", tier="fallback"),
        ],
    )
    p = blux(env, "--routes", table, "-v", "x")
    assert p.returncode == 0 and p.stdout == "echo: x\n"
    assert "fallback route" in p.stderr
    assert "main.down: failed (PROVIDER_UNAVAILABLE)" in p.stderr
    assert "main.broke: failed (PROVIDER_QUOTA_EXCEEDED)" in p.stderr


@pytest.mark.parametrize(
    ("main", "kwargs", "exit_code", "words"),
    [
        (
            [mock_route("main.a", "fail-provider_unavailable"), mock_route("main.b", "fail-provider_error")],
            {},
            1,
            "ALL_ROUTES_FAILED",
        ),
        ([mock_route("main.a", "fail-content_filtered")], {}, 1, "does not route around a refusal"),
        ([mock_route("main.a", "echo")], {"entry": "no-such-bluxgpt blux-model-backend"}, 3, "blux doctor"),
        ([mock_route("main.a", "slow-20000")], {"timeout_ms": 800}, 4, "raise --timeout"),
    ],
)
def test_hard_gate_failures_end_cleanly(
    env: dict[str, str],
    tmp_path: Path,
    main: list[dict[str, Any]],
    kwargs: dict[str, Any],
    exit_code: int,
    words: str,
) -> None:
    table = routes(tmp_path, main, **kwargs)
    t0 = time.monotonic()
    p = blux(env, "--routes", table, "x")
    assert time.monotonic() - t0 < 30
    assert (p.returncode, p.stdout) == (exit_code, ""), p.stderr
    assert words in p.stderr and "operation op_" in p.stderr
    p = blux(env, "--routes", table, "--json", "x")
    assert p.returncode == exit_code and json.loads(p.stdout)["status"] == "failed"


def test_hard_gate_config_failures_end_cleanly(env: dict[str, str], tmp_path: Path) -> None:
    Path(env["BLUX_CONFIG"]).write_text(f'api_key = "{FAKE_KEY}"\n')
    p = blux(env, "x")
    assert p.returncode == 3 and "does not store provider credentials" in p.stderr and FAKE_KEY not in p.stderr
    Path(env["BLUX_CONFIG"]).unlink()
    (tmp_path / "broken.json").write_text("{")
    p = blux(env, "--routes", str(tmp_path / "broken.json"), "x")
    assert p.returncode == 3 and "routes table" in p.stderr
    p = blux(env, "--model", "gpt-5", "x")
    assert p.returncode == 2


def _children(pid: int) -> list[int]:
    out = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[1]) == pid:
            out.append(int(stat.parent.name))
    return out


def _wait_for_child(pid: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _children(pid):
        time.sleep(0.1)
    time.sleep(0.5)  # the backend is running the slow route


def test_ctrl_c_cancels_the_call_and_stops_the_backend(env: dict[str, str], tmp_path: Path) -> None:
    table = routes(tmp_path, [mock_route("main.slow", "slow-30000")])
    proc = subprocess.Popen(
        [BLUX, "--routes", table, "x"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    _wait_for_child(proc.pid)  # interrupt the call in flight, not the CLI's start-up
    t0 = time.monotonic()
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=30)
    assert time.monotonic() - t0 < 10
    assert (proc.returncode, out) == (130, ""), err
    # Usually the call is in flight ("cancelled"); if the signal lands just before it starts the CLI reports
    # "interrupted". Either way: exit 130, nothing on stdout, no traceback, and the process ends promptly.
    assert ("cancelled" in err or "interrupted" in err) and "Traceback" not in err


def test_hard_gate_receipts_are_linked_and_safe(env: dict[str, str], tmp_path: Path) -> None:
    store = tmp_path / "truth"
    assert subprocess.run([SYSTEM, "init", "--store", str(store)], capture_output=True).returncode == 0
    env = {**env, "BLUX_SYSTEM_STORE": str(store)}
    table = routes(tmp_path, [mock_route("main.echo", "echo")])
    p = blux(env, "--routes", table, "--json", PROMPT, stdin=f"{PIPED}\nOPENAI_API_KEY={FAKE_KEY}\n")
    doc = json.loads(p.stdout)
    assert p.returncode == 0 and "redacted 1" in p.stderr
    op = doc["operation_id"]
    shown = subprocess.run([SYSTEM, "show", "--store", str(store), "--operation", op], capture_output=True, text=True)
    receipt = json.loads(shown.stdout)
    assert shown.returncode == 0 and receipt["session_id"] and len(receipt["artifact_ids"]) == 1
    p = blux(env, "chat", "--routes", table, "--json", stdin="one\ntwo\n")
    first, second = (json.loads(line) for line in p.stdout.splitlines())
    graph = subprocess.run(
        [SYSTEM, "graph", "--store", str(store), "--session", second["output"]["linkage"]["session_id"]],
        capture_output=True,
        text=True,
    )
    nodes = json.loads(graph.stdout)["nodes"]
    assert graph.returncode == 0 and set(nodes) == {first["operation_id"], second["operation_id"]}
    assert second["output"]["linkage"]["parent_operation_id"] == first["operation_id"]
    stored = "\n".join(f.read_bytes().decode("utf-8", "replace") for f in store.rglob("*") if f.is_file())
    for secret in (FAKE_KEY, PROMPT, PIPED, "echo: one"):
        assert secret not in stored, secret


def test_an_unrecorded_receipt_fails_closed(env: dict[str, str], tmp_path: Path) -> None:
    table = routes(tmp_path, [mock_route("main.echo", "echo")])
    p = blux({**env, "BLUX_SYSTEM_STORE": str(tmp_path / "never-initialised")}, "--routes", table, "x")
    assert p.returncode == 5 and p.stdout == "echo: x\n" and "NOT recorded" in p.stderr


@pytest.mark.skipif(os.environ.get("BLUX_REAL_MODELS") != "1", reason="REAL provider path: set BLUX_REAL_MODELS=1")
def test_real_provider_through_default_routes(tmp_path: Path) -> None:
    """REAL: `blux` with the packaged default routes and BluxGPT's own credentials (~/.config/bluxgpt)."""
    env = {**os.environ, "BLUX_CONFIG": str(tmp_path / "config.toml")}
    env["PATH"] = os.pathsep.join([str(Path(BLUXGPT).parent), env["PATH"]])
    env.pop("BLUXGPT_CONFIG", None)
    p = blux(env, "--max-output-tokens", "400", "-v", "Reply with the single word: ok", stdin=None)
    assert p.returncode == 0 and p.stdout.strip(), p.stderr
    print(f"REAL {p.stderr.strip()}")
    p = blux(env, "--json", "--max-output-tokens", "400", "Reply with the single word: ok")
    doc = json.loads(p.stdout)
    assert p.returncode == 0 and doc["output"]["usage"]["reported_by_provider"]
