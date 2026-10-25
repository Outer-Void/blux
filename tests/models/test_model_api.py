"""BLUX Model API v1: SDK validation, router behaviour, the model.generate capability and its receipts.

Backends here are in-process (CallableBackend): MOCKED. tests/integration/test_model_api.py runs the real
BluxGPT backend process.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import InMemoryReceiptSink, Runtime
from blux_runtime.models import CallableBackend, ModelRouter
from blux_runtime.models import capability as model_capability
from blux_sdk import envelopes, model_api
from blux_sdk.model_api import ModelApiError
from blux_sdk.schemas import check_envelope

FAKE_KEY = "sk-proj-ROUTERTESTROUTERTEST00000000"


def route(
    route_id: str,
    provider: str = "p1",
    *,
    caps: tuple[str, ...] = ("streaming", "tools", "structured_output"),
    tier: str = "primary",
    locality: str = "remote",
    temperature: bool = True,
    seed: bool = False,
) -> dict[str, Any]:
    return {
        "route_id": route_id,
        "backend": "test",
        "provider": provider,
        "provider_model": f"{provider}-model",
        "locality": locality,
        "tier": tier,
        "capabilities": list(caps),
        "determinism": {"temperature": temperature, "seed": seed},
    }


def table(*routes: dict[str, Any], model: str = "blux-main", **defaults: Any) -> dict[str, Any]:
    others = {m: {"routes": [route(f"{m}.x", "p9")]} for m in model_api.LOGICAL_MODELS if m != model}
    return {
        "contract": "blux.model-routes",
        "model_api_version": "1.0",
        "routes_id": "test-routes",
        "backends": {"test": {"kind": "subprocess", "entrypoint": "unused-in-tests"}},
        "models": {model: {"routes": list(routes)}, **others},
        "defaults": {"max_attempts": 3, "allow_fallback": True, "timeout_ms": 5000, **defaults},
    }


def reply(
    request: dict[str, Any],
    rt: dict[str, Any],
    *,
    text: str | None = "ok",
    json_value: Any = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "contract": "blux.model-response",
        "model_api_version": "1.0",
        "response_id": model_api.new_response_id(),
        "model": request["model"],
        "status": "tool_calls" if tool_calls else "completed",
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "output": {"text": text, "json": json_value, "tool_calls": tool_calls or []},
        "usage": {
            "input_tokens": 3,
            "output_tokens": 1,
            "total_tokens": 4,
            "reasoning_tokens": None,
            "cached_input_tokens": None,
            "reported_by_provider": True,
        },
        "provider": {
            "backend": "test",
            "provider": rt["provider"],
            "provider_model": rt["provider_model"],
            "route_id": rt["route_id"],
            "locality": rt["locality"],
            "request_id": "req-1",
        },
        "routing": {
            "route_id": rt["route_id"],
            "fallback_used": False,
            "degraded": False,
            "notes": [],
            "attempts": [
                {
                    "route_id": rt["route_id"],
                    "provider": rt["provider"],
                    "provider_model": rt["provider_model"],
                    "outcome": "succeeded",
                    "error_code": None,
                    "retryable": None,
                    "reason": None,
                    "latency_ms": 1,
                    "retries": 0,
                }
            ],
        },
        "determinism": {"requested": False, "supported": False, "applied": {"temperature": None, "seed": None}},
        "timing": {"latency_ms": 1, "first_token_ms": None},
        "linkage": None,
    }


class Script:
    """A backend whose behaviour per route is scripted: a ModelApiError code to raise, or a response maker."""

    def __init__(self, **behaviour: Any) -> None:
        self.behaviour = behaviour
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: dict[str, Any], rt: dict[str, Any], emit: Any) -> dict[str, Any]:
        self.seen.append((rt["route_id"], request))
        b = self.behaviour.get(rt["route_id"].replace(".", "_"), "ok")
        if isinstance(b, str) and b != "ok":
            raise ModelApiError(b, f"scripted {b}", provider=rt["provider"], route_id=rt["route_id"])
        if callable(b):
            return b(request, rt, emit)  # type: ignore[no-any-return]
        emit({"type": "delta", "data": {"text": "ok"}})
        return reply(request, rt)


def router(script: Script, *routes: dict[str, Any], **defaults: Any) -> ModelRouter:
    return ModelRouter(table(*routes, **defaults), {"test": CallableBackend(script)})


def req(**fields: Any) -> dict[str, Any]:
    return model_api.request("blux-main", "hello", **fields)


# SDK ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "code"),
    [
        ({**model_api.request("gpt-4", "x")}, "UNKNOWN_MODEL"),
        ({**model_api.request("blux-main", "x"), "api_key": "nope"}, "INVALID_REQUEST"),
        (model_api.request("blux-main", "x", tool_choice={"name": "ghost"}), "INVALID_REQUEST"),
        (
            model_api.request("blux-main", messages=[{"role": "tool", "tool_call_id": "c9", "content": "r"}]),
            "INVALID_REQUEST",
        ),
        (
            model_api.request(
                "blux-main", "x", response_format={"type": "json_schema", "name": "n", "schema": {"type": 5}}
            ),
            "INVALID_REQUEST",
        ),
        (model_api.request("blux-main", messages=[{"role": "assistant", "content": "hi"}]), "INVALID_REQUEST"),
    ],
)
def test_requests_are_checked(doc: dict[str, Any], code: str) -> None:
    with pytest.raises(ModelApiError) as info:
        model_api.check_request(doc)
    assert info.value.code == code


def test_required_capabilities_follow_the_request() -> None:
    r = req(
        stream=True,
        tools=[{"name": "t", "description": "", "input_schema": {"type": "object"}}],
        response_format={"type": "json_schema", "name": "n", "schema": {}},
        capabilities=["deterministic"],
    )
    assert model_api.required_capabilities(r) == ["streaming", "tools", "structured_output", "deterministic"]


def test_errors_are_scrubbed_and_typed() -> None:
    err = ModelApiError("PROVIDER_AUTH_FAILED", f"bad key {FAKE_KEY}", details={"echo": f"Bearer {FAKE_KEY}"})
    doc = err.to_document()
    assert FAKE_KEY not in json.dumps(doc) and (doc["retryable"], doc["fallback"]) == (False, True)
    assert model_api.schema_errors("error", doc) == []
    assert ModelApiError("CONTENT_FILTERED", "x").fallback is False


def test_packaged_default_routes_are_valid() -> None:
    routes = model_capability.default_routes()
    assert model_api.schema_errors("routes", routes) == []
    assert ModelRouter(routes).catalog()["models"][0]["model"] == "blux-main"


# Router -------------------------------------------------------------------------------------------------


def test_a_logical_model_is_served_without_naming_a_provider() -> None:
    script = Script()
    resp = router(script, route("main.a")).generate(req())
    assert model_api.schema_errors("response", resp) == []
    assert (resp["model"], resp["provider"]["provider"], resp["routing"]["degraded"]) == ("blux-main", "p1", False)
    assert "provider" not in json.dumps(script.seen[0][1]["messages"])


def test_fallback_is_explicit_and_degraded() -> None:
    script = Script(main_a="PROVIDER_UNAVAILABLE")
    resp = router(script, route("main.a"), route("main.b", "p2", tier="fallback")).generate(req())
    r = resp["routing"]
    assert r["route_id"] == "main.b" and r["fallback_used"] and r["degraded"] and r["notes"]
    assert [(a["route_id"], a["outcome"], a["error_code"]) for a in r["attempts"]] == [
        ("main.a", "failed", "PROVIDER_UNAVAILABLE"),
        ("main.b", "succeeded", None),
    ]


@pytest.mark.parametrize("code", ["CONTENT_FILTERED", "PROVIDER_REJECTED", "INVALID_REQUEST"])
def test_non_fallback_errors_fail_at_once(code: str) -> None:
    script = Script(main_a=code)
    with pytest.raises(ModelApiError) as info:
        router(script, route("main.a"), route("main.b", "p2")).generate(req())
    assert info.value.code == code and [s[0] for s in script.seen] == ["main.a"]
    assert info.value.attempts[0]["error_code"] == code


def test_fallback_can_be_disabled() -> None:
    script = Script(main_a="PROVIDER_RATE_LIMITED")
    with pytest.raises(ModelApiError) as info:
        router(script, route("main.a"), route("main.b", "p2")).generate(req(routing={"allow_fallback": False}))
    assert info.value.code == "PROVIDER_RATE_LIMITED" and info.value.details["fallback_disabled"] is True
    assert [a["outcome"] for a in info.value.attempts] == ["failed", "skipped"]


def test_all_routes_failing() -> None:
    script = Script(main_a="PROVIDER_QUOTA_EXCEEDED", main_b="PROVIDER_TIMEOUT")
    with pytest.raises(ModelApiError) as info:
        router(script, route("main.a"), route("main.b", "p2")).generate(req())
    assert info.value.code == "ALL_ROUTES_FAILED" and info.value.retryable
    assert [a["error_code"] for a in info.value.attempts] == ["PROVIDER_QUOTA_EXCEEDED", "PROVIDER_TIMEOUT"]


def test_capability_filtering_and_provider_lists() -> None:
    script = Script()
    rt = router(script, route("main.a", caps=("streaming",)), route("main.b", "p2"), route("main.c", "p3"))
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
    script.behaviour["main_b"] = lambda r, rr, e: reply(r, rr, text=None, json_value={"x": 1})
    resp = rt.generate(req(response_format={"type": "json_schema", "name": "x", "schema": schema}))
    assert (
        resp["routing"]["route_id"] == "main.b"
        and resp["routing"]["attempts"][0]["reason"] == "route lacks structured_output"
    )
    resp = rt.generate(req(routing={"exclude_providers": ["p1", "p2"]}))
    assert resp["routing"]["route_id"] == "main.c"
    with pytest.raises(ModelApiError) as info:
        rt.generate(req(capabilities=["local_only"]))
    assert info.value.code == "CAPABILITY_UNAVAILABLE"


def test_invalid_structured_output_moves_to_the_next_route() -> None:
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
    script = Script(
        main_a=lambda r, rr, e: reply(r, rr, text=None, json_value={"x": "nope"}),
        main_b=lambda r, rr, e: reply(r, rr, text=None, json_value={"x": 2}),
    )
    resp = router(script, route("main.a"), route("main.b", "p2")).generate(
        req(response_format={"type": "json_schema", "name": "x", "schema": schema})
    )
    assert resp["output"]["json"] == {"x": 2}
    assert resp["routing"]["attempts"][0]["error_code"] == "STRUCTURED_OUTPUT_INVALID"


def test_tool_calls_are_validated_never_executed() -> None:
    tools = [
        {
            "name": "lookup",
            "description": "",
            "input_schema": {"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}},
        }
    ]
    bad = lambda r, rr, e: reply(r, rr, text=None, tool_calls=[{"id": "1", "name": "rm_rf", "arguments": {}}])  # noqa: E731
    good = lambda r, rr, e: reply(r, rr, text=None, tool_calls=[{"id": "2", "name": "lookup", "arguments": {"q": "x"}}])  # noqa: E731
    resp = router(Script(main_a=bad, main_b=good), route("main.a"), route("main.b", "p2")).generate(req(tools=tools))
    assert resp["status"] == "tool_calls" and resp["output"]["tool_calls"][0]["name"] == "lookup"
    assert resp["routing"]["attempts"][0]["error_code"] == "TOOL_CALL_INVALID"


def test_deterministic_controls_where_the_route_exposes_them() -> None:
    script = Script()
    resp = router(script, route("main.a", temperature=True, seed=True)).generate(
        req(deterministic=True, generation={"temperature": 0.8})
    )
    assert script.seen[-1][1]["generation"] == {"temperature": 0, "seed": 0}
    assert resp["determinism"] == {"requested": True, "supported": True, "applied": {"temperature": 0, "seed": 0}}
    resp = router(script, route("main.a", temperature=False, seed=False)).generate(
        req(deterministic=True, generation={"temperature": 0.8})
    )
    assert "generation" not in script.seen[-1][1] and resp["determinism"]["supported"] is False
    assert any("deterministic" in n for n in resp["routing"]["notes"])


def test_timeout_and_cancellation() -> None:
    def slow(r: dict[str, Any], rr: dict[str, Any], e: Any) -> dict[str, Any]:
        time.sleep(0.3)
        raise ModelApiError("PROVIDER_TIMEOUT", "slow", provider=rr["provider"], route_id=rr["route_id"])

    with pytest.raises(ModelApiError) as info:
        router(Script(main_a=slow), route("main.a"), timeout_ms=200).generate(req())
    assert info.value.code == "TIMEOUT"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ModelApiError) as info:
        router(Script(), route("main.a")).generate(req(), cancel=cancel)
    assert info.value.code == "CANCELLED"


def test_stream_events_are_valid_and_sequenced() -> None:
    events: list[dict[str, Any]] = []
    router(Script(main_a="PROVIDER_ERROR"), route("main.a"), route("main.b", "p2")).generate(
        req(stream=True), on_event=events.append
    )
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert [e["type"] for e in events] == ["start", "route", "route", "delta", "end"]
    assert all(model_api.schema_errors("stream-event", e) == [] for e in events)


# Through the runtime: policy, receipts, safe metadata ---------------------------------------------------


def runtime(
    script: Script, *routes: dict[str, Any], policy: dict[str, Any] | None = None
) -> tuple[Runtime, InMemoryReceiptSink]:
    registry = builtin_registry()
    model_capability.register(registry, router(script, *routes))
    sink = InMemoryReceiptSink()
    return Runtime(registry, policy or model_capability.model_policy(), receipt_sink=sink), sink


def execute(rt: Runtime, payload: dict[str, Any], **kw: Any) -> Any:
    return rt.execute(envelopes.execution_request("model.generate", payload, actor={"type": "agent", "id": "a1"}), **kw)


def test_receipts_carry_safe_metadata_only() -> None:
    def answer(r: dict[str, Any], rr: dict[str, Any], e: Any) -> dict[str, Any]:
        return reply(r, rr, text="THE SECRET ANSWER")

    rt, sink = runtime(Script(main_a="PROVIDER_UNAVAILABLE", main_b=answer), route("main.a"), route("main.b", "p2"))
    outcome = execute(rt, req(system="SYSTEM PROMPT TEXT"))
    assert outcome.result["status"] == "succeeded"
    assert outcome.result["output"]["linkage"]["operation_id"] == outcome.result["operation_id"]
    receipt = outcome.receipt
    [(artifact, data)] = sink.artifacts
    assert receipt["artifact_ids"] == [artifact["artifact_id"]]
    check_envelope(artifact, "artifact")
    record = json.loads(data)
    assert record["provider"]["provider"] == "p2" and record["routing"]["fallback_used"] is True
    assert [a["error_code"] for a in record["routing"]["attempts"]] == ["PROVIDER_UNAVAILABLE", None]
    assert artifact["labels"]["degraded"] == "true" and artifact["labels"]["model"] == "blux-main"
    everything = json.dumps([receipt, artifact, record])
    for private in ("hello", "SYSTEM PROMPT TEXT", "THE SECRET ANSWER"):
        assert private not in everything


def test_failures_are_recorded_with_their_attempts() -> None:
    rt, sink = runtime(
        Script(main_a="PROVIDER_QUOTA_EXCEEDED", main_b="PROVIDER_ERROR"), route("main.a"), route("main.b", "p2")
    )
    outcome = execute(rt, req())
    err = outcome.result["error"]
    assert outcome.result["status"] == "failed" and err["code"] == "EXECUTION_FAILED"
    assert err["details"]["provider_code"] == "ALL_ROUTES_FAILED"
    [(artifact, data)] = sink.artifacts
    assert outcome.receipt["artifact_ids"] == [artifact["artifact_id"]]
    assert json.loads(data)["error_code"] == "ALL_ROUTES_FAILED"


def test_secrets_never_reach_results_receipts_or_artifacts() -> None:
    def leak(r: dict[str, Any], rr: dict[str, Any], e: Any) -> dict[str, Any]:
        raise ModelApiError(
            "PROVIDER_AUTH_FAILED",
            f"key {FAKE_KEY} rejected; Authorization: Bearer {FAKE_KEY}",
            details={"echo": FAKE_KEY},
        )

    rt, sink = runtime(Script(main_a=leak, main_b=leak), route("main.a"), route("main.b", "p2"))
    outcome = execute(rt, req())
    assert FAKE_KEY not in json.dumps([outcome.result, outcome.receipt, [(a, d.decode()) for a, d in sink.artifacts]])


def test_policy_still_decides() -> None:
    rt, _ = runtime(Script(), route("main.a"), policy=reference_policy())
    outcome = execute(rt, req())
    assert outcome.result["error"]["code"] == "POLICY_DENIED" and outcome.receipt["artifact_ids"] == []


def test_runtime_forwards_stream_events() -> None:
    rt, _ = runtime(Script(), route("main.a"))
    events: list[dict[str, Any]] = []
    execute(rt, req(stream=True), on_event=events.append)
    assert [e["type"] for e in events] == ["start", "route", "delta", "end"]


def test_catalog_capability() -> None:
    rt, _ = runtime(Script(), route("main.a"))
    out = rt.execute(envelopes.execution_request("model.catalog", {}, actor={"type": "agent", "id": "a"}))
    assert [m["model"] for m in out.result["output"]["models"]] == list(model_api.LOGICAL_MODELS)


@pytest.mark.parametrize("kind", ["request", "response", "catalog", "error"])
def test_bundled_schemas_resolve_every_reference(kind: str) -> None:
    from jsonschema import Draft202012Validator

    schema = model_api.bundled_schema(kind)
    Draft202012Validator.check_schema(schema)
    text = json.dumps(schema)
    assert "urn:blux:model-api" not in text
    for ref in {r.split('"')[0] for r in text.split('"$ref": "')[1:]}:
        assert ref.startswith("#/$defs/") and ref.rsplit("/", 1)[-1] in schema["$defs"], ref
