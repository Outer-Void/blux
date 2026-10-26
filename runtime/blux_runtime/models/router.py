"""The BLUX model router: logical model -> provider route, with explicit fallback (ADR-0010).

For each request the router:

1. validates it (`check_request`) and works out which capabilities a route must have;
2. filters the logical model's routes, in table order, by those capabilities, `local_only`, and the
   request's provider allow and deny lists. A filtered-out route is recorded as a `skipped` attempt with
   its reason;
3. tries the remaining routes in order. A failure moves on to the next route only when the error's
   `fallback` flag allows it, fallback is enabled (request or table default) and attempts remain. Anything
   else fails at once;
4. checks what the backend returned: the response schema, structured output against the requested JSON
   Schema, and tool calls against the declared tools. A violation counts as a failed attempt of that route;
5. returns the response with authoritative `routing`, `determinism`, `timing` and `linkage` sections.

`degraded` is true when the serving route is not the model's first route or is marked `tier: fallback`.
It is never silent: the response says which route served it and every attempt before it. When no route
succeeds, the error is ALL_ROUTES_FAILED (or the first non-fallback error) with every attempt attached.

With a hook bus (ADR-0012) the router emits model.before_route (vetoable; hooks may only exclude more
providers), model.after_route, and for each attempt model.before_call (a veto skips that route),
credential.requested (variable names only, never values) and model.after_call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from blux_runtime.hooks.bus import Emission, EventContext, HookBus, HookError
from blux_runtime.models.backends import ModelBackend, SubprocessModelBackend
from blux_sdk import model_api
from blux_sdk.model_api import ModelApiError

EventSink = Callable[[dict[str, Any]], None]


class ModelRouter:
    def __init__(
        self,
        routes: Mapping[str, Any],
        backends: Mapping[str, ModelBackend] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        hooks: HookBus | None = None,
    ) -> None:
        self.hooks = hooks
        self.routes = model_api.validate("routes", routes, error_code="INVALID_REQUEST")
        ids = [r["route_id"] for m in self.routes["models"].values() for r in m["routes"]]
        if len(ids) != len(set(ids)):
            raise ValueError("route_id values must be unique across the routing table")
        self.backends: dict[str, ModelBackend] = {
            name: SubprocessModelBackend(name, spec["entrypoint"], env=env)
            for name, spec in self.routes["backends"].items()
        }
        self.backends.update(backends or {})
        for model in self.routes["models"].values():
            for route in model["routes"]:
                if route["backend"] not in self.backends:
                    raise ValueError(f"route {route['route_id']} uses undeclared backend {route['backend']!r}")

    def catalog(self) -> dict[str, Any]:
        models = []
        for name in model_api.LOGICAL_MODELS:
            routes = self.routes["models"][name]["routes"]
            caps = sorted({c for r in routes for c in r["capabilities"]}, key=model_api.CAPABILITIES.index)
            models.append(
                {"model": name, "purpose": model_api.MODEL_PURPOSE[name], "capabilities": caps, "routes": len(routes)}
            )
        return {
            "contract": model_api.KINDS["catalog"],
            "model_api_version": model_api.MODEL_API_VERSION,
            "routes_id": self.routes["routes_id"],
            "models": models,
        }

    def generate(
        self,
        request: Any,
        *,
        on_event: EventSink | None = None,
        cancel: threading.Event | None = None,
        linkage: dict[str, Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict[str, Any]:
        req = model_api.check_request(request)
        cancel = cancel or threading.Event()
        emit = _Sequencer(on_event)
        t0 = clock()
        defaults = self.routes["defaults"]
        timeout_ms = min(req.get("timeout_ms", defaults["timeout_ms"]), defaults["timeout_ms"])
        deadline = t0 + timeout_ms / 1000
        routing = dict(req.get("routing") or {})
        allow_fallback = routing.get("allow_fallback", defaults["allow_fallback"])
        needed = model_api.required_capabilities(req)
        hook_ctx = _hook_context(linkage)
        before = self._hook(
            "model.before_route",
            {"model": req["model"], "required_capabilities": needed, "allow_fallback": bool(allow_fallback),
             "exclude_providers": list(routing.get("exclude_providers") or []), "messages": req["messages"]},
            hook_ctx,
        )  # fmt: skip
        if before is not None:
            if not before.proceed:
                raise ModelApiError(
                    "INVALID_REQUEST",
                    "A hook refused this model request before routing.",
                    details={"denied_by": "hook", "hook_id": before.vetoed_by, "reason": before.veto_reason},
                )
            if before.payload["exclude_providers"]:
                routing["exclude_providers"] = list(before.payload["exclude_providers"])  # hooks only add exclusions
        all_routes = self.routes["models"][req["model"]]["routes"]
        attempts: list[dict[str, Any]] = []
        candidates = []
        for route in all_routes:
            reason = _skip_reason(route, needed, routing)
            if reason:
                attempts.append(_attempt(route, "skipped", reason=reason))
            else:
                candidates.append(route)
        self._hook(
            "model.after_route",
            {"model": req["model"], "candidates": [r["route_id"] for r in candidates],
             "skipped": [a["route_id"] for a in attempts]},
            hook_ctx,
        )  # fmt: skip
        emit("start", {"model": req["model"]})
        if not candidates:
            error = ModelApiError(
                "CAPABILITY_UNAVAILABLE",
                f"No route for {req['model']} provides {', '.join(needed) or 'the requested routing'}.",
                attempts=attempts,
                details={"required_capabilities": needed},
            )
            emit("error", {"error": error.to_document()})
            raise error
        tried = 0
        last_error: ModelApiError | None = None
        for route in candidates:
            if tried >= defaults["max_attempts"] or (tried and not allow_fallback):
                attempts.append(
                    _attempt(
                        route, "skipped", reason="fallback disabled" if not allow_fallback else "attempt limit reached"
                    )
                )
                continue
            if cancel.is_set():
                last_error = ModelApiError("CANCELLED", "Cancelled by the caller.", attempts=attempts)
                break
            if clock() >= deadline:
                last_error = ModelApiError("TIMEOUT", f"No route finished within {timeout_ms} ms.", attempts=attempts)
                break
            gate = self._hook(
                "model.before_call",
                {"model": req["model"], "route_id": route["route_id"], "provider": route["provider"],
                 "provider_model": route["provider_model"], "attempt": tried + 1},
                hook_ctx,
            )  # fmt: skip
            if gate is not None and not gate.proceed:
                attempts.append(_attempt(route, "skipped", reason=f"vetoed by hook {gate.vetoed_by}"))
                continue
            if route.get("credential_env"):
                self._hook(
                    "credential.requested",
                    {"names": list(route["credential_env"]), "purpose": "model backend", "route_id": route["route_id"]},
                    hook_ctx,
                )
            tried += 1
            emit(
                "route",
                {
                    "route_id": route["route_id"],
                    "provider": route["provider"],
                    "provider_model": route["provider_model"],
                    "attempt": tried,
                },
            )
            backend_request, determinism = _apply_determinism(req, route)
            route_deadline = min(deadline, clock() + route.get("timeout_ms", timeout_ms) / 1000)
            started = clock()
            try:
                response = self.backends[route["backend"]].generate(
                    backend_request,
                    route,
                    deadline=route_deadline,
                    cancel=cancel,
                    on_event=emit.forward,
                    context=dict(linkage or {}),
                )
                response = self._check_response(req, route, response)
            except ModelApiError as exc:
                exc.provider = exc.provider or route["provider"]
                exc.route_id = exc.route_id or route["route_id"]
                self._after_call(req["model"], route, "failed", exc.code, _ms(clock() - started), None, hook_ctx)
                attempts.append(
                    _attempt(
                        route,
                        "failed",
                        error=exc,
                        latency_ms=_ms(clock() - started),
                        retries=int(exc.details.get("retries", 0)),
                    )
                )
                last_error = exc
                if exc.code == "CANCELLED" or cancel.is_set():
                    last_error = ModelApiError("CANCELLED", "Cancelled by the caller.", attempts=attempts)
                    break
                if exc.code == "PROVIDER_TIMEOUT" and clock() >= deadline:
                    last_error = ModelApiError(
                        "TIMEOUT", f"No route finished within {timeout_ms} ms.", attempts=attempts
                    )
                    break
                if not exc.fallback:
                    last_error.attempts = attempts
                    break
                continue
            self._after_call(
                req["model"], route, "succeeded", None, _ms(clock() - started), response.get("usage"), hook_ctx
            )
            backend_attempt = (response.get("routing") or {}).get("attempts") or [{}]
            attempts.append(
                _attempt(
                    route,
                    "succeeded",
                    latency_ms=_ms(clock() - started),
                    retries=int(backend_attempt[-1].get("retries", 0)),
                )
            )
            primary = all_routes[0]["route_id"]
            notes = [f"served by {route['route_id']} after {tried - 1} failed attempt(s)"] if tried > 1 else []
            if route["route_id"] != primary and tried == 1:
                notes.append(f"primary route {primary} was skipped")
            if determinism["requested"] and not determinism["supported"]:
                notes.append("deterministic controls requested but this route exposes none")
            response.update(
                {
                    "model": req["model"],
                    "routing": {
                        "route_id": route["route_id"],
                        "attempts": attempts,
                        "fallback_used": tried > 1,
                        "degraded": route["route_id"] != primary or route["tier"] == "fallback",
                        "notes": notes,
                    },
                    "determinism": determinism,
                    "timing": {
                        "latency_ms": _ms(clock() - t0),
                        "first_token_ms": response["timing"].get("first_token_ms"),
                    },
                    "linkage": _linkage(linkage),
                }
            )
            response = model_api.validate("response", response)
            emit("end", {"response": response})
            return response
        if last_error is not None and last_error.fallback and not allow_fallback:
            error = last_error  # fallback was possible but the request (or table) disabled it: fail as-is
            error.attempts = attempts
            error.details["fallback_disabled"] = True
        elif last_error is None or last_error.fallback:
            error = ModelApiError(
                "ALL_ROUTES_FAILED",
                f"Every route for {req['model']} failed.",
                attempts=attempts,
                details={"last_error": last_error.to_document() if last_error else None},
            )
        else:
            error = last_error
            error.attempts = attempts
        emit("error", {"error": error.to_document()})
        raise error

    def _hook(self, name: str, payload: dict[str, Any], context: EventContext) -> Emission | None:
        if self.hooks is None:
            return None
        try:
            return self.hooks.emit(name, payload, context=context)
        except HookError:
            if name in ("model.before_route", "model.before_call"):
                raise ModelApiError("INTERNAL_ERROR", f"Hook emission failed ({name}).") from None
            return None

    def _after_call(
        self,
        model: str,
        route: Mapping[str, Any],
        outcome: str,
        error_code: str | None,
        latency_ms: int,
        usage: Mapping[str, Any] | None,
        context: EventContext,
    ) -> None:
        self._hook(
            "model.after_call",
            {"model": model, "route_id": route["route_id"], "provider": route["provider"], "outcome": outcome,
             "error_code": error_code, "latency_ms": latency_ms,
             "input_tokens": (usage or {}).get("input_tokens"), "output_tokens": (usage or {}).get("output_tokens")},
            context,
        )  # fmt: skip

    def _check_response(self, req: Mapping[str, Any], route: Mapping[str, Any], response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise ModelApiError("BACKEND_PROTOCOL_ERROR", "The backend returned no response object.")
        model_api.validate("response", response)
        fmt = req.get("response_format") or {}
        output = response["output"]
        if fmt.get("type") == "json_schema":
            if output["json"] is None:
                raise ModelApiError("STRUCTURED_OUTPUT_INVALID", "The model returned no JSON output.")
            errors = model_api.structured_errors(output["json"], fmt["schema"])
            if errors:
                raise ModelApiError(
                    "STRUCTURED_OUTPUT_INVALID",
                    f"Output does not match {fmt['name']}: {errors[0]}",
                    details={"errors": errors},
                )
        tools = {t["name"]: t for t in req.get("tools") or []}
        for call in output["tool_calls"]:
            if call["name"] not in tools:
                raise ModelApiError("TOOL_CALL_INVALID", f"The model called an undeclared tool {call['name']!r}.")
            errors = model_api.structured_errors(call["arguments"], tools[call["name"]]["input_schema"])
            if errors:
                raise ModelApiError(
                    "TOOL_CALL_INVALID",
                    f"Arguments for {call['name']} are invalid: {errors[0]}",
                    details={"errors": errors},
                )
        if response["provider"]["route_id"] != route["route_id"]:
            raise ModelApiError("BACKEND_PROTOCOL_ERROR", "The backend reported a different route than it was given.")
        return dict(response)


class _Sequencer:
    """Re-numbers events so the caller sees one sequence across routes."""

    def __init__(self, sink: EventSink | None) -> None:
        self.sink = sink
        self.seq = 0

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if self.sink is None:
            return
        self.sink(
            {
                "contract": model_api.KINDS["stream-event"],
                "model_api_version": model_api.MODEL_API_VERSION,
                "seq": self.seq,
                "type": kind,
                "data": data,
            }
        )
        self.seq += 1

    def forward(self, event: dict[str, Any]) -> None:
        self(event["type"], event["data"])


def _skip_reason(route: Mapping[str, Any], needed: list[str], routing: Mapping[str, Any]) -> str | None:
    missing = [c for c in needed if c not in route["capabilities"] and c != "local_only"]
    if missing:
        return f"route lacks {', '.join(missing)}"
    if "local_only" in needed and route["locality"] != "local":
        return "local_only requested; route is remote"
    if routing.get("providers") and route["provider"] not in routing["providers"]:
        return "provider not in the request's allow list"
    if route["provider"] in (routing.get("exclude_providers") or []):
        return "provider excluded by the request"
    return None


def _apply_determinism(req: Mapping[str, Any], route: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    out = dict(req)
    gen = dict(req.get("generation") or {})
    requested = bool(req.get("deterministic"))
    applied: dict[str, Any] = {"temperature": gen.get("temperature"), "seed": gen.get("seed")}
    caps = route["determinism"]
    if requested:
        if caps["temperature"]:
            gen["temperature"] = applied["temperature"] = 0
        if caps["seed"]:
            gen["seed"] = applied["seed"] = gen.get("seed", 0)
    if not caps["temperature"]:
        gen.pop("temperature", None)
        applied["temperature"] = None
    if not caps["seed"]:
        gen.pop("seed", None)
        applied["seed"] = None
    if gen:
        out["generation"] = gen
    else:
        out.pop("generation", None)
    supported = caps["temperature"] or caps["seed"]
    return out, {"requested": requested, "supported": bool(supported), "applied": applied}


def _attempt(
    route: Mapping[str, Any],
    outcome: str,
    *,
    error: ModelApiError | None = None,
    reason: str | None = None,
    latency_ms: int | None = None,
    retries: int = 0,
) -> dict[str, Any]:
    return {
        "route_id": route["route_id"],
        "provider": route["provider"],
        "provider_model": route["provider_model"],
        "outcome": outcome,
        "error_code": error.code if error else None,
        "retryable": error.retryable if error else None,
        "reason": (reason or (error.message if error else None) or "")[:256] or None,
        "latency_ms": latency_ms,
        "retries": retries,
    }


def _hook_context(linkage: Mapping[str, Any] | None) -> EventContext:
    link = linkage or {}
    return EventContext(
        operation_id=link.get("operation_id"),
        parent_operation_id=link.get("parent_operation_id"),
        session_id=link.get("session_id"),
        project_id=link.get("project_id"),
        capability_id=link.get("capability_id"),
    )


def _linkage(linkage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not linkage:
        return None
    keys = ("operation_id", "parent_operation_id", "session_id", "project_id", "capability_id")
    return {k: linkage.get(k) for k in keys}


def _ms(seconds: float) -> int:
    return max(0, int(seconds * 1000))
