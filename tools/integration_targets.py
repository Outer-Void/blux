#!/usr/bin/env python3
"""Install and check out BLUX's cross-repository integration targets at the pinned commits (OV-61).

    python tools/integration_targets.py check                 # the lock is well-formed and CI uses it
    python tools/integration_targets.py install [--from DIR]  # pip install --no-deps each target (DIR: local clones)
    python tools/integration_targets.py checkout DIR          # clone each target at its commit into DIR/<name>
    python tools/integration_targets.py env DIR               # print KEY=VALUE lines for the integration tests

`env` prints, for every target, its BLUX_REQUIRE_* flag (1 when this Python meets the target's minimum, else 0)
and, where the tests read one, its *_PROJECT_DIR. CI appends that output to $GITHUB_ENV, so a missing target is
an error instead of a silent skip. Targets come only from integrations/targets.lock.json.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "integrations" / "targets.lock.json"
CI = ROOT / ".github" / "workflows" / "ci.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")


def load() -> list[dict[str, Any]]:
    doc = json.loads(LOCK.read_text(encoding="utf-8"))
    if doc.get("contract") != "blux.integration-targets" or doc.get("version") != 1:
        raise SystemExit("integrations/targets.lock.json: unexpected contract or version")
    return list(doc["targets"])


def supported(target: dict[str, Any]) -> bool:
    major, minor = (int(x) for x in target["python_min"].split("."))
    return sys.version_info[:2] >= (major, minor)


def problems(targets: list[dict[str, Any]], ci_text: str) -> list[str]:
    out = []
    names = [t["name"] for t in targets]
    if len(set(names)) != len(names):
        out.append("duplicate target names")
    for t in targets:
        if not SHA.match(t["commit"]):
            out.append(f"{t['name']}: commit must be a full 40-character SHA")
        if not re.match(r"^\d+\.\d+$", t["python_min"]):
            out.append(f"{t['name']}: python_min must look like 3.11")
        if not t["require"].startswith("BLUX_REQUIRE_"):
            out.append(f"{t['name']}: require must be a BLUX_REQUIRE_* variable")
        if f"git+https://github.com/{t['repository']}.git@" in ci_text:
            out.append(f"ci.yml installs {t['repository']} at a hard-coded commit; use tools/integration_targets.py")
        if re.search(rf"\b{t['require']}:", ci_text):
            out.append(f"ci.yml sets {t['require']} by hand; it must come from `integration_targets.py env`")
    for cmd in ("integration_targets.py install", "integration_targets.py checkout", "integration_targets.py env"):
        if cmd not in ci_text:
            out.append(f"ci.yml does not run `{cmd}`")
    return out


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True)  # noqa: S603 - argv built from the lock, no shell


def git_head(path: str) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", path, "rev-parse", "HEAD"],  # noqa: S607 - git from PATH, as in CI
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip()


def install(targets: list[dict[str, Any]], source: Path | None) -> None:
    for t in targets:
        if not supported(t):
            print(f"skip {t['name']}: needs Python {t['python_min']}+", file=sys.stderr)
            continue
        if source is not None:
            spec = str(source / t["name"])
            head = git_head(spec)
            if head != t["commit"]:
                raise SystemExit(f"{spec} is at {head or '?'}, the lock pins {t['commit']}")
        else:
            spec = f"{t['package']} @ git+https://github.com/{t['repository']}.git@{t['commit']}"
        run([sys.executable, "-m", "pip", "install", "--no-deps", spec])


def checkout(targets: list[dict[str, Any]], dest: Path) -> None:
    for t in targets:
        path = dest / t["name"]
        if not (path / ".git").is_dir():
            path.mkdir(parents=True, exist_ok=True)
            run(["git", "-C", str(path), "init", "-q"])
            run(["git", "-C", str(path), "remote", "add", "origin", f"https://github.com/{t['repository']}.git"])
        run(["git", "-C", str(path), "fetch", "-q", "--depth", "1", "origin", t["commit"]])
        run(["git", "-C", str(path), "checkout", "-q", "--detach", t["commit"]])


def env_lines(targets: list[dict[str, Any]], dest: Path) -> list[str]:
    lines = []
    for t in targets:
        lines.append(f"{t['require']}={'1' if supported(t) else '0'}")
        if t.get("project_env"):
            lines.append(f"{t['project_env']}={(dest / t['name']).resolve()}")
    return lines


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("check", "install", "checkout", "env"):
        print(__doc__, file=sys.stderr)
        return 2
    targets = load()
    cmd = argv[0]
    if cmd == "check":
        found = problems(targets, CI.read_text(encoding="utf-8"))
        for p in found:
            print(f"FAIL: {p}")
        if not found:
            print(f"integration targets: {len(targets)} pinned, CI uses the lock")
        return 1 if found else 0
    if cmd == "install":
        source = Path(argv[2]) if len(argv) == 3 and argv[1] == "--from" else None
        install(targets, source)
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    if cmd == "checkout":
        checkout(targets, Path(argv[1]))
    else:
        print("\n".join(env_lines(targets, Path(argv[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
