"""Direct intelligence: `blux "..."`, `blux ask`, `blux chat`, `blux doctor`, `blux config` (ADR-0011).

`blux` talks to a logical BLUX model through the Model API and the capability runtime, so every call
is policy-checked, validated and receipted. It never manages projects, devices or the ecosystem: that
is `ax`.

Human mode (default): the answer on stdout (streamed when stdout is a terminal), diagnostics on
stderr. Machine mode (--json): `ask` writes exactly one blux.result document; `chat` writes one per
turn (JSON Lines); `doctor` and `config` write one document.

Exit codes: 0 ok · 1 the model call failed · 2 usage or unusable input · 3 configuration (routes,
config file, backend or credentials) · 4 timed out · 5 answered but the receipt was not recorded ·
130 cancelled.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from blux_cli import config as cfg
from blux_runtime.builtin import builtin_registry
from blux_runtime.executor import Outcome, Runtime
from blux_runtime.models import ModelRouter
from blux_runtime.models import capability as model_capability
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import envelopes, ids, model_api
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.model_api import ModelApiError
from blux_sdk.serialization import dumps_json, loads_json, loads_yaml

OK, FAILED, USAGE, CONFIG, TIMED_OUT, NOT_RECORDED, CANCELLED = 0, 1, 2, 3, 4, 5, 130
COMMANDS = ("ask", "chat", "doctor", "config")
CONFIG_CODES = {"PROVIDER_NOT_CONFIGURED", "PROVIDER_AUTH_FAILED", "CAPABILITY_UNAVAILABLE", "UNKNOWN_MODEL"}
TIMEOUT_CODES = {"TIMEOUT", "PROVIDER_TIMEOUT"}
HINTS = {
    "PROVIDER_NOT_CONFIGURED": "the backend or the provider key is missing; run `blux doctor`",
    "PROVIDER_AUTH_FAILED": "the provider rejected the credentials; check the backend's (`bluxgpt status`)",
    "PROVIDER_QUOTA_EXCEEDED": "the provider account has no credit or quota left",
    "PROVIDER_RATE_LIMITED": "rate limited by the provider; try again shortly",
    "PROVIDER_UNAVAILABLE": "the provider is unavailable; try again shortly",
    "TIMEOUT": "no route answered in time; raise --timeout (the routing table sets the upper bound)",
    "PROVIDER_TIMEOUT": "no route answered in time; raise --timeout (the routing table sets the upper bound)",
    "CONTEXT_TOO_LONG": "the input is too long for this model; shorten it (in chat: /reset)",
    "CONTENT_FILTERED": "the provider declined the request; BLUX does not route around a refusal",
    "CAPABILITY_UNAVAILABLE": "no route for this model offers what was asked; see `blux models`",
    "STRUCTURED_OUTPUT_INVALID": "the model's JSON did not match the schema",
}
PROBE_PROMPT = "Reply with the single word: ok"
SECRETS = re.compile("|".join(f"(?:{p})" for p in model_api.SECRET_PATTERNS))


class Abort(Exception):
    """Stop before any model call: a usage or configuration problem."""

    def __init__(self, code: int, message: str, reason: str) -> None:
        super().__init__(message)
        self.code, self.message, self.reason = code, message, reason


# ------------------------------------------------------------------------------------------- parsers


def add_parsers(sub: Any, parser_class: type[argparse.ArgumentParser]) -> None:
    a = sub.add_parser(
        "ask",
        help='ask a logical model (the default: `blux "..."`); stdin is added as input',
        description='Ask a logical BLUX model. `blux "..."` is shorthand; piped stdin becomes the input.',
    )
    a.add_argument("prompt", nargs="*", help="the request (words are joined); `--` ends options")
    _model_options(a)
    a.add_argument("--schema", help="JSON Schema file (.json/.yaml): ask for structured output matching it")
    a.add_argument("--no-stdin", action="store_true", help="ignore stdin even when it is piped")
    a.add_argument("--receipt-out", help="also write the blux.receipt to this path")
    a.add_argument("--parent-operation-id", help="link this call under an earlier operation")
    a.add_argument("--session-id", help="link this call into an existing session")

    c = sub.add_parser("chat", help="interactive chat with a logical model (stdin lines when piped)")
    _model_options(c)

    d = sub.add_parser("doctor", help="diagnose config, routes, backends, credentials; --probe calls each route")
    d.add_argument("-m", "--model", choices=list(model_api.LOGICAL_MODELS), help="only this logical model")
    d.add_argument("--routes", help="blux.model-routes table")
    d.add_argument("--probe", action="store_true", help="send a tiny prompt through every route (uses credit)")
    d.add_argument("--json", action="store_true", help="one JSON document on stdout")

    g = sub.add_parser("config", help="show the effective settings and where each came from")
    g.add_argument("--json", action="store_true", help="one JSON document on stdout")


def _model_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("-m", "--model", choices=list(model_api.LOGICAL_MODELS), help="logical model (default blux-main)")
    p.add_argument("-s", "--system", help="system instructions")
    p.add_argument("--json", action="store_true", help="machine mode: blux.result documents on stdout")
    p.add_argument("--stream", dest="stream", action="store_true", default=None, help="stream even to a pipe")
    p.add_argument("--no-stream", dest="stream", action="store_false", help="print the answer when complete")
    p.add_argument("--timeout", type=float, metavar="SECONDS", help="give up after this long")
    p.add_argument("--max-output-tokens", type=int)
    p.add_argument("--deterministic", action="store_true", help="temperature 0 / fixed seed where the route allows")
    p.add_argument("--no-fallback", action="store_true", help="fail on the first route's error")
    p.add_argument("--no-redact", action="store_true", help="send credential-shaped strings in the input as-is")
    p.add_argument("--routes", help="blux.model-routes table (default: config, $BLUX_MODEL_ROUTES, packaged)")
    p.add_argument("--system-store", help="record receipts in this BLUX System store (needs blux-system)")
    p.add_argument("-q", "--quiet", action="store_true", help="no footer")
    p.add_argument("-v", "--verbose", action="store_true", help="footer with routing details")


# ------------------------------------------------------------------------------------------- entry


def run(args: argparse.Namespace, stdin: TextIO, out: TextIO, err: TextIO) -> int:
    machine = getattr(args, "json", False)
    try:
        settings = cfg.load()
        _apply_flags(settings, args)
        if args.command == "config":
            return _config(settings, args, out)
        if args.command == "doctor":
            return _doctor(settings, args, out, err)
        client = Client(settings, args, err)
        if args.command == "ask":
            return _ask(client, args, stdin, out, err)
        return _chat(client, args, stdin, out, err)
    except cfg.ConfigError as exc:
        return _abort(Abort(CONFIG, str(exc), "config"), machine, args, out, err)
    except Abort as exc:
        return _abort(exc, machine, args, out, err)


def _apply_flags(settings: cfg.Settings, args: argparse.Namespace) -> None:
    cfg.override(settings, "model", getattr(args, "model", None), "--model")
    cfg.override(settings, "routes", getattr(args, "routes", None), "--routes")
    cfg.override(settings, "system_store", getattr(args, "system_store", None), "--system-store")
    cfg.override(settings, "timeout_seconds", getattr(args, "timeout", None), "--timeout")
    cfg.override(settings, "stream", getattr(args, "stream", None), "--stream/--no-stream")
    if getattr(args, "no_redact", False):
        cfg.override(settings, "redact_input", False, "--no-redact")


def _abort(exc: Abort, machine: bool, args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    if machine:
        doc = BluxError(ErrorCode.MALFORMED_INPUT, exc.message, details={"reason": exc.reason, "exit_code": exc.code})
        out.write(dumps_json(doc.to_document(), pretty=getattr(args, "pretty", False)) + "\n")
        out.flush()
    else:
        print(f"blux: {exc.message}", file=err)
    return exc.code


# ------------------------------------------------------------------------------------------- client


@dataclass
class Turn:
    outcome: Outcome
    code: int
    streamed: bool


class Client:
    """One runtime for the whole invocation; every call shares a session and links to the previous one."""

    def __init__(self, settings: cfg.Settings, args: argparse.Namespace, err: TextIO) -> None:
        self.settings = settings
        self.err = err
        self.router = make_router(load_routes(settings))
        registry = builtin_registry()
        model_capability.register(registry, self.router)
        store = settings.system_store
        if store and not _system_available():
            raise Abort(
                CONFIG,
                f"system_store is set ({settings.sources['system_store']}) but blux-system is not installed",
                "system_store",
            )
        sink = BluxSystemSink(store) if store else None
        self.runtime = Runtime(registry, model_capability.model_policy(), receipt_sink=sink)
        self.session_id = getattr(args, "session_id", None) or ids.new_session_id()
        self.parent: str | None = getattr(args, "parent_operation_id", None)
        self.args = args

    def streams(self, model: str) -> bool:
        return any("streaming" in r["capabilities"] for r in self.router.routes["models"][model]["routes"])

    def request(self, model: str, messages: list[dict[str, Any]], *, stream: bool, **extra: Any) -> dict[str, Any]:
        a = self.args
        fields: dict[str, Any] = {
            "system": a.system or None,
            "stream": True if stream else None,
            "deterministic": True if a.deterministic else None,
            "client": {"name": "blux-cli"},
            **extra,
        }
        if self.settings.timeout_seconds:
            fields["timeout_ms"] = max(1, int(self.settings.timeout_seconds * 1000))
        if a.max_output_tokens:
            fields["generation"] = {"max_output_tokens": a.max_output_tokens}
        if a.no_fallback:
            fields["routing"] = {"allow_fallback": False}
        return model_api.request(model, messages=messages, **fields)

    def call(self, req: dict[str, Any], on_event: Callable[[dict[str, Any]], None]) -> Outcome:
        execution = envelopes.execution_request(
            "model.generate",
            req,
            actor={"type": "user", "id": "blux-cli"},
            session_id=self.session_id,
            parent_operation_id=self.parent,
        )
        with _cancellable() as cancel:
            outcome = self.runtime.execute(execution, on_event=on_event, cancel=cancel)
        self.parent = outcome.result["operation_id"]
        return outcome


def make_router(routes: dict[str, Any]) -> ModelRouter:
    """The router for a validated routing table (tests replace this to use in-process backends)."""
    return ModelRouter(routes)


def load_routes(settings: cfg.Settings) -> dict[str, Any]:
    source = settings.routes
    if not source:
        return model_capability.default_routes()
    try:
        data = Path(source).read_bytes()
        doc = loads_yaml(data) if source.endswith((".yaml", ".yml")) else loads_json(data)
        ModelRouter(doc)
    except OSError as exc:
        raise Abort(
            CONFIG, f"routes table {source} ({settings.sources['routes']}) is not readable ({exc.strerror})", "routes"
        ) from None
    except (BluxError, ModelApiError) as exc:
        raise Abort(
            CONFIG, f"routes table {source} ({settings.sources['routes']}) is invalid: {exc}", "routes"
        ) from None
    return doc


def _system_available() -> bool:
    local = Path(sys.executable).with_name("blux-system")
    return local.exists() or shutil.which("blux-system") is not None


@contextmanager
def _cancellable() -> Iterator[threading.Event]:
    """Ctrl-C cancels the call in flight (the backend is stopped); a second Ctrl-C interrupts at once."""
    cancel = threading.Event()

    def handler(signum: int, frame: Any) -> None:
        if cancel.is_set():
            raise KeyboardInterrupt
        cancel.set()

    previous = signal.signal(signal.SIGINT, handler) if threading.current_thread() is threading.main_thread() else None
    try:
        yield cancel
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def classify(outcome: Outcome) -> int:
    result = outcome.result
    if result["status"] == "succeeded":
        return NOT_RECORDED if outcome.sink_error is not None else OK
    error = result.get("error") or {}
    code = (error.get("details") or {}).get("provider_code")
    attempts = ((error.get("details") or {}).get("model_error") or {}).get("attempts") or []
    failed = {a.get("error_code") for a in attempts if a.get("outcome") == "failed"}
    if code == "CANCELLED":
        return CANCELLED
    if code in TIMEOUT_CODES or (code == "ALL_ROUTES_FAILED" and failed and failed <= TIMEOUT_CODES):
        return TIMED_OUT
    if code in CONFIG_CODES or (code == "ALL_ROUTES_FAILED" and failed and failed <= CONFIG_CODES):
        return CONFIG
    return FAILED


# ------------------------------------------------------------------------------------------- one turn


class Renderer:
    """Writes one turn for a human: streamed or whole answer on stdout, everything else on stderr."""

    def __init__(self, client: Client, out: TextIO, err: TextIO, *, stream: bool) -> None:
        self.client, self.out, self.err, self.stream = client, out, err, stream
        self.wrote = False
        self.ended_with_newline = True
        self.attempt = 0

    def on_event(self, event: dict[str, Any]) -> None:
        kind, data = event["type"], event["data"]
        if kind == "route":
            if data["attempt"] > 1 and self.wrote:
                self._newline()
                print(f"[blux: the answer above is incomplete; continuing on {data['route_id']}]", file=self.err)
            self.attempt = data["attempt"]
        elif kind == "delta" and self.stream and data.get("text"):
            self.out.write(data["text"])
            self.out.flush()
            self.wrote = True
            self.ended_with_newline = data["text"].endswith("\n")

    def _newline(self) -> None:
        if self.wrote and not self.ended_with_newline:
            self.out.write("\n")
            self.out.flush()
            self.ended_with_newline = True

    def finish(self, turn: Turn) -> None:
        result = turn.outcome.result
        if result["status"] == "succeeded":
            output = result["output"]["output"]
            if output["json"] is not None:
                self.out.write(dumps_json(output["json"], pretty=True) + "\n")
            elif not self.wrote:
                if output["text"]:
                    self.out.write(output["text"].rstrip("\n") + "\n")
                else:
                    print("blux: the model returned no text", file=self.err)
            self._newline()
            self.out.flush()
            wanted = (
                self.client.settings.footer == "always"
                or self.client.args.verbose
                or (self.client.settings.footer == "auto" and _isatty(self.err))
            )
            if wanted and not self.client.args.quiet:
                print(footer(turn.outcome, verbose=self.client.args.verbose), file=self.err)
            if turn.outcome.sink_error is not None:
                print("blux: answered, but the receipt was NOT recorded by BLUX System", file=self.err)
            return
        self._newline()
        describe_failure(turn.outcome, self.err, cancelled=turn.code == CANCELLED)


def footer(outcome: Outcome, *, verbose: bool = False) -> str:
    resp = outcome.result["output"]
    usage = resp.get("usage") or {}
    parts = [
        resp["model"],
        f"{resp['provider']['provider']}/{resp['provider']['provider_model']}",
        f"{resp['timing']['latency_ms'] / 1000:.1f}s",
    ]
    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        parts.append(f"{usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out tokens")
    if resp["routing"].get("fallback_used") or resp["routing"].get("degraded"):
        parts.append("fallback route")
    parts.append(f"operation {outcome.result['operation_id']}")
    lines = ["-- " + " · ".join(parts)]
    if verbose:
        lines.append(f"   receipt {outcome.receipt['receipt_id']} · session {outcome.receipt.get('session_id')}")
        for a in resp["routing"]["attempts"]:
            lines.append(
                f"   {a['route_id']}: {a['outcome']}" + (f" ({a['error_code']})" if a.get("error_code") else "")
            )
        lines += [f"   note: {n}" for n in resp["routing"].get("notes") or []]
    return "\n".join(lines)


def describe_failure(outcome: Outcome, err: TextIO, *, cancelled: bool = False) -> None:
    result = outcome.result
    error = result.get("error") or {}
    details = error.get("details") or {}
    code = details.get("provider_code") or error.get("code")
    model_error = details.get("model_error") or {}
    if cancelled:
        print("blux: cancelled", file=err)
    else:
        print(f"blux: {error.get('message', 'the call failed')} [{code}]", file=err)
    attempts = model_error.get("attempts") or []
    for a in attempts:
        if a.get("outcome") == "failed":
            reason = f": {a['reason']}" if a.get("reason") else ""
            print(f"  - {a['route_id']} ({a['provider']}/{a['provider_model']}) {a['error_code']}{reason}", file=err)
    codes = [code] + [a.get("error_code") for a in attempts if a.get("outcome") == "failed"]
    seen: list[str] = []
    for c in codes:
        if c in HINTS and HINTS[c] not in seen and not cancelled:
            seen.append(HINTS[c])
    for hint in seen[:3]:
        print(f"  hint: {hint}", file=err)
    print(f"  operation {result['operation_id']} · receipt {outcome.receipt['receipt_id']}", file=err)
    if outcome.sink_error is not None:
        print("blux: the receipt was NOT recorded by BLUX System", file=err)


def _run_turn(client: Client, req: dict[str, Any], renderer: Renderer | None) -> Turn:
    outcome = client.call(req, renderer.on_event if renderer else lambda _e: None)
    turn = Turn(outcome, classify(outcome), streamed=bool(renderer and renderer.wrote))
    if renderer:
        renderer.finish(turn)
    return turn


# ------------------------------------------------------------------------------------------- ask


def _ask(client: Client, args: argparse.Namespace, stdin: TextIO, out: TextIO, err: TextIO) -> int:
    settings = client.settings
    prompt = " ".join(args.prompt).strip()
    piped = None if args.no_stdin or _isatty(stdin) else read_input(stdin, settings.max_input_bytes)
    if not prompt and not piped:
        raise Abort(USAGE, 'nothing to ask: give a prompt (blux "...") or pipe input', "no_prompt")
    content = compose(prompt, piped)
    if settings.redact_input:
        content, n = SECRETS.subn("[redacted]", content)
        if n:
            print(f"blux: redacted {n} credential-shaped string(s) from the input (--no-redact keeps them)", file=err)
    extra: dict[str, Any] = {}
    if args.schema:
        extra["response_format"] = {"type": "json_schema", "name": "output", "schema": _read_schema(args.schema)}
    stream = (
        not args.json
        and not args.schema
        and settings.stream
        and client.streams(settings.model)
        and (args.stream is True or _isatty(out))
    )
    req = client.request(settings.model, [{"role": "user", "content": content}], stream=stream, **extra)
    turn = _run_turn(client, req, None if args.json else Renderer(client, out, err, stream=stream))
    if args.receipt_out:
        Path(args.receipt_out).write_text(dumps_json(turn.outcome.receipt, pretty=True) + "\n", encoding="utf-8")
    if args.json:
        out.write(dumps_json(turn.outcome.result, pretty=getattr(args, "pretty", False)) + "\n")
        out.flush()
        if turn.outcome.sink_error is not None:
            print(f"blux: receipt not recorded by BLUX System: {dumps_json(turn.outcome.sink_error)}", file=err)
    return turn.code


def read_input(stdin: TextIO, limit: int) -> str | None:
    stream = getattr(stdin, "buffer", None)
    raw: bytes | str = stream.read(limit + 1) if stream is not None else stdin.read(limit + 1)
    data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    if len(data) > limit:
        raise Abort(
            USAGE, f"stdin is larger than {limit} bytes; trim it (for example `tail -n 200`)", "input_too_large"
        )
    if b"\x00" in data:
        raise Abort(USAGE, "stdin looks binary (NUL bytes); pipe text", "binary_input")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Abort(USAGE, "stdin is not UTF-8 text", "not_utf8") from None
    return text if text.strip() else None


def compose(prompt: str, piped: str | None) -> str:
    if piped is None:
        return prompt
    if not prompt:
        return piped
    return f'{prompt}\n\n<input source="stdin">\n{piped.rstrip()}\n</input>'


def _read_schema(path: str) -> dict[str, Any]:
    try:
        data = Path(path).read_bytes()
        doc = loads_yaml(data) if path.endswith((".yaml", ".yml")) else loads_json(data)
    except OSError as exc:
        raise Abort(USAGE, f"schema {path} is not readable ({exc.strerror})", "schema") from None
    except BluxError as exc:
        raise Abort(USAGE, f"schema {path} is not valid JSON/YAML: {exc.message}", "schema") from None
    if not isinstance(doc, dict):
        raise Abort(USAGE, f"schema {path} must be a JSON object", "schema")
    return doc


# ------------------------------------------------------------------------------------------- chat

CHAT_HELP = """commands:
  /model NAME    switch logical model (blux-main, blux-fast, blux-reason, blux-code)
  /system TEXT   set system instructions (/system alone clears them)
  /reset         forget the conversation
  /last          routing details of the last answer
  /exit          leave (also Ctrl-D); Ctrl-C cancels an answer in progress"""


def _chat(client: Client, args: argparse.Namespace, stdin: TextIO, out: TextIO, err: TextIO) -> int:
    interactive = _isatty(stdin) and not args.json
    settings = client.settings
    model = settings.model
    history: list[dict[str, Any]] = []
    last: Outcome | None = None
    worst = OK
    if interactive:
        with contextlib.suppress(ImportError):
            import readline  # noqa: F401  (line editing for input())
        print(f"blux chat · {model} · session {client.session_id} · /help for commands", file=err)
    while True:
        line = _read_line(stdin, interactive)
        if line is None:
            break
        text = line.strip()
        if not text:
            continue
        if text.startswith("/"):
            name, _, rest = text.partition(" ")
            if name in ("/exit", "/quit"):
                break
            if name == "/help":
                print(CHAT_HELP, file=err)
            elif name == "/reset":
                history.clear()
                print("blux: conversation cleared", file=err)
            elif name == "/model":
                if rest.strip() in model_api.LOGICAL_MODELS:
                    model = rest.strip()
                    print(f"blux: model {model}", file=err)
                else:
                    print(f"blux: unknown model; use one of {', '.join(model_api.LOGICAL_MODELS)}", file=err)
            elif name == "/system":
                args.system = rest.strip() or None
                print("blux: system instructions " + ("set" if args.system else "cleared"), file=err)
            elif name == "/last":
                if last is None:
                    print("blux: no answer yet", file=err)
                elif last.result["status"] == "succeeded":
                    print(footer(last, verbose=True), file=err)
                else:
                    describe_failure(last, err)
            else:
                print(f"blux: unknown command {name}; /help lists them", file=err)
            continue
        content = text
        if settings.redact_input:
            content, n = SECRETS.subn("[redacted]", content)
            if n:
                print(f"blux: redacted {n} credential-shaped string(s)", file=err)
        messages = [*history, {"role": "user", "content": content}]
        stream = not args.json and settings.stream and client.streams(model) and (args.stream is True or _isatty(out))
        try:
            turn = _run_turn(
                client,
                client.request(model, messages, stream=stream),
                None if args.json else Renderer(client, out, err, stream=stream),
            )
        except KeyboardInterrupt:
            print("\nblux: interrupted", file=err)
            return CANCELLED
        last = turn.outcome
        if args.json:
            out.write(dumps_json(turn.outcome.result) + "\n")
            out.flush()
        if turn.code in (OK, NOT_RECORDED):
            reply = turn.outcome.result["output"]["output"]
            answer = (
                reply["text"] if reply["text"] is not None else dumps_json(reply["json"]) if reply["json"] else None
            )
            if answer:
                history = [*messages, {"role": "assistant", "content": answer}]
        if turn.code != OK and worst == OK:
            worst = turn.code
        if turn.code == CANCELLED and not interactive:
            return CANCELLED
    return OK if interactive else worst


def _read_line(stdin: TextIO, interactive: bool) -> str | None:
    if not interactive:
        line = stdin.readline()
        return line if line else None
    while True:
        try:
            return input("you> ")
        except EOFError:
            print(file=sys.stderr)
            return None
        except KeyboardInterrupt:
            print("\n(/exit or Ctrl-D to leave)", file=sys.stderr)


# ------------------------------------------------------------------------------------------- doctor


def _doctor(settings: cfg.Settings, args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    checks: list[dict[str, Any]] = []

    def check(name: str, status: str, detail: str, **extra: Any) -> None:
        checks.append({"check": name, "status": status, "detail": detail, **extra})

    check("config", "ok", f"{settings.path} ({'found' if settings.found else 'not present; defaults'})")
    release = Path(__file__).resolve().parents[3] / "share" / "blux" / "release.json"
    if release.is_file():  # installed from the BLUX Termux package
        rel = json.loads(release.read_text(encoding="utf-8"))
        check("package", "ok", f"{rel['package']} {rel['version']} (updates: {rel['update']['owner']}, no self-update)")
    try:
        routes = load_routes(settings)
        check("routes", "ok", f"{routes['routes_id']} ({settings.routes or 'packaged default'})")
    except Abort as exc:
        check("routes", "fail", exc.message)
        return _doctor_report(checks, [], args, out, CONFIG)
    backends = {}
    for name, b in routes["backends"].items():
        exe = shutil.which(b["entrypoint"].split()[0])  # what the backend launcher resolves
        backends[name] = exe
        check(f"backend {name}", "ok" if exe else "fail", exe or f"{b['entrypoint'].split()[0]} not found on PATH")
    if settings.system_store:
        check("system_store", "ok" if _system_available() else "fail", settings.system_store)
    models: list[dict[str, Any]] = []
    code = OK
    for model in [args.model] if args.model else list(model_api.LOGICAL_MODELS):
        rows = []
        for r in routes["models"][model]["routes"]:
            creds = {
                n: ("set" if os.environ.get(n) else "not in BLUX's environment") for n in r.get("credential_env") or []
            }
            rows.append(
                {
                    "route_id": r["route_id"],
                    "provider": r["provider"],
                    "provider_model": r["provider_model"],
                    "tier": r["tier"],
                    "backend_installed": backends.get(r["backend"]) is not None,
                    "credentials": creds,
                }
            )
        usable = any(row["backend_installed"] for row in rows)
        models.append({"model": model, "usable": usable, "routes": rows})
        if not usable:
            code = CONFIG
    if args.probe and code == OK:
        print("blux doctor: probing every route (this sends a tiny real request to each provider)", file=err)
        probe_args = argparse.Namespace(
            system=None,
            deterministic=False,
            max_output_tokens=400,
            no_fallback=True,
            session_id=None,
            parent_operation_id=None,
            verbose=False,
            quiet=True,
        )
        client = Client(settings, probe_args, err)
        for m in models:
            ok_any = False
            for row in m["routes"]:
                req = client.request(m["model"], [{"role": "user", "content": PROBE_PROMPT}], stream=False)
                req["routing"] = {"allow_fallback": False, "providers": [row["provider"]]}
                req.setdefault("timeout_ms", 60000)
                outcome = client.call(req, lambda _e: None)
                result = outcome.result
                if result["status"] == "succeeded":
                    row["probe"] = {"status": "ok", "latency_ms": result["output"]["timing"]["latency_ms"]}
                    ok_any = True
                else:
                    pc = (result["error"].get("details") or {}).get("provider_code")
                    row["probe"] = {"status": "failed", "error_code": pc, "operation_id": result["operation_id"]}
            m["probe_ok"] = ok_any
            if not ok_any and code == OK:
                code = FAILED
    return _doctor_report(checks, models, args, out, code)


def _doctor_report(
    checks: list[dict[str, Any]], models: list[dict[str, Any]], args: argparse.Namespace, out: TextIO, code: int
) -> int:
    if args.json:
        doc = {
            "contract": "blux.cli-doctor",
            "status": "ok" if code == OK else "problems",
            "exit_code": code,
            "checks": checks,
            "models": models,
            "credentials_note": "values are never read or shown; the backend may supply keys from its own config",
        }
        out.write(dumps_json(doc, pretty=getattr(args, "pretty", False)) + "\n")
        out.flush()
        return code
    mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    for c in checks:
        out.write(f"[{mark[c['status']]}] {c['check']}: {c['detail']}\n")
    for m in models:
        out.write(f"\n{m['model']}: {'usable' if m['usable'] else 'NO INSTALLED BACKEND'}\n")
        for r in m["routes"]:
            creds = ", ".join(f"{k} {v}" for k, v in r["credentials"].items()) or "no credentials needed"
            probe = r.get("probe")
            ptxt = (
                ""
                if not probe
                else (
                    f" · probe ok {probe['latency_ms']} ms"
                    if probe["status"] == "ok"
                    else f" · probe FAILED {probe['error_code']}"
                )
            )
            out.write(
                f"  {r['route_id']:<18} {r['provider']}/{r['provider_model']} ({r['tier']})"
                f"{'' if r['backend_installed'] else ' · backend missing'} · {creds}{ptxt}\n"
            )
    out.write(
        "\ncredentials: presence only, never values. A key missing from BLUX's environment may still be"
        " configured in the backend (BluxGPT: `bluxgpt status`); `blux doctor --probe` checks for real.\n"
    )
    out.flush()
    return code


# ------------------------------------------------------------------------------------------- config


def _config(settings: cfg.Settings, args: argparse.Namespace, out: TextIO) -> int:
    doc = settings.to_document()
    if args.json:
        out.write(dumps_json(doc, pretty=getattr(args, "pretty", False)) + "\n")
    else:
        out.write(f"config file  {doc['config_file']} ({'found' if doc['config_found'] else 'not present'})\n")
        for key, v in doc["settings"].items():
            out.write(f"  {key:<16} {v['value']!s:<28} {v['source']}\n")
        out.write(f"credentials  {doc['credentials']}\n")
    out.flush()
    return OK


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False
