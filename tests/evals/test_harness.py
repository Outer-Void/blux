"""The Generation 0 harness: in-process suites run, results are deterministic, missing engines are BLOCKED."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.blux_eval import run


def test_in_process_suites_measure_real_code() -> None:
    for suite in (run.policy_suite(), run.routing_suite(), run.malformed_suite()):
        assert suite["status"] == "MEASURED" and suite["cases"] > 0
        assert 0 <= suite["deterministic"]["task_success"] <= 1


def test_routing_uses_the_packaged_table_and_never_violates_provider_policy() -> None:
    suite = run.routing_suite()
    assert suite["routes_id"] and suite["deterministic"]["policy_violations"] == 0


def test_malformed_inputs_are_refused_without_crashing() -> None:
    d = run.malformed_suite()["deterministic"]
    assert d["crashes"] == [] and d["accepted_malformed"] == []


def test_missing_engines_and_models_are_blocked_not_scored(tmp_path: Path) -> None:
    blocked = run._summarize(run._engine(tmp_path, "blux-coga-dataset", None, []), "coga")
    assert blocked["status"] == "BLOCKED" and "deterministic" not in blocked
    model = run.model_suite("intent", live=False)
    assert model["status"] == "BLOCKED" and "task_success" not in json.dumps(model)


def test_reports_are_reproducible(tmp_path: Path) -> None:
    args = argparse.Namespace(
        workspace=tmp_path, blux_coga_dataset_python=None, blux_ca_dataset_python=None, dat_bin=None, live=False
    )
    a, b = run.run(args), run.run(args)
    assert run.deterministic_view(a) == run.deterministic_view(b)
    assert "| policy_risk | MEASURED |" in run.markdown(a)
