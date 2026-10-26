"""The BLUX hook bus (Hook System v1, ADR-0012).

BLUX owns the bus. Components inside BLUX emit typed, versioned events from the catalog. Hooks subscribe
and can do exactly three things:

  observe  see the event (secret-scrubbed; sensitive fields need `read_sensitive:<event>`)
  veto     stop a vetoable `before` event (needs `veto:<event>`)
  mutate   change a declared mutable field, and only in its narrowing direction
           (needs `mutate:<event>.<field>`)

There is no "allow". A hook cannot authorize, grant, widen or un-deny anything. A timeout, failure or
cancellation never counts as approval. Events about device operations accept no mutation at all.

Determinism: handlers run one at a time, in descending priority and then registration order. Each emission
produces one audit receipt (blux.hook-receipt) that names every handler, its source and its outcome, with
payload hashes but no payload values.
"""

from __future__ import annotations

import asyncio
import contextvars
import fnmatch
import inspect
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from blux_runtime.hooks import catalog
from blux_runtime.hooks.catalog import EventSpec
from blux_sdk import ids
from blux_sdk.model_api import scrub_doc
from blux_sdk.serialization import sha256_of
from blux_sdk.timeutil import utc_now

SOURCE_KINDS = ("builtin", "project", "plugin", "skill", "mcp")
EXTERNAL_KINDS = ("plugin", "skill", "mcp")
FAILURE_POLICIES = ("ignore", "deny")
# Fields no hook may ever change. Touching one is reported as an elevation attempt.
PROTECTED_FIELDS = frozenset(
    {"side_effects", "device_operation", "capability_id", "tool", "actor", "permissions", "grants", "policy_id",
     "rule_id", "denied_by", "required_capabilities", "allow_fallback"}
)  # fmt: skip
SENSITIVE_MASK = "[sensitive: needs read_sensitive]"
ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
PERMISSION_RE = re.compile(r"^(observe|veto|read_sensitive|emit|mutate):[a-z*][a-z_.*]*$")
DEFAULT_GRANTS: dict[str, tuple[str, ...]] = {
    "builtin:*": ("observe:*", "veto:*", "mutate:*", "read_sensitive:*", "emit:*"),
    "project:*": ("observe:*",),
}

_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("blux_hook_depth", default=0)
_ACTIVE: contextvars.ContextVar[frozenset[tuple[str, str]]] = contextvars.ContextVar(
    "blux_hook_active", default=frozenset()
)
_CURRENT: contextvars.ContextVar[Registration | None] = contextvars.ContextVar("blux_hook_current", default=None)


class HookError(Exception):
    """A registration or emission broke the hook contract."""


class HookPermissionError(HookError):
    pass


# ------------------------------------------------------------------------------------------- values


@dataclass(frozen=True)
class HookSource:
    """Who a hook belongs to. Recorded in every receipt."""

    kind: str
    id: str
    version: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise HookError(f"unknown hook source kind {self.kind!r}")
        if not ID_RE.match(self.id):
            raise HookError(f"invalid hook source id {self.id!r}")

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.id}" + (f"@{self.version}" if self.version else "")


@dataclass(frozen=True)
class HookResult:
    """What a handler may answer. Returning None means `ok`."""

    action: str = "ok"
    reason: str | None = None
    mutations: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def ok(cls) -> HookResult:
        return cls()

    @classmethod
    def veto(cls, reason: str) -> HookResult:
        return cls("veto", reason=str(reason)[:256])

    @classmethod
    def mutate(cls, **fields: Any) -> HookResult:
        return cls("mutate", mutations=MappingProxyType(dict(fields)))


@dataclass(frozen=True)
class EventContext:
    operation_id: str | None = None
    parent_operation_id: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    capability_id: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class HookEvent:
    """What a handler receives. Immutable: core metadata and payload cannot be changed in place."""

    name: str
    version: str
    event_id: str
    sequence: int
    emitted_at: str
    depth: int
    context: EventContext
    payload: Mapping[str, Any]

    def to_document(self) -> dict[str, Any]:
        return {
            "contract": "blux.hook-event",
            "hooks_version": catalog.HOOKS_VERSION,
            "event_id": self.event_id,
            "name": self.name,
            "version": self.version,
            "sequence": self.sequence,
            "emitted_at": self.emitted_at,
            "depth": self.depth,
            "context": self.context.to_dict(),
            "payload": thaw(self.payload),
        }


Handler = Callable[[HookEvent], "HookResult | Awaitable[HookResult | None] | None"]


@dataclass
class Registration:
    hook_id: str
    events: frozenset[str]
    handler: Handler
    source: HookSource
    permissions: frozenset[str]
    priority: int
    timeout_ms: int
    failure_policy: str
    seq: int
    failures: int = 0
    quarantined: bool = False

    def holds(self, permission: str) -> bool:
        return any(fnmatch.fnmatchcase(permission, p) for p in self.permissions)


@dataclass(frozen=True)
class Emission:
    """The outcome of one emission: the final (possibly narrowed) event, whether to proceed, and its receipt."""

    event: HookEvent
    proceed: bool
    vetoed_by: str | None
    veto_reason: str | None
    cancelled: bool
    receipt: dict[str, Any]

    @property
    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = thaw(self.event.payload)
        return result


class HookPolicy:
    """What each source may be granted, as permission patterns keyed by `kind:id` (or `kind:*`).

    External sources (plugin, skill, mcp) get nothing unless granted, and can never hold `emit:`.
    """

    def __init__(self, grants: Mapping[str, Iterable[str]] | None = None) -> None:
        self.grants = {k: tuple(v) for k, v in (DEFAULT_GRANTS if grants is None else grants).items()}

    def permits(self, source: HookSource, permission: str) -> bool:
        if permission.startswith("emit:") and source.kind != "builtin":
            return False
        patterns = self.grants.get(f"{source.kind}:{source.id}", ()) + self.grants.get(f"{source.kind}:*", ())
        return any(fnmatch.fnmatchcase(permission, p) for p in patterns)


# ------------------------------------------------------------------------------------------- bus


class HookBus:
    def __init__(
        self,
        *,
        policy: HookPolicy | None = None,
        receipt_sink: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], str] = utc_now,
        max_depth: int = 4,
        breaker_threshold: int = 3,
        default_timeout_ms: int = 1000,
        max_timeout_ms: int = 30000,
    ) -> None:
        self.policy = policy or HookPolicy()
        self.receipt_sinks: list[Callable[[dict[str, Any]], None]] = [receipt_sink] if receipt_sink else []
        self.clock = clock
        self.max_depth = max_depth
        self.breaker_threshold = breaker_threshold
        self.default_timeout_ms = default_timeout_ms
        self.max_timeout_ms = max_timeout_ms
        self._lock = threading.Lock()
        self._registrations: dict[str, Registration] = {}
        self._seq = 0
        self._event_seq = 0

    # registration ----------------------------------------------------------------------------------

    def register(
        self,
        events: str | Iterable[str],
        handler: Handler,
        *,
        source: HookSource,
        permissions: Iterable[str] = (),
        hook_id: str | None = None,
        priority: int = 0,
        timeout_ms: int | None = None,
        failure_policy: str = "ignore",
    ) -> Registration:
        patterns = [events] if isinstance(events, str) else list(events)
        names = frozenset(n for p in patterns for n in catalog.catalog() if fnmatch.fnmatchcase(n, p))
        perms = frozenset(permissions) | {f"observe:{p}" for p in patterns}
        with self._lock:
            self._seq += 1
            seq = self._seq
            hid = hook_id or f"{source.kind}.{source.id}.{seq}"

        def refuse(why: str, kind: str = "registration") -> HookError:
            self._violation(kind, hid, source, None, why)
            return HookPermissionError(why) if kind == "permission" else HookError(why)

        if not ID_RE.match(hid):
            raise refuse(f"invalid hook_id {hid!r}")
        if not patterns or any(not any(fnmatch.fnmatchcase(n, p) for n in catalog.catalog()) for p in patterns):
            raise refuse(f"{hid}: subscribes to an unknown event pattern {patterns!r}")
        for p in sorted(perms):
            if not PERMISSION_RE.match(p):
                raise refuse(f"{hid}: malformed permission {p!r}")
            if not self.policy.permits(source, p):
                raise refuse(f"{hid}: {source.label} is not granted {p!r}", "permission")
        low, high = (-1000, 1000) if source.kind == "builtin" else (-100, 100)
        if not low <= priority <= high:
            raise refuse(f"{hid}: priority {priority} outside {low}..{high} for {source.kind} hooks")
        timeout = self.default_timeout_ms if timeout_ms is None else timeout_ms
        if not 1 <= timeout <= self.max_timeout_ms:
            raise refuse(f"{hid}: timeout_ms {timeout} outside 1..{self.max_timeout_ms}")
        if failure_policy not in FAILURE_POLICIES:
            raise refuse(f"{hid}: unknown failure_policy {failure_policy!r}")
        if failure_policy == "deny":
            # Failing closed is a veto: allowed only on vetoable events the hook may veto.
            bad = [
                n
                for n in names
                if not catalog.spec(n).vetoable or not any(fnmatch.fnmatchcase(f"veto:{n}", q) for q in perms)
            ]
            if bad:
                raise refuse(f"{hid}: failure_policy deny needs veto permission on vetoable events; not {sorted(bad)}")
        reg = Registration(hid, names, handler, source, perms, priority, timeout, failure_policy, seq)
        with self._lock:
            if hid in self._registrations:
                raise refuse(f"hook_id {hid!r} is already registered")
            self._registrations[hid] = reg
        return reg

    def unregister(self, hook_id: str) -> bool:
        with self._lock:
            return self._registrations.pop(hook_id, None) is not None

    def registrations(self) -> tuple[Registration, ...]:
        with self._lock:
            return tuple(sorted(self._registrations.values(), key=lambda r: (-r.priority, r.seq)))

    def add_receipt_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        self.receipt_sinks.append(sink)

    def remove_receipt_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        if sink in self.receipt_sinks:
            self.receipt_sinks.remove(sink)

    # emission ---------------------------------------------------------------------------------------

    def emit(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        context: EventContext | None = None,
        cancel: threading.Event | None = None,
    ) -> Emission:
        run = self._begin(name, payload, context, cancel)
        while (reg := run.next()) is not None:
            run.apply(reg, *self._call_sync(reg, run.view_for(reg), run.depth))
        return run.finish()

    async def aemit(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        context: EventContext | None = None,
        cancel: threading.Event | None = None,
    ) -> Emission:
        run = self._begin(name, payload, context, cancel)
        while (reg := run.next()) is not None:
            run.apply(reg, *(await self._call_async(reg, run.view_for(reg), run.depth)))
        return run.finish()

    def _begin(
        self, name: str, payload: Mapping[str, Any], context: EventContext | None, cancel: threading.Event | None
    ) -> _Run:
        spec = catalog.spec(name)
        current = _CURRENT.get()
        if current is not None and not current.holds(f"emit:{spec.family}"):
            self._violation("permission", current.hook_id, current.source, name, f"hook may not emit {name}")
            raise HookPermissionError(f"{current.hook_id} may not emit {name}")
        with self._lock:
            self._event_seq += 1
            sequence = self._event_seq
            candidates = sorted(
                (r for r in self._registrations.values() if name in r.events), key=lambda r: (-r.priority, r.seq)
            )
        event = HookEvent(
            name=name,
            version=catalog.HOOKS_VERSION,
            event_id=f"evt_{ids.ulid()}",
            sequence=sequence,
            emitted_at=self.clock(),
            depth=_DEPTH.get(),
            context=context or EventContext(),
            payload=freeze(scrub_doc(dict(payload))),
        )
        errors = catalog.schema_errors("event", event.to_document())
        if errors:
            raise HookError(f"{name}: payload does not match the v1 contract: {errors[0]}")
        return _Run(self, spec, event, candidates, cancel)

    def _call_sync(self, reg: Registration, event: HookEvent, depth: int) -> tuple[str, Any]:
        box: dict[str, Any] = {}
        ctx = _handler_context(reg, event.name, depth)

        def target() -> None:
            try:
                result = reg.handler(event)
                if inspect.isawaitable(result):
                    result = asyncio.run(_await(result))
                box["result"] = result
            except BaseException as exc:  # noqa: BLE001 - failure isolation: a hook can never break the caller
                box["error"] = exc

        thread = threading.Thread(target=ctx.run, args=(target,), daemon=True, name=f"blux-hook-{reg.hook_id}")
        thread.start()
        thread.join(reg.timeout_ms / 1000)
        if thread.is_alive():
            return "timeout", None  # the thread is abandoned; nothing it returns is ever applied
        if "error" in box:
            return "error", box["error"]
        return "result", box.get("result")

    async def _call_async(self, reg: Registration, event: HookEvent, depth: int) -> tuple[str, Any]:
        ctx = _handler_context(reg, event.name, depth)
        loop = asyncio.get_running_loop()
        try:
            if inspect.iscoroutinefunction(reg.handler):

                async def runner() -> Any:
                    _apply_context(ctx)
                    return await reg.handler(event)  # type: ignore[misc]

                result = await asyncio.wait_for(runner(), reg.timeout_ms / 1000)
            else:
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, ctx.run, reg.handler, event), reg.timeout_ms / 1000
                )
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(_await(result), reg.timeout_ms / 1000)
        except asyncio.TimeoutError:
            return "timeout", None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - failure isolation
            return "error", exc
        return "result", result

    # internal ---------------------------------------------------------------------------------------

    def _violation(
        self, kind: str, hook_id: str | None, source: HookSource | None, event: str | None, detail: str
    ) -> None:
        if event == "security.violation" or _DEPTH.get() >= self.max_depth:
            return  # never recurse on violations; the receipt of the offending emission records it
        try:
            token = _CURRENT.set(None)  # the bus itself emits violations, not the offending hook
            try:
                self.emit(
                    "security.violation",
                    {"kind": kind, "hook_id": hook_id, "source": source.label if source else None, "event": event,
                     "detail": detail[:256]},
                )  # fmt: skip
            finally:
                _CURRENT.reset(token)
        except HookError:
            pass

    def _deliver(self, receipt: dict[str, Any]) -> None:
        for sink in list(self.receipt_sinks):
            try:
                sink(receipt)
            except Exception:  # noqa: BLE001, S112 - a failing sink must not break emission; others still get it
                continue


class _Run:
    """One emission in progress: ordering, outcomes, mutations and the receipt."""

    def __init__(
        self,
        bus: HookBus,
        spec: EventSpec,
        event: HookEvent,
        candidates: list[Registration],
        cancel: threading.Event | None,
    ) -> None:
        self.bus, self.spec, self.event, self.cancel = bus, spec, event, cancel
        self.depth = event.depth
        self.queue = list(candidates)
        self.handlers: list[dict[str, Any]] = []
        self.payload: dict[str, Any] = thaw(event.payload)
        self.initial_sha = sha256_of(self.payload)
        self.vetoed_by: str | None = None
        self.veto_reason: str | None = None
        self.cancelled = False
        self.recursion_limited = self.depth >= bus.max_depth
        self.started = time.monotonic()
        self.pending_violations: list[tuple[str, Registration, str]] = []
        if self.recursion_limited:
            self.queue = []

    def view_for(self, reg: Registration) -> HookEvent:
        payload = dict(self.payload)
        if not reg.holds(f"read_sensitive:{self.spec.name}"):
            for f in self.spec.sensitive:
                if f in payload:
                    payload[f] = SENSITIVE_MASK
        return HookEvent(
            name=self.event.name,
            version=self.event.version,
            event_id=self.event.event_id,
            sequence=self.event.sequence,
            emitted_at=self.event.emitted_at,
            depth=self.depth,
            context=self.event.context,
            payload=freeze(payload),
        )

    def record(
        self,
        reg: Registration,
        outcome: str,
        ms: int = 0,
        mutations: list[str] | None = None,
        detail: str | None = None,
    ) -> None:
        self.handlers.append(
            {"hook_id": reg.hook_id, "source": reg.source.label, "priority": reg.priority, "outcome": outcome,
             "duration_ms": ms, "mutations": mutations or [], "detail": (detail or None) and str(detail)[:256]}
        )  # fmt: skip

    def next(self) -> Registration | None:
        while self.queue:
            reg = self.queue.pop(0)
            if self.vetoed_by is not None:
                self.record(reg, "skipped", detail=f"vetoed by {self.vetoed_by}")
                continue
            if self.cancel is not None and self.cancel.is_set():
                self.cancelled = True
                self.record(reg, "cancelled")
                continue
            if reg.quarantined:
                self.record(reg, "quarantined", detail=f"disabled after {reg.failures} consecutive failures")
                continue
            if (reg.hook_id, self.spec.name) in _ACTIVE.get():
                self.record(reg, "skipped", detail="re-entrant: the hook is already handling this event")
                continue
            self._t0 = time.monotonic()
            return reg
        return None

    def apply(self, reg: Registration, kind: str, value: Any) -> None:
        ms = int((time.monotonic() - self._t0) * 1000)
        if kind in ("timeout", "error"):
            reg.failures += 1
            detail = "exceeded timeout" if kind == "timeout" else f"raised {type(value).__name__}"
            if reg.failures >= self.bus.breaker_threshold:
                reg.quarantined = True
            if reg.failure_policy == "deny" and self.spec.vetoable:
                self._veto(reg, f"hook {kind} with failure_policy deny")
            self.record(reg, kind, ms, detail=detail)
            return
        reg.failures = 0
        result = HookResult.ok() if value is None else value
        if not isinstance(result, HookResult):
            self.record(reg, "rejected", ms, detail=f"returned {type(result).__name__}, not a HookResult")
            self._flag("protocol", reg, "handler returned something other than a HookResult (there is no allow)")
            return
        if result.action == "ok":
            self.record(reg, "ok", ms)
        elif result.action == "veto":
            if not self.spec.vetoable:
                self.record(reg, "rejected", ms, detail=f"{self.spec.name} is not vetoable")
                self._flag("protocol", reg, f"veto on non-vetoable {self.spec.name}")
            elif not reg.holds(f"veto:{self.spec.name}"):
                self.record(reg, "not_permitted", ms, detail="missing veto permission")
                self._flag("permission", reg, f"veto without veto:{self.spec.name}")
            else:
                self._veto(reg, result.reason or "vetoed")
                self.record(reg, "veto", ms, detail=self.veto_reason)
        elif result.action == "mutate":
            self._mutate(reg, result, ms)
        else:
            self.record(reg, "rejected", ms, detail=f"unknown action {result.action!r}")
            self._flag("protocol", reg, f"unknown action {result.action!r} (hooks cannot allow or authorize)")

    def _veto(self, reg: Registration, reason: str) -> None:
        if self.vetoed_by is None:
            self.vetoed_by, self.veto_reason = reg.hook_id, scrub_doc(str(reason))[:256]

    def _mutate(self, reg: Registration, result: HookResult, ms: int) -> None:
        fields = sorted(result.mutations)
        if self.payload.get("device_operation") is True:
            self.record(reg, "rejected", ms, detail="device operations accept no mutation")
            self._flag("device", reg, f"mutation of a device-operation {self.spec.name}")
            return
        for f in fields:
            if f in PROTECTED_FIELDS or f not in self.spec.mutable:
                kind = "elevation" if f in PROTECTED_FIELDS else "mutation"
                self.record(reg, "rejected", ms, detail=f"{f} is not mutable on {self.spec.name}")
                self._flag(kind, reg, f"attempted to change {f} on {self.spec.name}")
                return
            if not reg.holds(f"mutate:{self.spec.name}.{f}"):
                self.record(reg, "not_permitted", ms, detail=f"missing mutate:{self.spec.name}.{f}")
                self._flag("permission", reg, f"mutation of {f} without permission")
                return
        candidate = dict(self.payload)
        for f in fields:
            new, old, rule = result.mutations[f], self.payload.get(f), self.spec.mutable[f]
            new = thaw(freeze(new))
            if not _narrows(rule, old, new):
                self.record(reg, "rejected", ms, detail=f"{f}: change does not narrow ({rule})")
                self._flag("elevation", reg, f"{f} change on {self.spec.name} would widen the operation")
                return
            candidate[f] = new
        probe = self.event.to_document() | {"payload": candidate}
        if catalog.schema_errors("event", probe):
            self.record(reg, "rejected", ms, detail="mutated payload violates the event contract")
            self._flag("mutation", reg, "mutated payload violates the event contract")
            return
        self.payload = scrub_doc(candidate)
        self.record(reg, "mutated", ms, mutations=fields)

    def _flag(self, kind: str, reg: Registration, detail: str) -> None:
        self.pending_violations.append((kind, reg, detail))

    def finish(self) -> Emission:
        proceed = self.vetoed_by is None
        if self.spec.vetoable and (self.cancelled or self.recursion_limited):
            proceed = False  # safe cancellation: an interrupted guard never counts as approval
        final = HookEvent(
            name=self.event.name,
            version=self.event.version,
            event_id=self.event.event_id,
            sequence=self.event.sequence,
            emitted_at=self.event.emitted_at,
            depth=self.depth,
            context=self.event.context,
            payload=freeze(self.payload),
        )
        receipt = {
            "contract": "blux.hook-receipt",
            "hooks_version": catalog.HOOKS_VERSION,
            "receipt_id": f"hrc_{ids.ulid()}",
            "event_id": self.event.event_id,
            "name": self.spec.name,
            "sequence": self.event.sequence,
            "depth": self.depth,
            "context": self.event.context.to_dict(),
            "payload_sha256": self.initial_sha,
            "final_payload_sha256": sha256_of(self.payload),
            "handlers": self.handlers,
            "proceed": proceed,
            "vetoed_by": self.vetoed_by,
            "veto_reason": self.veto_reason,
            "cancelled": self.cancelled,
            "recursion_limited": self.recursion_limited,
            "emitted_at": self.event.emitted_at,
            "duration_ms": int((time.monotonic() - self.started) * 1000),
        }
        self.bus._deliver(receipt)
        for kind, reg, detail in self.pending_violations:
            self.bus._violation(kind, reg.hook_id, reg.source, self.spec.name, detail)
        return Emission(final, proceed, self.vetoed_by, self.veto_reason, self.cancelled, receipt)


# ------------------------------------------------------------------------------------------- helpers


def _narrows(rule: str, old: Any, new: Any) -> bool:
    if rule == "superset":
        return (
            isinstance(new, list)
            and isinstance(old, list)
            and set(old) <= set(new)
            and all(isinstance(x, str) for x in new)
        )
    if rule == "decrease":
        return isinstance(new, int) and not isinstance(new, bool) and isinstance(old, int) and 1 <= new <= old
    return False


def _handler_context(reg: Registration, name: str, depth: int) -> contextvars.Context:
    ctx = contextvars.copy_context()

    def setup() -> None:
        _DEPTH.set(depth + 1)
        _ACTIVE.set(_ACTIVE.get() | {(reg.hook_id, name)})
        _CURRENT.set(reg)

    ctx.run(setup)
    return ctx


def _apply_context(ctx: contextvars.Context) -> None:
    _DEPTH.set(ctx[_DEPTH])
    _ACTIVE.set(ctx[_ACTIVE])
    _CURRENT.set(ctx[_CURRENT])


async def _await(value: Awaitable[Any]) -> Any:
    return await value


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [thaw(v) for v in value]
    return value
