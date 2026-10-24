"""The CLI's machine protocol: exactly one JSON document on stdout, diagnostics on stderr, fixed exit codes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blux_sdk.schemas import check_envelope
from tests.conftest import FIXTURES, ROOT


def blux(*args: str, stdin: bytes | None = None, cwd: Path | None = None) -> tuple[int, Any, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "blux_cli", *args], input=stdin, capture_output=True, cwd=cwd or ROOT, timeout=60
    )
    out = proc.stdout.decode()
    doc = None
    if out:
        assert out.endswith("\n") and out.count("\n") == 1, f"stdout must be one JSON line: {out!r}"
        doc = json.loads(out)
    return proc.returncode, doc, proc.stderr.decode()


def test_run_echo() -> None:
    code, doc, err = blux("run", "echo.test", "--input", '{"message": "cli"}')
    assert code == 0, err
    check_envelope(doc, "result")
    assert doc["output"] == {"message": "cli", "length": 3, "uppercase": False}
    assert err == ""


def test_exec_from_stdin_and_receipt_file(tmp_path: Path) -> None:
    request = (FIXTURES / "valid" / "execution" / "echo.json").read_bytes()
    receipt_path = tmp_path / "receipt.json"
    code, doc, _ = blux("exec", "-", "--receipt-out", str(receipt_path), stdin=request)
    assert code == 0
    receipt = check_envelope(json.loads(receipt_path.read_text()), "receipt")
    assert receipt["receipt_id"] == doc["receipt_id"]
    assert receipt["session_id"] == "ses_01JAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"garbage", "MALFORMED_INPUT"),
        (b'{"contract": "blux.execution", "contract_version": "2.0"}', "UNSUPPORTED_CONTRACT_VERSION"),
        (b'{"contract": "blux.execution", "contract_version": "one"}', "MALFORMED_CONTRACT_VERSION"),
    ],
)
def test_exec_failures_are_documents_with_exit_1(payload: bytes, code: str) -> None:
    rc, doc, _ = blux("exec", "-", stdin=payload)
    assert rc == 1
    check_envelope(doc, "result")
    assert doc["status"] == "rejected"
    assert doc["error"]["code"] == code


def test_exec_policy_file_denies(tmp_path: Path) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "contract: blux.policy\ncontract_version: '1.0'\npolicy_id: none\ndefault_effect: deny\nrules: []\n"
    )
    rc, doc, _ = blux("run", "echo.test", "--input", '{"message": "x"}', "--policy", str(policy))
    assert rc == 1
    assert doc["error"]["code"] == "POLICY_DENIED"


def test_validate_ok_and_failure() -> None:
    rc, doc, _ = blux("validate", "capability", str(FIXTURES / "valid" / "capability" / "echo-test.json"))
    assert (rc, doc) == (0, {"valid": True, "kind": "capability", "contract_version": "1.0"})
    case = json.loads((FIXTURES / "invalid" / "execution" / "version-2-0.json").read_text())
    rc, doc, _ = blux("validate", "execution", "-", stdin=json.dumps(case["document"]).encode())
    assert rc == 1
    check_envelope(doc, "error")
    assert doc["code"] == "UNSUPPORTED_CONTRACT_VERSION"


def test_validate_missing_file_is_a_document() -> None:
    rc, doc, _ = blux("validate", "execution", "/nonexistent/request.json")
    assert rc == 1
    assert doc["code"] == "MALFORMED_INPUT"


def test_project_capabilities_version_id() -> None:
    rc, doc, _ = blux("project", ".")
    assert (rc, doc["project_id"], doc["capabilities"]) == (0, "blux", ["echo.test"])
    rc, doc, _ = blux("capabilities")
    assert rc == 0
    check_envelope(doc, "capability-set")
    rc, doc, _ = blux("version")
    assert doc["supported_contract_versions"] == ["1.0"]
    rc, doc, _ = blux("id", "session")
    assert doc["id"].startswith("ses_")


def test_usage_errors_exit_2_with_empty_stdout() -> None:
    proc = subprocess.run([sys.executable, "-m", "blux_cli", "bogus"], capture_output=True, cwd=ROOT)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert b"usage:" in proc.stderr


def test_pretty_is_still_one_document() -> None:
    proc = subprocess.run([sys.executable, "-m", "blux_cli", "--pretty", "version"], capture_output=True, cwd=ROOT)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["contract_version"] == "1.0"
