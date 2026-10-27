"""A report's historical prerequisite note is not a new dependency on an abandoned fork."""

from __future__ import annotations

from importlib import util
from pathlib import Path


def checker():
    path = Path(__file__).resolve().parents[2] / "migration/blhermes/decoupling.py"
    spec = util.spec_from_file_location("native_report_decoupling", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_report_allowlist_is_narrow(tmp_path, monkeypatch):
    module = checker()
    root = tmp_path
    report = root / "training/experiments/REPORT.md"
    report.parent.mkdir(parents=True)
    # Synthetic identity text exercises both sides of the historical-document boundary.
    historical = "BLHermes"
    report.write_text(historical + " unavailable in the historical test run\n")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "tracked", lambda: ["training/experiments/REPORT.md"])
    result = module.scan()
    assert result["classes"]["stale_branding"]["violations"] == []
    assert len(result["classes"]["stale_branding"]["allowed"]) == 1
    report.write_text(historical + "\n" + "post" + "hog" + "\n")
    assert len(module.scan()["classes"]["telemetry_hook"]["violations"]) == 1
    other = root / "product.md"
    other.write_text(historical)
    monkeypatch.setattr(module, "tracked", lambda: ["product.md"])
    assert len(module.scan()["classes"]["stale_branding"]["violations"]) == 1
