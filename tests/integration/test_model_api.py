"""HARD GATE (Stage 09): BLUX Model API v1 through the real BluxGPT backend process.

- A caller targets a logical BLUX model without knowing the backend provider.
- Provider failure degrades, routes or fails explicitly.
- Receipts (in BLUX System) carry safe metadata and no secrets.

MOCKED by default: BluxGPT's `mock` provider behind the real subprocess protocol. With BLUX_REAL_MODELS=1
one test also calls a REAL provider through the packaged default routes (needs BluxGPT credentials).
Needs `bluxgpt` and `blux-system`; skips unless present, or fails if BLUX_REQUIRE_BLUXGPT=1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry
from blux_runtime.executor import Runtime
from blux_runtime.models import ModelRouter
from blux_runtime.models import capability as model_capability
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import envelopes, ids, model_api


def _exe(name: str) -> str | None:
    local = Path(sys.executable).with_name(name)
    return str(local) if local.exists() else shutil.which(name)


BLUXGPT, SYSTEM = _exe("bluxgpt"), _exe("blux-system")
missing = [n for n, ok in (("bluxgpt", BLUXGPT), ("blux-system", SYSTEM)) if not ok]
if missing:
    if os.environ.get("BLUX_REQUIRE_BLUXGPT") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_BLUXGPT=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert BLUXGPT is not None and SYSTEM is not None
FAKE_KEY = "sk-proj-INTEGRATIONFAKEKEY0000000000"
PROMPT = "PRIVATE-PROMPT-TEXT about the quarterly numbers"
SERVICE = {"type": "service", "id": "app"}


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


def routes(main: list[dict[str, Any]], *, backend: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
    entry = f"{backend or BLUXGPT} blux-model-backend"
    table = {
        "contract": "blux.model-routes",
        "model_api_version": "1.0",
        "routes_id": "gate-routes",
        "backends": {"bluxgpt": {"kind": "subprocess", "entrypoint": entry}},
        "models": {m: {"routes": [mock_route(f"{m.split('-')[1]}.echo", "echo")]} for m in model_api.LOGICAL_MODELS},
        "defaults": {"max_attempts": 3, "allow_fallback": True, "timeout_ms": timeout_ms},
    }
    table["models"]["blux-main"]["routes"] = main
    return table


def system(*args: str) -> tuple[int, Any]:
    proc = subprocess.run([SYSTEM, *args], capture_output=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(Path(BLUXGPT).parent), str(Path(SYSTEM).parent), os.environ["PATH"]])
    )
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)  # passed to the backend only via credential_env
    monkeypatch.setenv("BLUXGPT_CONFIG", str(tmp_path / "no-config.env"))


def run(store: Path, table: dict[str, Any], payload: dict[str, Any], **kw: Any) -> Any:
    registry = builtin_registry()
    model_capability.register(registry, ModelRouter(table))
    runtime = Runtime(registry, model_capability.model_policy(), receipt_sink=BluxSystemSink(store, executable=SYSTEM))
    outcome = runtime.execute(envelopes.execution_request("model.generate", payload, actor=SERVICE, **kw))
    assert outcome.sink_error is None, outcome.sink_error
    return outcome


def everything_in(store: Path) -> str:
    return "\n".join(p.read_bytes().decode("utf-8", "replace") for p in store.rglob("*") if p.is_file())


def test_hard_gate_logical_model_without_provider_knowledge(store: Path) -> None:
    session = ids.new_session_id()
    outcome = run(
        store, routes([mock_route("main.mock", "echo")]), model_api.request("blux-main", PROMPT), session_id=session
    )
    assert outcome.result["status"] == "succeeded", outcome.result["error"]
    resp = outcome.result["output"]
    assert model_api.schema_errors("response", resp) == []
    assert resp["model"] == "blux-main" and resp["output"]["text"] == f"echo: {PROMPT}"
    assert (resp["provider"]["backend"], resp["provider"]["provider"]) == ("bluxgpt", "mock")
    assert resp["linkage"]["operation_id"] == outcome.result["operation_id"]
    # BLUX System has the receipt and its model-call artifact, linked.
    op = outcome.result["operation_id"]
    code, receipt = system("show", "--store", str(store), "--operation", op)
    assert code == 0 and len(receipt["artifact_ids"]) == 1
    code, graph = system("graph", "--store", str(store), "--session", session)
    assert code == 0 and graph["nodes"][op]["artifact_ids"] == receipt["artifact_ids"]


def test_hard_gate_provider_failure_degrades_with_every_attempt_recorded(store: Path) -> None:
    table = routes(
        [
            mock_route("main.down", "fail-provider_unavailable"),
            mock_route("main.broke", "fail-provider_quota_exceeded"),
            mock_route("main.backup", "echo", tier="fallback"),
        ]
    )
    outcome = run(store, table, model_api.request("blux-main", PROMPT))
    resp = outcome.result["output"]
    r = resp["routing"]
    assert r["route_id"] == "main.backup" and r["fallback_used"] and r["degraded"]
    assert [(a["route_id"], a["outcome"], a["error_code"]) for a in r["attempts"]] == [
        ("main.down", "failed", "PROVIDER_UNAVAILABLE"),
        ("main.broke", "failed", "PROVIDER_QUOTA_EXCEEDED"),
        ("main.backup", "succeeded", None),
    ]


@pytest.mark.parametrize(
    ("main", "provider_code", "attempted"),
    [
        (
            [mock_route("main.a", "fail-provider_unavailable"), mock_route("main.b", "fail-provider_error")],
            "ALL_ROUTES_FAILED",
            2,
        ),
        ([mock_route("main.a", "fail-content_filtered"), mock_route("main.b", "echo")], "CONTENT_FILTERED", 1),
    ],
)
def test_hard_gate_provider_failure_fails_explicitly(
    store: Path, main: list[dict[str, Any]], provider_code: str, attempted: int
) -> None:
    outcome = run(store, routes(main), model_api.request("blux-main", PROMPT))
    err = outcome.result["error"]
    assert outcome.result["status"] == "failed" and err["details"]["provider_code"] == provider_code
    attempts = err["details"]["model_error"]["attempts"]
    assert sum(a["outcome"] == "failed" for a in attempts) == attempted
    code, receipt = system("show", "--store", str(store), "--operation", outcome.result["operation_id"])
    assert code == 0 and receipt["status"] == "failed" and len(receipt["artifact_ids"]) == 1


def test_a_missing_backend_routes_to_the_next(store: Path, tmp_path: Path) -> None:
    table = routes([mock_route("main.a", "echo")], backend=str(tmp_path / "no-such-bluxgpt"))
    table["backends"]["spare"] = {"kind": "subprocess", "entrypoint": f"{BLUXGPT} blux-model-backend"}
    table["models"]["blux-main"]["routes"].append(
        {**mock_route("main.spare", "echo", tier="fallback"), "backend": "spare"}
    )
    resp = run(store, table, model_api.request("blux-main", "x")).result["output"]
    assert (
        resp["routing"]["attempts"][0]["error_code"] == "PROVIDER_NOT_CONFIGURED"
        and resp["routing"]["route_id"] == "main.spare"
    )


def test_a_hung_backend_is_killed_at_the_deadline(store: Path) -> None:
    t0 = time.monotonic()
    outcome = run(
        store, routes([mock_route("main.slow", "slow-20000")], timeout_ms=800), model_api.request("blux-main", "x")
    )
    assert time.monotonic() - t0 < 10
    assert outcome.result["error"]["details"]["provider_code"] in ("TIMEOUT", "ALL_ROUTES_FAILED")


def test_hard_gate_receipts_hold_safe_metadata_and_no_secrets(store: Path) -> None:
    ok = run(
        store,
        routes([mock_route("main.leaky", "leak"), mock_route("main.echo", "echo", tier="fallback")]),
        model_api.request("blux-main", PROMPT, system="PRIVATE-SYSTEM-PROMPT"),
    )
    assert ok.result["status"] == "succeeded"
    failed = run(store, routes([mock_route("main.leaky", "leak")]), model_api.request("blux-main", PROMPT))
    assert failed.result["status"] == "failed"
    stored = everything_in(store)
    for secret in (
        FAKE_KEY,
        "sk-proj-MOCKMOCKMOCKMOCKMOCKMOCKMOCK0000",
        PROMPT,
        "PRIVATE-SYSTEM-PROMPT",
        f"echo: {PROMPT}",
    ):
        assert secret not in stored, secret
    assert FAKE_KEY not in json.dumps([ok.result, ok.receipt, failed.result, failed.receipt])
    # ...while the safe metadata is there: provider, route, attempts, usage, timing.
    code, _receipt = system("show", "--store", str(store), "--operation", ok.result["operation_id"])
    assert code == 0
    assert '"provider_model":"echo"' in stored.replace(" ", "") or '"provider_model": "echo"' in stored
    assert "PROVIDER_ERROR" in stored and '"degraded"' in stored


def test_streaming_through_the_backend(store: Path) -> None:
    registry = builtin_registry()
    model_capability.register(registry, ModelRouter(routes([mock_route("main.mock", "echo")])))
    runtime = Runtime(registry, model_capability.model_policy(), receipt_sink=BluxSystemSink(store, executable=SYSTEM))
    events: list[dict[str, Any]] = []
    out = runtime.execute(
        envelopes.execution_request(
            "model.generate", model_api.request("blux-main", "one two", stream=True), actor=SERVICE
        ),
        on_event=events.append,
    )
    assert out.result["status"] == "succeeded"
    assert [e["type"] for e in events] == ["start", "route", "delta", "delta", "delta", "usage", "end"]
    assert "".join(e["data"]["text"] for e in events if e["type"] == "delta") == "echo: one two"


@pytest.mark.skipif(os.environ.get("BLUX_REAL_MODELS") != "1", reason="REAL provider path: set BLUX_REAL_MODELS=1")
def test_real_provider_through_default_routes(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REAL: blux-main through the packaged default routes and BluxGPT's own credentials (~/.config/bluxgpt)."""
    monkeypatch.delenv("BLUXGPT_CONFIG")
    monkeypatch.delenv("OPENAI_API_KEY")
    payload = model_api.request("blux-main", "Reply with the single word: ok", generation={"max_output_tokens": 400})
    outcome = run(store, model_capability.default_routes(), payload)
    assert outcome.result["status"] == "succeeded", outcome.result["error"]
    resp = outcome.result["output"]
    assert resp["provider"]["provider"] in ("openai", "anthropic", "deepseek") and resp["usage"]["reported_by_provider"]
    print(
        f"REAL route {resp['routing']['route_id']} ({resp['provider']['provider_model']}), "
        f"attempts={[(a['route_id'], a['outcome'], a['error_code']) for a in resp['routing']['attempts']]}"
    )
