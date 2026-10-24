"""`blux` command line.

Machine protocol (ADR-0008): stdout carries exactly one JSON document per invocation, newline-terminated.
Diagnostics go to stderr. Exit codes: 0 ok, 1 contract-level failure (document still on stdout), 2 usage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, NoReturn, TextIO

import blux_runtime
import blux_sdk
from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import Outcome, Runtime
from blux_runtime.project import register_project
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import dot_blux, envelopes, ids
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.schemas import KINDS, check_envelope
from blux_sdk.serialization import dumps_json, loads_json, loads_yaml

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"blux: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _read_document(source: str, stdin: TextIO) -> dict[str, Any]:
    if source == "-":
        return loads_json(stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read())
    path = Path(source)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise BluxError(
            ErrorCode.MALFORMED_INPUT, "Cannot read input file.", details={"reason": "unreadable", "errno": exc.errno}
        ) from None
    return loads_yaml(data) if path.suffix in (".yaml", ".yml") else loads_json(data)


def _emit(doc: Any, out: TextIO, pretty: bool) -> None:
    out.write(dumps_json(doc, pretty=pretty) + "\n")
    out.flush()


def _registry(project_dir: str | None) -> tuple[CapabilityRegistry, dict[str, Any] | None]:
    """Built-ins, plus the project's `.blux/` capabilities and its policy when --project is given."""
    registry = builtin_registry()
    if project_dir is None:
        return registry, None
    project = dot_blux.load(project_dir)
    register_project(registry, project)
    return registry, project.policy


def _runtime(
    policy_path: str | None, stdin: TextIO, system_store: str | None = None, project_dir: str | None = None
) -> Runtime:
    registry, project_policy = _registry(project_dir)
    policy = _read_document(policy_path, stdin) if policy_path else project_policy or reference_policy()
    sink = BluxSystemSink(system_store) if system_store else None
    return Runtime(registry, policy, receipt_sink=sink)


def _finish(outcome: Outcome, args: argparse.Namespace, out: TextIO) -> int:
    _write_receipt(args.receipt_out, outcome.receipt)
    _emit(outcome.result, out, args.pretty)
    if outcome.sink_error is not None:
        # Fail closed: the result above is real, but it was not durably recorded.
        print(f"blux: receipt not recorded by BLUX System: {dumps_json(outcome.sink_error)}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK if outcome.result["status"] == "succeeded" else EXIT_FAILED


def _write_receipt(path: str | None, receipt: dict[str, Any]) -> None:
    if path:
        Path(path).write_text(dumps_json(receipt, pretty=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="blux", description="BLUX Contract v1 command line. Writes one JSON document to stdout.")
    p.add_argument("--pretty", action="store_true", help="indent JSON output")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    sub.add_parser("version", help="SDK/runtime versions and supported contract versions")

    v = sub.add_parser("validate", help="validate a contract document")
    v.add_argument("kind", choices=sorted(KINDS))
    v.add_argument("file", help="path (.json/.yaml) or - for JSON on stdin")

    e = sub.add_parser("exec", help="execute a blux.execution request; prints blux.result")
    e.add_argument("file", help="path or - for stdin")
    e.add_argument("--policy", help="policy document (default: built-in reference policy)")
    e.add_argument("--receipt-out", help="also write the blux.receipt to this path")
    e.add_argument("--system-store", help="record the receipt in this BLUX System store (needs blux-system)")
    e.add_argument("--project", help="also load capabilities (and, without --policy, the policy) from DIR/.blux")

    r = sub.add_parser("run", help="build and execute a request for CAPABILITY; prints blux.result")
    r.add_argument("capability_id")
    r.add_argument("--input", default="{}", help="JSON object for execution.input")
    r.add_argument("--actor-type", default="user", choices=["user", "agent", "service"])
    r.add_argument("--actor-id", default="cli")
    r.add_argument("--session-id")
    r.add_argument("--project-id")
    r.add_argument("--parent-operation-id")
    r.add_argument("--policy")
    r.add_argument("--receipt-out")
    r.add_argument("--system-store", help="record the receipt in this BLUX System store (needs blux-system)")
    r.add_argument("--project", help="also load capabilities (and, without --policy, the policy) from DIR/.blux")

    c = sub.add_parser("capabilities", help="print the blux.capability-set (built-ins, plus --project)")
    c.add_argument("--project", help="also list capabilities from DIR/.blux")

    i = sub.add_parser("id", help="generate an identifier")
    i.add_argument("kind", choices=["operation", "receipt", "artifact", "session"])

    pr = sub.add_parser("project", help="validate a repository's .blux/ directory")
    pr.add_argument("dir", nargs="?", default=".")
    return p


def main(argv: list[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args, stdin, out)
    except BluxError as exc:
        _emit(exc.to_document(), out, args.pretty)
        return EXIT_FAILED


def _dispatch(args: argparse.Namespace, stdin: TextIO, out: TextIO) -> int:
    if args.command == "version":
        _emit(
            {
                "blux_sdk": blux_sdk.__version__,
                "blux_runtime": blux_runtime.__version__,
                "contract_version": blux_sdk.CONTRACT_VERSION,
                "supported_contract_versions": list(blux_sdk.SUPPORTED_CONTRACT_VERSIONS),
            },
            out,
            args.pretty,
        )
        return EXIT_OK

    if args.command == "validate":
        doc = check_envelope(_read_document(args.file, stdin), args.kind)
        _emit({"valid": True, "kind": args.kind, "contract_version": doc["contract_version"]}, out, args.pretty)
        return EXIT_OK

    if args.command == "exec":
        runtime = _runtime(args.policy, stdin, args.system_store, args.project)
        raw: str | bytes
        if args.file == "-":
            raw = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read()
        else:
            try:
                raw = Path(args.file).read_bytes()
            except OSError as exc:
                raise BluxError(
                    ErrorCode.MALFORMED_INPUT,
                    "Cannot read input file.",
                    details={"reason": "unreadable", "errno": exc.errno},
                ) from None
        return _finish(runtime.execute(raw), args, out)

    if args.command == "run":
        runtime = _runtime(args.policy, stdin, args.system_store, args.project)
        request = envelopes.execution_request(
            args.capability_id,
            loads_json(args.input),
            actor={"type": args.actor_type, "id": args.actor_id},
            session_id=args.session_id,
            project_id=args.project_id,
            parent_operation_id=args.parent_operation_id,
        )
        return _finish(runtime.execute(request), args, out)

    if args.command == "capabilities":
        _emit(_registry(args.project)[0].to_capability_set(), out, args.pretty)
        return EXIT_OK

    if args.command == "id":
        kind = ids.IdKind(f"{args.kind}_id")
        _emit({"kind": kind.value, "id": ids.new_id(kind)}, out, args.pretty)
        return EXIT_OK

    if args.command == "project":
        project = dot_blux.load(args.dir)
        _emit(
            {
                "valid": True,
                "project_id": project.project_id,
                "capabilities": [c.capability_id for c in project.capabilities()],
                "policy_id": project.policy["policy_id"],
            },
            out,
            args.pretty,
        )
        return EXIT_OK

    raise AssertionError(f"unhandled command {args.command}")  # pragma: no cover
