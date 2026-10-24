"""Sink failures never lose a result; the BLUX System sink spools only what a retry can fix."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import Runtime
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import envelopes


class _Exploding:
    def emit(self, receipt: dict[str, Any]) -> None:
        raise OSError("disk full")


def _request() -> dict[str, Any]:
    return envelopes.execution_request("echo.test", {"message": "x"}, actor={"type": "user", "id": "u"})


def test_failing_sink_keeps_the_result() -> None:
    outcome = Runtime(builtin_registry(), reference_policy(), receipt_sink=_Exploding()).execute(_request())
    assert outcome.result["status"] == "succeeded"
    assert outcome.sink_error == {"exception_type": "OSError"}


def _fake_system(tmp_path: Path, response: dict[str, Any], code: int) -> str:
    exe = tmp_path / "fake-blux-system"
    exe.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '{json.dumps(response)}'\nexit {code}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_refusal_is_not_spooled(tmp_path: Path) -> None:
    exe = _fake_system(tmp_path, {"code": "RECEIPT_CONFLICT", "message": "conflict"}, 1)
    sink = BluxSystemSink(tmp_path / "s", executable=exe)
    outcome = Runtime(builtin_registry(), reference_policy(), receipt_sink=sink).execute(_request())
    assert outcome.sink_error is not None
    assert outcome.sink_error["code"] == "RECEIPT_CONFLICT" and outcome.sink_error["spooled"] is False
    assert not sink.outbox.exists()


def test_protocol_violation_is_spooled(tmp_path: Path) -> None:
    exe = tmp_path / "noisy"
    exe.write_text("#!/bin/sh\necho hello\n")
    exe.chmod(0o755)
    sink = BluxSystemSink(tmp_path / "s", executable=str(exe))
    outcome = Runtime(builtin_registry(), reference_policy(), receipt_sink=sink).execute(_request())
    assert outcome.sink_error is not None and outcome.sink_error["code"] == "SINK_PROTOCOL"
    assert sink.outbox.read_text().count("\n") == 1


def test_flush_moves_refused_receipts_aside(tmp_path: Path) -> None:
    sink = BluxSystemSink(
        tmp_path / "s", executable=_fake_system(tmp_path, {"code": "INVALID_RECORD", "message": "bad"}, 1)
    )
    receipt = Runtime(builtin_registry(), reference_policy()).execute(_request()).receipt
    sink._spool(receipt)
    assert sink.flush() == {"sent": 0, "remaining": 0, "refused": 1}
    assert not sink.outbox.exists()
    refused = sink.outbox.with_suffix(".refused.jsonl").read_text().splitlines()
    assert json.loads(refused[0])["receipt_id"] == receipt["receipt_id"]
