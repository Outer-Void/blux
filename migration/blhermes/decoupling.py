"""Stage 16 decoupling check: does anything in BLUX still depend on the BLHermes fork?

    python migration/blhermes/decoupling.py [--json]

Scans every tracked file of this repository for each dependency class. A hit is allowed only by an explicit,
reasoned rule (ALLOWED). Allowed hits include:
- migration inventory data;
- the time-bounded legacy importer;
- deny lists that protect legacy credential files;
- historical documentation.
Any other hit is a violation. Exit 0 when there are none.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CLASSES: dict[str, tuple[str, str]] = {
    "source_import": (
        r"^\s*(?:from|import)\s+(?:hermes_\w+|run_agent|model_tools|toolsets|tui_gateway|acp_adapter|mcp_serve)\b",
        "*.py",
    ),
    "build_dependency": (
        r"blhermes|hermes-agent|hermes_agent",
        "pyproject.toml|requirements*.txt|sdk/python/pyproject.toml",
    ),
    "ci_fetch": (r"blhermes|hermes-agent|NousResearch", ".github/**"),
    "packaging_dependency": (r"blhermes|hermes-agent|Package: hermes", "packaging/**"),
    "product_url": (r"nousresearch\.com|hermes-agent\.nousresearch", "*"),
    "updater_feed": (
        r"releases/latest|self[_-]?update\s*[:=]\s*true|update[_-]channel|autoUpdater|check_for_updates",
        "*",
    ),
    "telemetry_hook": (r"opentelemetry|otlp_exporter|posthog|sentry_sdk|shared[_-]metrics|mixpanel\.track", "*"),
    "hermes_home_requirement": (r"HERMES_HOME|\.hermes\b", "*"),
    "command_identity": (r"^\s*hermes(?:-agent|-acp)?\s*=|\bhermes chat\b|\bhermes gateway\b", "*"),
    "stale_branding": (r"\bBLHermes\b|\bHermes Agent\b", "*"),
}

# (glob over the repo-relative path, classes it may contain, reason)
ALLOWED: list[tuple[str, str, str]] = [
    (
        "migration/blhermes/**",
        "*",
        "Stage 11 forensic inventory, matrix, characterization and this checker: data about the fork",
    ),
    (
        "tests/migration/**",
        "*",
        "tests of the inventory/matrix/characterization (fork-dependent tests skip without it)",
    ),
    (
        "runtime/blux_runtime/compat/**",
        "hermes_home_requirement|stale_branding",
        "time-bounded opt-in legacy importer (expires 2027-04-30)",
    ),
    (
        "cli/blux_cli/migrate.py",
        "hermes_home_requirement|stale_branding",
        "`blux migrate hermes`, the importer's command",
    ),
    (
        "runtime/blux_runtime/security/paths.py",
        "hermes_home_requirement",
        "deny list protecting legacy credential files",
    ),
    (
        "tests/core/**",
        "hermes_home_requirement|stale_branding|source_import",
        "legacy-import and no-fork-import tests (synthetic legacy state)",
    ),
    (
        "tests/extensions/**",
        "stale_branding|hermes_home_requirement|source_import",
        "optional SKILL.md format check (skips without the fork); no-fork-import test",
    ),
    (
        "packaging/termux/maintainer/postinst",
        "hermes_home_requirement|stale_branding|packaging_dependency",
        "prints a hint when legacy state exists; reads nothing",
    ),
    ("packaging/termux/validate_installed.py", "hermes_home_requirement", "legacy-migration check on synthetic state"),
    ("packaging/termux/audit_deb.py", "*", "detection patterns for foreign identity/telemetry"),
    (
        "packaging/termux/THIRD_PARTY_NOTICES.md",
        "stale_branding|packaging_dependency",
        "states that no BLHermes/Hermes code is in the package",
    ),
    (".dat/policy.json", "stale_branding", "release-gate suppression reasons naming the migration probe fixtures"),
    ("docs/**", "*", "architecture decisions and history (no runtime or build effect)"),
    (
        "training/experiments/REPORT.md",
        "stale_branding",
        "immutable Router 0.1 evidence names a skipped legacy-checkout prerequisite; no runtime/build effect",
    ),
    ("CHANGELOG.md", "*", "history"),
    ("CREDITS.md", "*", "attribution required by the licence and the credits ledger"),
    ("README.md", "stale_branding", "states the migration directory and its notice"),
    ("intelligence/README.md", "stale_branding", "history of the intelligence surface"),
    ("tests/hooks/**", "*", "asserts upstream hook names are not reused"),
]


def tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], check=True, capture_output=True, text=True).stdout  # noqa: S603, S607
    return [p for p in out.splitlines() if p]


def _glob(path: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    for alt in pattern.split("|"):
        rx = "^" + re.escape(alt).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "$"
        if re.match(rx, path) or (("/" not in alt) and re.match(rx, Path(path).name)):
            return True
    return False


PROVENANCE_CLASSES = ("stale_branding", "hermes_home_requirement")


def _docstring_lines(text: str) -> range:
    """Line numbers of the module docstring (provenance notes live there, not user-facing strings)."""
    m = re.match(r'\s*(?:#[^\n]*\n\s*)*("""|\'\'\')', text)
    if not m:
        return range(0)
    end = text.find(m.group(1), m.end())
    if end < 0:
        return range(0)
    return range(text.count("\n", 0, m.start(1)) + 1, text.count("\n", 0, end) + 2)


def scan() -> dict[str, object]:
    files = tracked()
    report: dict[str, dict[str, list[dict[str, object]]]] = {c: {"allowed": [], "violations": []} for c in CLASSES}
    for path in files:
        full = ROOT / path
        if not full.is_file() or full.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for cls, (pattern, where) in CLASSES.items():
            if not _glob(path, where):
                continue
            hits = [
                i
                for i, line in enumerate(text.splitlines(), 1)
                if re.search(pattern, line, re.I if cls != "source_import" else 0)
            ]
            if not hits:
                continue
            if cls in PROVENANCE_CLASSES and path.endswith(".py"):
                doc = _docstring_lines(text)
                in_doc = [i for i in hits if i in doc]
                hits = [i for i in hits if i not in doc]
                if in_doc:
                    report[cls]["allowed"].append(
                        {
                            "path": path,
                            "lines": in_doc[:10],
                            "count": len(in_doc),
                            "reason": "provenance note in the module docstring",
                        }
                    )
                if not hits:
                    continue
            rule = next((r for r in ALLOWED if _glob(path, r[0]) and (r[1] == "*" or cls in r[1].split("|"))), None)
            entry = {"path": path, "lines": hits[:10], "count": len(hits)}
            if rule:
                report[cls]["allowed"].append({**entry, "reason": rule[2]})
            else:
                report[cls]["violations"].append(entry)
    violations = sum(len(v["violations"]) for v in report.values())
    return {
        "contract": "blux.blhermes-decoupling",
        "files_scanned": len(files),
        "classes": report,
        "violations": violations,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    doc = scan()
    if args.json:
        sys.stdout.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    else:
        for cls, v in doc["classes"].items():  # type: ignore[union-attr]
            print(f"{cls:26} violations={len(v['violations'])} allowed={len(v['allowed'])}")
            for e in v["violations"]:
                print(f"   VIOLATION {e['path']}:{e['lines']}")
    return 0 if doc["violations"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
