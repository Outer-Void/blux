"""BLUX Generation 0 evaluation harness (Stage 35).

Generation 0 = external models + BLUX routing + CogA + cA + DAT + Runtime + BLUX System. Each suite is
either MEASURED (real BLUX code or the pinned engines ran) or BLOCKED (what it needs is missing; the reason
is recorded and no score is invented). Deterministic metrics are separated from timings, so a re-run can be
checked against a committed baseline: `--check REPORT` fails on any deterministic difference.

    python -m evals.blux_eval.run --out evals/reports/gen0 [--workspace ~/ov] [--check evals/reports/gen0/report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from blux_runtime.models import CallableBackend, ModelRouter
from blux_runtime.models.capability import default_routes
from blux_runtime.security.commands import detect
from blux_sdk import model_api
from blux_sdk import schemas as contract_schemas
from blux_sdk.errors import BluxError
from blux_sdk.model_api import ModelApiError

HERE = Path(__file__).resolve().parent
SUITES = HERE.parent / "suites"
REPO = HERE.parents[1]
HARNESS_VERSION = "1.0.0"


def cases(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (SUITES / f"{name}.jsonl").read_text().splitlines() if line.strip()]


def timing(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"p50_ms": None, "p95_ms": None, "total_ms": None}
    s = sorted(samples)
    return {
        "p50_ms": round(statistics.median(s), 2),
        "p95_ms": round(s[min(len(s) - 1, int(0.95 * len(s)))], 2),
        "total_ms": round(sum(s), 2),
    }


def rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


# --- policy / risk (Android and Termux device commands included) ------------------------------------------


def policy_suite() -> dict[str, Any]:
    rows, times = [], []
    for c in cases("policy"):
        t0 = time.perf_counter()
        d = detect(c["command"])
        times.append((time.perf_counter() - t0) * 1000)
        rows.append(
            {
                "id": c["id"],
                "ok": d.dangerous == c["dangerous"] and (not c["dangerous"] or d.device == c["device"]),
                "missed": c["dangerous"] and not d.dangerous,
                "false_alarm": d.dangerous and not c["dangerous"],
                "device_mismatch": c["dangerous"] and d.dangerous and d.device != c["device"],
                "rule": d.rule,
            }
        )
    return {
        "status": "MEASURED",
        "subject": "blux_runtime.security.commands.detect",
        "cases": len(rows),
        "deterministic": {
            "task_success": rate(sum(r["ok"] for r in rows), len(rows)),
            "security_regressions": sorted(r["id"] for r in rows if r["missed"]),
            "false_alarms": sorted(r["id"] for r in rows if r["false_alarm"]),
            "device_flag_mismatches": sorted(r["id"] for r in rows if r["device_mismatch"]),
        },
        "timing": timing(times),
    }


# --- routing over the packaged production routing table (provider outcomes simulated) ----------------------


def _scripted(outcomes: dict[str, str]) -> Callable[..., dict[str, Any]]:
    def fn(request: dict[str, Any], route: dict[str, Any], emit: Any) -> dict[str, Any]:
        code = outcomes.get(route["route_id"])
        if code:
            raise ModelApiError(code, f"simulated {code}", provider=route["provider"], route_id=route["route_id"])
        emit({"type": "delta", "data": {"text": "ok"}})
        return {
            "contract": "blux.model-response",
            "model_api_version": "1.0",
            "response_id": model_api.new_response_id(),
            "model": request["model"],
            "status": "completed",
            "finish_reason": "stop",
            "output": {"text": "ok", "json": None, "tool_calls": []},
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "reasoning_tokens": None,
                "cached_input_tokens": None,
                "reported_by_provider": False,
            },
            "provider": {
                "backend": route["backend"],
                "provider": route["provider"],
                "provider_model": route["provider_model"],
                "route_id": route["route_id"],
                "locality": route["locality"],
                "request_id": "eval",
            },
            "routing": {
                "route_id": route["route_id"],
                "fallback_used": False,
                "degraded": False,
                "notes": [],
                "attempts": [
                    {
                        "route_id": route["route_id"],
                        "provider": route["provider"],
                        "provider_model": route["provider_model"],
                        "outcome": "succeeded",
                        "error_code": None,
                        "retryable": None,
                        "reason": None,
                        "latency_ms": 0,
                        "retries": 0,
                    }
                ],
            },
            "determinism": {"requested": False, "supported": False, "applied": {"temperature": None, "seed": None}},
            "timing": {"latency_ms": 0, "first_token_ms": None},
            "linkage": None,
        }

    return fn


def routing_suite() -> dict[str, Any]:
    table = default_routes()
    rows, times, violations = [], [], 0
    for c in cases("routing"):
        router = ModelRouter(table, {name: CallableBackend(_scripted(c["outcomes"])) for name in table["backends"]})
        req = model_api.request(
            c["model"], "evaluate routing", routing=c.get("routing"), capabilities=c.get("capabilities")
        )
        t0 = time.perf_counter()
        try:
            resp = router.generate(req)
            got = {
                "route_id": resp["routing"]["route_id"],
                "fallback_used": resp["routing"]["fallback_used"],
                "provider": resp["provider"]["provider"],
            }
        except ModelApiError as exc:
            got = {"error": exc.code}
        times.append((time.perf_counter() - t0) * 1000)
        exp = c["expect"]
        ok = all(got.get(k) == v for k, v in exp.items() if k != "never_providers")
        bad = got.get("provider") in exp.get("never_providers", [])
        violations += bad
        rows.append({"id": c["id"], "ok": ok and not bad, "got": got})
    return {
        "status": "MEASURED",
        "subject": "ModelRouter with the packaged default_routes.json (provider outcomes simulated)",
        "routes_id": table["routes_id"],
        "cases": len(rows),
        "deterministic": {
            "task_success": rate(sum(r["ok"] for r in rows), len(rows)),
            "policy_violations": violations,
            "failures": sorted(r["id"] for r in rows if not r["ok"]),
            "observed": {r["id"]: r["got"] for r in rows},
        },
        "timing": timing(times),
    }


# --- malformed inputs --------------------------------------------------------------------------------------


def malformed_suite() -> dict[str, Any]:
    rows, times = [], []
    for c in cases("malformed"):
        t0 = time.perf_counter()
        outcome = "accepted"
        try:
            if c["kind"] == "model-request":
                model_api.check_request(c["doc"])
            elif contract_schemas.schema_errors(c["kind"], c["doc"]):
                raise BluxError("INVALID", "schema errors")
        except (BluxError, ModelApiError):
            outcome = "refused"
        except Exception as exc:  # noqa: BLE001 - an untyped exception is the measured failure
            outcome = f"crashed:{type(exc).__name__}"
        times.append((time.perf_counter() - t0) * 1000)
        rows.append({"id": c["id"], "outcome": outcome})
    return {
        "status": "MEASURED",
        "subject": "blux_sdk model_api.check_request and Contract v1 schemas",
        "cases": len(rows),
        "deterministic": {
            "task_success": rate(sum(r["outcome"] == "refused" for r in rows), len(rows)),
            "accepted_malformed": sorted(r["id"] for r in rows if r["outcome"] == "accepted"),
            "crashes": sorted(r["id"] for r in rows if r["outcome"].startswith("crashed")),
        },
        "timing": timing(times),
    }


# --- engines: CogA reasoning, cA construction/repair with DAT validation ------------------------------------


def _engine(workspace: Path, dataset: str, python: Path | None, extra_path: list[Path]) -> dict[str, Any]:
    root = workspace / dataset
    if python is None or not python.exists():
        return {
            "status": "BLOCKED",
            "reason": f"no interpreter with the pinned engines for {dataset} (pass --{dataset}-python)",
        }
    env = {**os.environ, "PATH": os.pathsep.join([*map(str, extra_path), os.environ.get("PATH", "")])}
    proc = subprocess.run(
        [str(python), str(HERE / "engine_runner.py"), "test", "holdout", "regression", "adversarial"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if proc.returncode != 0:
        return {"status": "BLOCKED", "reason": (proc.stderr.strip().splitlines() or ["engine runner failed"])[-1][:300]}
    data = json.loads(proc.stdout)
    data["commit"] = _git(root)
    return data


def _summarize(data: dict[str, Any], kind: str) -> dict[str, Any]:
    if data.get("status") == "BLOCKED":
        return data
    rows = data["results"]
    adv = [r for r in rows if r["split"] == "adversarial"]
    by_cat: dict[str, list[int]] = {}
    for r in rows:
        by_cat.setdefault(r["category"] or "?", []).append(int(r["match"]))
    det: dict[str, Any] = {
        "task_success": rate(sum(r["match"] for r in rows), len(rows)),
        "structural_validity": rate(sum(r["valid"] for r in rows), len(rows)),
        "crashes": sum(1 for r in rows if r["crashed"]),
        "security_regressions": sorted(r["record_id"] for r in adv if r["expected_rejected"] and not r["rejected"]),
        "adversarial_refusal_rate": rate(sum(r["rejected"] for r in adv), len(adv)),
        "per_split": {
            s: rate(sum(r["match"] for r in rows if r["split"] == s), sum(1 for r in rows if r["split"] == s))
            for s in sorted({r["split"] for r in rows})
        },
        "per_category": {k: rate(sum(v), len(v)) for k, v in sorted(by_cat.items())},
        "hallucinated_capabilities": 0,  # engines are invoked with a fixed capability; nothing is chosen
        "token_cost": 0,  # deterministic engines: no model tokens
    }
    if kind == "ca":
        gates = [g for r in rows for g in r.get("dat_gates", [])]
        det |= {
            "dat_gate_pass_rate": rate(sum(g == "pass" for g in gates), len(gates)),
            "dat_gate_verdicts": {g: gates.count(g) for g in sorted(set(map(str, gates)))},
            "repair_count": sum(r.get("repairs", 0) for r in rows),
            "self_certification_violations": sum(1 for r in rows if r.get("self_certified")),
            "unnecessary_edits": sum(r.get("extra_operations", 0) for r in rows),
            "tool_calls_per_case": rate(sum(r.get("steps", 0) for r in rows), len(rows)),
        }
    return {
        "status": "MEASURED",
        "subject": f"{data['dataset']} {data.get('dataset_version')} replayed through pinned engines",
        "engines": data["engines"],
        "dataset_commit": data["commit"],
        "cases": len(rows),
        "deterministic": det,
        "timing": timing([r["wall_ms"] for r in rows]) | {"cpu_total_ms": round(sum(r["cpu_ms"] for r in rows), 2)},
    }


# --- model-dependent suites --------------------------------------------------------------------------------


def model_suite(name: str, live: bool) -> dict[str, Any]:
    n = len(cases(name))
    creds = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY") if os.environ.get(k)]
    if not live or not creds:
        return {
            "status": "BLOCKED",
            "cases": n,
            "reason": "needs an external model: run with --live and provider credentials (none available: provider "
            "accounts have no balance, docs finding F10)",
        }
    return {"status": "BLOCKED", "cases": n, "reason": "live model runner not implemented in harness 1.0.0"}


def _git(path: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run(args: argparse.Namespace) -> dict[str, Any]:
    ws = args.workspace.expanduser()
    suites = {
        "policy_risk": policy_suite(),
        "routing": routing_suite(),
        "malformed_inputs": malformed_suite(),
        "reasoning_structure": _summarize(_engine(ws, "blux-coga-dataset", args.blux_coga_dataset_python, []), "coga"),
        "construction_repair_dat": _summarize(
            _engine(ws, "blux-ca-dataset", args.blux_ca_dataset_python, [p for p in [args.dat_bin] if p]), "ca"
        ),
        "intent": model_suite("intent", args.live),
        "tool_selection": model_suite("tool_selection", args.live),
        "repository_diagnosis": model_suite("repo_diagnosis", args.live),
    }
    return {
        "kind": "blux.eval.gen0-report",
        "harness_version": HARNESS_VERSION,
        "generation": "0: external models + BLUX routing + CogA + cA + DAT + Runtime + BLUX System",
        "environment": {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "blux_commit": _git(REPO),
        },
        "models": {
            "provider_models": "none measured (model suites BLOCKED)",
            "routes_id": suites["routing"].get("routes_id"),
        },
        "suites": suites,
        "summary": {name: s["status"] for name, s in suites.items()},
    }


def deterministic_view(report: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {k: s.get(k) for k in ("status", "cases", "deterministic", "engines")}
        for name, s in report["suites"].items()
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BLUX Generation 0 baseline",
        "",
        f"Harness {report['harness_version']} · BLUX `{report['environment']['blux_commit']}` · "
        f"Python {report['environment']['python']} · {report['environment']['platform']}",
        "",
        "| Suite | Status | Cases | Task success | Key results | p50 ms |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in report["suites"].items():
        d = s.get("deterministic", {})
        key = []
        for k in (
            "security_regressions",
            "policy_violations",
            "crashes",
            "accepted_malformed",
            "adversarial_refusal_rate",
            "dat_gate_pass_rate",
            "self_certification_violations",
            "unnecessary_edits",
            "repair_count",
        ):
            if k in d:
                v = d[k]
                key.append(f"{k.replace('_', ' ')}: {len(v) if isinstance(v, list) else v}")
        if s["status"] == "BLOCKED":
            key = [s["reason"]]
        lines.append(
            f"| {name} | {s['status']} | {s.get('cases', '')} | {d.get('task_success', '—')} | {'; '.join(key)} | "
            f"{(s.get('timing') or {}).get('p50_ms', '—')} |"
        )
    lines += [
        "",
        "Deterministic results must reproduce exactly (`--check`); timings vary by machine.",
        "Model-dependent suites are BLOCKED until provider credentials exist; no score is reported for them.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="blux_eval")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workspace", type=Path, default=REPO.parent)
    p.add_argument("--blux-coga-dataset-python", type=Path)
    p.add_argument("--blux-ca-dataset-python", type=Path)
    p.add_argument("--dat-bin", type=Path, help="directory containing the `dat` executable (needed by the cA adapter)")
    p.add_argument("--live", action="store_true", help="also run model-dependent suites (needs provider credentials)")
    p.add_argument("--check", type=Path, help="baseline report.json: exit 1 if any deterministic result differs")
    args = p.parse_args(argv)
    report = run(args)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    (args.out / "REPORT.md").write_text(markdown(report))
    print(json.dumps(report["summary"], sort_keys=True))
    if args.check:
        base = json.loads(args.check.read_text())
        if deterministic_view(base) != deterministic_view(report):
            print("deterministic results differ from the baseline", file=sys.stderr)
            return 1
        print("deterministic results match the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
