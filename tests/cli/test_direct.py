"""Direct intelligence CLI (ADR-0011): `blux "..."`, ask, chat, doctor, config.

In-process and MOCKED: scripted backends (CallableBackend) behind the real router and runtime.
tests/integration/test_cli_direct.py drives the installed `blux` against the real BluxGPT process.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from blux_cli import direct
from blux_cli.main import main
from blux_runtime.models import CallableBackend, ModelRouter
from blux_sdk import model_api
from blux_sdk.model_api import ModelApiError
from blux_sdk.schemas import check_envelope
from tests.models.test_model_api import Script, reply, route, table

FAKE_KEY = "sk-proj-DIRECTCLITESTDIRECTCLI0000"


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BLUX_CONFIG", str(tmp_path / "config.toml"))
    for name in ("BLUX_MODEL", "BLUX_MODEL_ROUTES", "BLUX_SYSTEM_STORE", "BLUX_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Install a routing table served by a scripted in-process backend; returns (script, routes path)."""

    def install(script: Script, *routes: dict[str, Any], model: str = "blux-main", **defaults: Any) -> Path:
        path = tmp_path / "routes.json"
        path.write_text(json.dumps(table(*routes, model=model, **defaults)), encoding="utf-8")
        monkeypatch.setattr(direct, "make_router", lambda doc: ModelRouter(doc, {"test": CallableBackend(script)}))
        monkeypatch.setenv("BLUX_MODEL_ROUTES", str(path))
        return path

    return install


def cli(*argv: str, stdin: io.StringIO | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        code = main(list(argv), stdin=stdin if stdin is not None else TTY(), stdout=out, stderr=err)
    except SystemExit as exc:  # argparse usage errors
        code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def user_text(request: dict[str, Any]) -> str:
    return str(request["messages"][-1]["content"])


# argv routing ---------------------------------------------------------------------------------------


def test_a_bare_prompt_asks_the_default_logical_model(backend: Any) -> None:
    script = Script()
    backend(script, route("main.a"))
    code, out, err = cli("explain", "this", "error")
    assert (code, out) == (0, "ok\n"), err
    rid, request = script.seen[0]
    assert rid == "main.a" and request["model"] == "blux-main" and user_text(request) == "explain this error"


def test_subcommands_keep_the_machine_protocol_and_dashdash_forces_a_prompt(backend: Any) -> None:
    code, out, _ = cli("version")
    assert code == 0 and "contract_version" in json.loads(out)
    script = Script()
    backend(script, route("main.a"))
    assert cli("--", "version")[0] == 0 and user_text(script.seen[0][1]) == "version"


def test_logical_model_selection(backend: Any) -> None:
    script = Script()
    backend(script, route("code.a"), model="blux-code")
    code, _, err = cli("--model", "blux-code", "refactor this")
    assert code == 0, err
    assert script.seen[0][0] == "code.a" and script.seen[0][1]["model"] == "blux-code"
    assert cli("-m", "gpt-4", "x")[0] == direct.USAGE  # vendor names are not models


# stdin -------------------------------------------------------------------------------------------------


def test_piped_stdin_becomes_the_input(backend: Any) -> None:
    script = Script()
    backend(script, route("main.a"))
    assert cli("diagnose", stdin=io.StringIO("ERROR: disk full\n"))[0] == 0
    text = user_text(script.seen[0][1])
    assert text.startswith('diagnose\n\n<input source="stdin">\nERROR: disk full\n</input>')
    assert cli(stdin=io.StringIO("just the log"))[0] == 0  # bare `blux` with a pipe
    assert user_text(script.seen[1][1]) == "just the log"
    assert cli("--no-stdin", "only this", stdin=io.StringIO("ignored"))[0] == 0
    assert user_text(script.seen[2][1]) == "only this"


def test_unusable_input_is_a_usage_error(backend: Any, tmp_path: Path) -> None:
    backend(Script(), route("main.a"))
    assert cli()[0] == direct.USAGE  # nothing to ask (a terminal, no prompt): argparse usage
    assert cli("--no-stdin")[0] == direct.USAGE
    assert cli("x", stdin=io.StringIO("a\x00b"))[0] == direct.USAGE
    (tmp_path / "config.toml").write_text("max_input_bytes = 8\n")
    code, _, err = cli("x", stdin=io.StringIO("more than eight bytes"))
    assert code == direct.USAGE and "larger than 8 bytes" in err


def test_credential_shaped_input_is_redacted_unless_asked(backend: Any) -> None:
    script = Script()
    backend(script, route("main.a"))
    code, _, err = cli("why", stdin=io.StringIO(f"OPENAI_API_KEY={FAKE_KEY}\nboom\n"))
    assert code == 0 and "redacted 1" in err and FAKE_KEY not in user_text(script.seen[0][1])
    assert cli("--no-redact", "why", stdin=io.StringIO(FAKE_KEY))[0] == 0
    assert FAKE_KEY in user_text(script.seen[1][1])


# modes -------------------------------------------------------------------------------------------------


def test_machine_mode_is_one_result_document(backend: Any) -> None:
    backend(Script(), route("main.a"))
    code, out, err = cli("--json", "hello")
    assert code == 0 and out.count("\n") == 1 and err == ""
    doc = check_envelope(json.loads(out), "result")
    assert doc["status"] == "succeeded" and model_api.schema_errors("response", doc["output"]) == []
    assert doc["output"]["linkage"]["operation_id"] == doc["operation_id"]


def test_machine_mode_reports_usage_and_config_problems_as_documents(backend: Any, tmp_path: Path) -> None:
    backend(Script(), route("main.a"))
    code, out, _ = cli("--json", "--no-stdin")
    assert code == direct.USAGE and json.loads(out)["details"] == {"reason": "no_prompt", "exit_code": 2}
    (tmp_path / "config.toml").write_text('model = "gpt-5"\n')
    code, out, _ = cli("--json", "x")
    assert code == direct.CONFIG and json.loads(out)["details"]["reason"] == "config"


def test_structured_output(backend: Any, tmp_path: Path) -> None:
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}
    (tmp_path / "s.json").write_text(json.dumps(schema))
    script = Script(main_a=lambda r, rt, e: reply(r, rt, text=None, json_value={"verdict": "fine"}))
    backend(script, route("main.a"))
    code, out, err = cli("review", "--schema", str(tmp_path / "s.json"))
    assert code == 0, err
    assert json.loads(out) == {"verdict": "fine"}
    assert script.seen[0][1]["response_format"]["schema"] == schema and "stream" not in script.seen[0][1]


def test_streaming_writes_deltas_and_flags_a_cut_off_answer(backend: Any) -> None:
    def partial(r: dict[str, Any], rt: dict[str, Any], emit: Any) -> dict[str, Any]:
        emit({"type": "delta", "data": {"text": "half an ans"}})
        raise ModelApiError("PROVIDER_ERROR", "connection reset")

    def whole(r: dict[str, Any], rt: dict[str, Any], emit: Any) -> dict[str, Any]:
        for word in ("full ", "answer"):
            emit({"type": "delta", "data": {"text": word}})
        return reply(r, rt, text="full answer")

    backend(Script(main_a=partial, main_b=whole), route("main.a"), route("main.b", "p2", tier="fallback"))
    code, out, err = cli("--stream", "x")
    assert code == 0 and out == "half an ans\nfull answer\n"
    assert "the answer above is incomplete; continuing on main.b" in err


def test_footer_and_verbose_routing(backend: Any) -> None:
    backend(Script(main_a="PROVIDER_UNAVAILABLE"), route("main.a"), route("main.b", "p2", tier="fallback"))
    code, _, err = cli("-v", "x")
    assert code == 0
    assert "blux-main · p2/p2-model" in err and "fallback route" in err and "operation op_" in err
    assert "main.a: failed (PROVIDER_UNAVAILABLE)" in err and "receipt rcpt_" in err
    assert cli("-q", "x")[2] == ""  # stderr is not a terminal and the footer is "auto"


# failures and exit codes ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("behaviour", "exit_code", "words"),
    [
        (("PROVIDER_UNAVAILABLE", "PROVIDER_ERROR"), direct.FAILED, "ALL_ROUTES_FAILED"),
        (("PROVIDER_QUOTA_EXCEEDED", "PROVIDER_QUOTA_EXCEEDED"), direct.FAILED, "no credit or quota"),
        (("CONTENT_FILTERED", "ok"), direct.FAILED, "does not route around a refusal"),
        (("PROVIDER_NOT_CONFIGURED", "PROVIDER_AUTH_FAILED"), direct.CONFIG, "blux doctor"),
        (("PROVIDER_TIMEOUT", "PROVIDER_TIMEOUT"), direct.TIMED_OUT, "raise --timeout"),
        (("CANCELLED", "ok"), direct.CANCELLED, "cancelled"),
    ],
)
def test_failures_are_explicit_with_clear_exit_codes(
    backend: Any, behaviour: tuple[str, str], exit_code: int, words: str
) -> None:
    backend(Script(main_a=behaviour[0], main_b=behaviour[1]), route("main.a"), route("main.b", "p2", tier="fallback"))
    code, out, err = cli("x")
    assert (code, out) == (exit_code, ""), err
    assert words in err and "operation op_" in err
    code, out, _ = cli("--json", "x")
    assert code == exit_code and json.loads(out)["status"] == "failed"


def test_configuration_failures(backend: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend(Script(), route("main.a"))
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'openai_api_key = "{FAKE_KEY}"\n')
    code, _, err = cli("x")
    assert code == direct.CONFIG and "does not store provider credentials" in err and FAKE_KEY not in err
    cfg.write_text(f'routes = "{FAKE_KEY}"\n')
    code, _, err = cli("x")
    assert code == direct.CONFIG and "looks like a credential" in err and FAKE_KEY not in err
    cfg.write_text("colour = 'blue'\n")
    assert cli("x")[0] == direct.CONFIG
    cfg.write_text("not = [toml\n")
    assert cli("x")[0] == direct.CONFIG
    cfg.unlink()
    (tmp_path / "bad.json").write_text('{"contract": "blux.model-routes"}')
    code, _, err = cli("--routes", str(tmp_path / "bad.json"), "x")
    assert code == direct.CONFIG and "is invalid" in err
    code, _, err = cli("--routes", str(tmp_path / "missing.json"), "x")
    assert code == direct.CONFIG and "not readable" in err
    monkeypatch.setenv("BLUX_TIMEOUT", "soon")
    assert cli("x")[0] == direct.CONFIG


def test_config_precedence_and_report(backend: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend(Script(), route("fast.a"), model="blux-fast")
    (tmp_path / "config.toml").write_text('model = "blux-reason"\ntimeout_seconds = 30\n')
    monkeypatch.setenv("BLUX_MODEL", "blux-fast")
    code, out, _ = cli("config", "--json")
    settings = json.loads(out)["settings"]
    assert code == 0 and settings["model"] == {"value": "blux-fast", "source": "env BLUX_MODEL"}
    assert settings["timeout_seconds"]["value"] == 30 and settings["timeout_seconds"]["source"].startswith("file ")
    script = Script()
    backend(script, route("fast.a"), model="blux-fast")
    assert cli("x")[0] == 0 and script.seen[0][1]["model"] == "blux-fast" and script.seen[0][1]["timeout_ms"] == 30000
    assert cli("--timeout", "2.5", "x")[0] == 0 and script.seen[1][1]["timeout_ms"] == 2500


# chat ------------------------------------------------------------------------------------------------


def test_chat_keeps_history_and_links_turns(backend: Any) -> None:
    replies = iter(["first answer", "second answer"])
    script = Script(main_a=lambda r, rt, e: reply(r, rt, text=next(replies)))
    backend(script, route("main.a"))
    code, out, _ = cli("chat", "--json", stdin=io.StringIO("hello\n\nwhat did you say?\n/exit\nignored\n"))
    assert code == 0
    results = [json.loads(line) for line in out.splitlines()]
    assert [r["output"]["output"]["text"] for r in results] == ["first answer", "second answer"]
    second = script.seen[1][1]["messages"]
    assert [(m["role"], m["content"]) for m in second] == [
        ("user", "hello"),
        ("assistant", "first answer"),
        ("user", "what did you say?"),
    ]
    one, two = (r["output"]["linkage"] for r in results)
    assert two["session_id"] == one["session_id"] and two["parent_operation_id"] == one["operation_id"]


def test_chat_commands_and_failed_turns(backend: Any) -> None:
    calls = {"n": 0}

    def flaky(r: dict[str, Any], rt: dict[str, Any], emit: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ModelApiError("PROVIDER_REJECTED", "no")
        return reply(r, rt, text=f"answer {calls['n']}")

    script = Script(main_a=flaky, **{"blux-fast_x": flaky})
    backend(script, route("main.a"))
    lines = "one\ntwo\nthree\n/reset\n/model blux-fast\n/system be brief\nfour\n/model nope\n/bogus\n"
    code, out, err = cli("chat", stdin=io.StringIO(lines))
    assert code == direct.FAILED  # piped chat reports its worst turn
    assert out == "answer 1\nanswer 3\nanswer 4\n"
    third = [(m["role"], m["content"]) for m in script.seen[2][1]["messages"]]
    assert third == [("user", "one"), ("assistant", "answer 1"), ("user", "three")]  # failed turn dropped
    fourth = script.seen[3]
    assert fourth[0] == "blux-fast.x" and [m["content"] for m in fourth[1]["messages"]] == ["four"]
    assert fourth[1]["system"] == "be brief"
    assert "conversation cleared" in err and "unknown model" in err and "unknown command /bogus" in err


# doctor ----------------------------------------------------------------------------------------------


def test_doctor_reports_backends_and_credential_presence_only(
    backend: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = table(dict(route("main.a"), credential_env=["OPENAI_API_KEY"]))
    doc["backends"]["test"]["entrypoint"] = "python3 -c pass"
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    code, out, _ = cli("doctor", "--routes", str(path), "--json")
    report = json.loads(out)
    assert code == 0 and report["status"] == "ok" and FAKE_KEY not in out
    main_model = next(m for m in report["models"] if m["model"] == "blux-main")
    assert main_model["routes"][0]["credentials"] == {"OPENAI_API_KEY": "set"}
    doc["backends"]["test"]["entrypoint"] = "no-such-blux-backend serve"
    path.write_text(json.dumps(doc))
    code, out, _ = cli("doctor", "--routes", str(path))
    assert code == direct.CONFIG and "FAIL" in out and "NO INSTALLED BACKEND" in out and FAKE_KEY not in out


def test_doctor_probe_calls_every_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doc = table(route("main.a"), route("main.b", "p2", tier="fallback"))
    doc["backends"]["test"]["entrypoint"] = "python3 -c pass"
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(doc))
    script = Script(main_b="PROVIDER_QUOTA_EXCEEDED")
    monkeypatch.setattr(direct, "make_router", lambda d: ModelRouter(d, {"test": CallableBackend(script)}))
    code, out, err = cli("doctor", "--routes", str(path), "--probe", "-m", "blux-main", "--json")
    rows = json.loads(out)["models"][0]["routes"]
    assert code == 0, err
    assert rows[0]["probe"]["status"] == "ok" and rows[1]["probe"]["error_code"] == "PROVIDER_QUOTA_EXCEEDED"
    assert [s[1]["routing"] for s in script.seen] == [
        {"allow_fallback": False, "providers": ["p1"]},
        {"allow_fallback": False, "providers": ["p2"]},
    ]
    broken = Script(main_a="PROVIDER_ERROR", main_b="PROVIDER_ERROR")
    monkeypatch.setattr(direct, "make_router", lambda d: ModelRouter(d, {"test": CallableBackend(broken)}))
    assert cli("doctor", "--routes", str(path), "--probe", "-m", "blux-main")[0] == direct.FAILED
