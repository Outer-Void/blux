"""OV-52: no receipt is lost on the way to BLUX System, and required receipts fail closed.

Each test failed on 9cd53ef:
- F83: an operation with an artifact lost its receipt when the store was unavailable (the artifact upload raised
  first, so the receipt was neither sent nor spooled);
- F84: a receipt spooled by another process while a flush was running was deleted by the flush;
- F85: a refusal without its own code (SINK_REFUSED) was spooled and retried for ever, and a receipt refused at
  emit time was dropped instead of set aside.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import Runtime
from blux_runtime.system_sink import ARTIFACT_ENTRY, BluxSystemSink, SystemSinkError
from blux_sdk import envelopes
from tests.conftest import ROOT


def _request() -> dict[str, Any]:
    return envelopes.execution_request("echo.test", {"message": "x"}, actor={"type": "user", "id": "u"})


def _exe(tmp_path: Path, name: str, script: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return str(path)


def _accepting(tmp_path: Path, delay: float = 0.0, log: Path | None = None) -> str:
    record = f'cat >> "{log}"; echo >> "{log}"' if log else "cat >/dev/null"
    return _exe(tmp_path, "accept", f'sleep {delay}\n{record}\necho \'{{"status":"stored"}}\'\n')


def _runtime_with_artifact(sink: Any) -> Runtime:
    rt = Runtime(builtin_registry(), reference_policy(), receipt_sink=sink)

    def attach(trace: Any) -> list[tuple[dict[str, Any], bytes]]:
        data = b'{"model": "call"}'
        d = envelopes.artifact_from_bytes(
            operation_id=trace.operation_id, name="model-call.json", media_type="application/json", data=data
        )
        return [(d, data)]

    rt._artifacts = attach  # type: ignore[method-assign]
    return rt


# ---------------------------------------------------------------- F83
def test_an_unavailable_store_spools_the_receipt_and_its_artifact(tmp_path: Path) -> None:
    sink = BluxSystemSink(tmp_path / "s", executable=str(tmp_path / "no-such-blux-system"))
    outcome = _runtime_with_artifact(sink).execute(_request())
    assert outcome.result["status"] == "succeeded"
    err = outcome.sink_error
    assert err is not None and err["code"] == "SINK_UNAVAILABLE" and err["spooled"] is True
    assert err["missing_artifacts"] == outcome.receipt["artifact_ids"]
    entries = [json.loads(line) for line in sink.outbox.read_text().splitlines()]
    assert [e.get("contract") for e in entries] == [ARTIFACT_ENTRY, "blux.receipt"]
    assert entries[1] == outcome.receipt
    assert sink.outbox.stat().st_mode & 0o077 == 0  # the outbox holds unredacted documents: owner only
    assert not outcome.receipt_not_queued


def test_a_refused_artifact_does_not_stop_the_receipt(tmp_path: Path) -> None:
    exe = _exe(
        tmp_path,
        "picky",
        'case "$*" in *"--kind artifact"*) cat >/dev/null\n'
        '  echo \'{"code":"ARTIFACT_HASH_MISMATCH","message":"no"}\'; exit 1;;\n'
        '*) cat >/dev/null; echo \'{"status":"stored"}\';; esac\n',
    )
    sink = BluxSystemSink(tmp_path / "s", executable=exe)
    outcome = _runtime_with_artifact(sink).execute(_request())
    err = outcome.sink_error
    assert err is not None and err["receipt_recorded"] is True and err["spooled"] is False
    assert err["code"] == "ARTIFACT_HASH_MISMATCH" and err["missing_artifacts"] == outcome.receipt["artifact_ids"]
    assert outcome.receipt_not_queued
    (aside,) = sink.refused_path.read_text().splitlines()
    assert json.loads(aside)["contract"] == ARTIFACT_ENTRY  # set aside, not dropped


# ---------------------------------------------------------------- F85
def test_a_refusal_without_a_code_is_set_aside_not_retried(tmp_path: Path) -> None:
    exe = _exe(tmp_path, "refuse", 'cat >/dev/null\necho \'{"message":"no"}\'\nexit 1\n')
    sink = BluxSystemSink(tmp_path / "s", executable=exe)
    receipt = Runtime(builtin_registry(), reference_policy()).execute(_request()).receipt
    with pytest.raises(SystemSinkError) as exc:
        sink.emit(receipt)
    assert (exc.value.code, exc.value.spooled) == ("SINK_REFUSED", False)
    assert not sink.outbox.exists()
    assert json.loads(sink.refused_path.read_text()) == receipt
    assert sink.pending() == {"waiting": 0, "refused": 1}


def test_set_aside_receipts_can_be_re_sent_after_a_repair(tmp_path: Path) -> None:
    log = tmp_path / "received.jsonl"
    receipt = Runtime(builtin_registry(), reference_policy()).execute(_request()).receipt
    sink = BluxSystemSink(
        tmp_path / "s",
        executable=_exe(tmp_path, "corrupt", 'cat >/dev/null\necho \'{"code":"STORE_CORRUPT"}\'\nexit 1\n'),
    )
    with pytest.raises(SystemSinkError):
        sink.emit(receipt)
    sink.executable = _accepting(tmp_path, log=log)
    assert sink.flush() == {"sent": 0, "remaining": 0, "refused": 0}  # refused ones wait for an explicit retry
    assert sink.flush(include_refused=True) == {"sent": 1, "remaining": 0, "refused": 0}
    assert json.loads(log.read_text()) == receipt and sink.pending() == {"waiting": 0, "refused": 0}


# ---------------------------------------------------------------- F84
def test_a_receipt_spooled_during_a_flush_is_kept(tmp_path: Path) -> None:
    flusher = BluxSystemSink(tmp_path / "s", executable=_accepting(tmp_path, delay=0.5))
    flusher._spool({"n": 1})
    done: list[dict[str, Any]] = []
    t = threading.Thread(target=lambda: done.append(flusher.flush()))
    t.start()
    time.sleep(0.2)  # the flush is sending {"n": 1}
    BluxSystemSink(tmp_path / "s", executable="unused")._spool({"n": 2})  # another process spools meanwhile
    t.join()
    assert done == [{"sent": 1, "remaining": 0, "refused": 0}]
    assert [json.loads(x) for x in flusher.outbox.read_text().splitlines()] == [{"n": 2}]


def test_two_processes_spooling_and_flushing_lose_nothing(tmp_path: Path) -> None:
    log = tmp_path / "received.jsonl"
    exe = _accepting(tmp_path, delay=0.01, log=log)
    store = tmp_path / "s"
    code = (
        "import sys; sys.path[:0] = sys.argv[3:]\n"
        "from blux_runtime.system_sink import BluxSystemSink\n"
        "s = BluxSystemSink(sys.argv[1], executable='unused')\n"
        "for i in range(int(sys.argv[2])): s._spool({'n': i})\n"
    )
    paths = [str(ROOT / "runtime"), str(ROOT / "sdk" / "python" / "src")]
    child = subprocess.Popen([sys.executable, "-c", code, str(store), "60", *paths])
    sink = BluxSystemSink(store, executable=exe)
    while child.poll() is None:
        sink.flush()
    assert child.wait() == 0
    sink.flush()
    received = sorted(json.loads(line)["n"] for line in log.read_text().splitlines() if line.strip())
    assert received == list(range(60)) and sink.pending() == {"waiting": 0, "refused": 0}


def test_a_flush_killed_part_way_loses_nothing(tmp_path: Path) -> None:
    log = tmp_path / "received.jsonl"
    sink = BluxSystemSink(tmp_path / "s", executable=_accepting(tmp_path, log=log))
    for n in range(3):
        sink._spool({"n": n})
    real, sent = sink._send, []

    def killed(doc: dict[str, Any], extra: tuple[str, ...] = ()) -> dict[str, Any]:
        if sent:
            raise SystemExit("killed")
        sent.append(doc)
        return real(doc, extra)

    sink._send = killed  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        sink.flush()
    sink._send = real  # type: ignore[method-assign]
    sink._spool({"n": 3})
    assert sink.pending()["waiting"] == 4  # the interrupted flush's three, and n=3
    assert sink.flush() == {"sent": 4, "remaining": 0, "refused": 0}
    received = sorted(json.loads(line)["n"] for line in log.read_text().splitlines() if line.strip())
    # n=0 is sent twice; BLUX System answers an identical re-send as a duplicate, so nothing is doubled there.
    assert received == [0, 0, 1, 2, 3]


def test_a_second_flush_at_the_same_time_steps_aside(tmp_path: Path) -> None:
    sink = BluxSystemSink(tmp_path / "s", executable=_accepting(tmp_path))
    with sink._locked(".flush-lock"):
        assert BluxSystemSink(tmp_path / "s").flush()["busy"] is True
