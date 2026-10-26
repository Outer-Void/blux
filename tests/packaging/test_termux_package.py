"""Stage 15 HARD GATE: the BLUX-owned Termux .deb exists, passes the artifact audit, and survives its lifecycle.

- **Build.** It builds reproducibly and the audit passes.
- **Lifecycle.** dpkg runs the actual package in a throwaway root: clean install, reinstall, upgrade, remove
  and purge.
- **Each step.** validate_installed.py exercises CLI, TUI, providers, tools, skills, plugins, MCP,
  subagents, cron, gateway, state paths and legacy migration.
- **State.** User state is preserved through remove and purge.
- **Failure and recovery.** A damaged install is detected and repaired by reinstalling.
- **Bionic runtime.** When Termux's own python3 is on this machine, the package payload is validated
  under it, with Termux's python-rpds-py extracted and never installed.

Needs dpkg, dpkg-deb and the ../blux-gpt checkout at the pinned commit (or $BLUX_BLUXGPT_SRC).
Network is needed only to fetch pinned vendor artifacts that are not cached ($BLUX_TERMUX_VENDOR).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import ROOT

HERE = ROOT / "packaging" / "termux"
PREFIX = "data/data/com.termux/files/usr"
TERMUX_PY = Path("/data/data/com.termux/files/usr/bin/python3.14")
GPT_SRC = Path(os.environ.get("BLUX_BLUXGPT_SRC", ROOT.parent / "blux-gpt"))
missing = [t for t in ("dpkg", "dpkg-deb") if not shutil.which(t)]
pytestmark = pytest.mark.skipif(
    bool(missing) or not (GPT_SRC / ".git").exists(), reason=f"needs {missing or 'blux-gpt checkout'}"
)


def build(out: Path, revision: str) -> Path:
    vendor = Path(os.environ.get("BLUX_TERMUX_VENDOR", ROOT / "build" / "termux-vendor"))
    proc = subprocess.run(
        [
            sys.executable,
            str(HERE / "build_deb.py"),
            "--out",
            str(out),
            "--vendor-dir",
            str(vendor),
            "--revision",
            revision,
            "--bluxgpt-src",
            str(GPT_SRC),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(proc.stdout.split()[0])


@pytest.fixture(scope="module")
def debs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("dist")
    return {"1": build(out, "1"), "2": build(out, "2")}


class Sandbox:
    def __init__(self, base: Path) -> None:
        self.root, self.home = base / "root", base / "home"
        self.admin = self.root / "var" / "lib" / "dpkg"
        for d in (self.root, self.admin / "updates", self.admin / "info", self.home):
            d.mkdir(parents=True, exist_ok=True)
        for f in ("status", "available"):
            (self.admin / f).touch()

    def dpkg(self, *args: str) -> subprocess.CompletedProcess[str]:
        # No fakeroot: its glibc preload cannot load into Termux's Bionic sh, which runs the maintainer scripts.
        cmd = [
            "dpkg",
            f"--root={self.root}",
            "--force-not-root",
            "--force-script-chrootless",
            "--force-bad-path",
            "--force-depends",
            "--force-architecture",
            *args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "HOME": str(self.home)})

    def status(self) -> str:
        text = (self.admin / "status").read_text()
        block = next((b for b in text.split("\n\n") if b.startswith("Package: blux\n")), "")
        return block

    @property
    def prefix(self) -> Path:
        return self.root / PREFIX

    def validate(self, python: str = sys.executable, extra_site: Path | None = None) -> dict[str, Any]:
        # -S: no site hooks, so an editable BLUX install in this interpreter cannot shadow the package payload.
        # Dependencies (rpds-py) come from --extra-site instead (the venv's site-packages, or Termux's rpds).
        extra_site = extra_site or Path(sysconfig.get_paths()["purelib"])
        cmd = [python, "-I", "-S", str(HERE / "validate_installed.py"), "--prefix", str(self.prefix)]
        if extra_site:
            cmd += ["--extra-site", str(extra_site)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        report: dict[str, Any] = json.loads(proc.stdout)
        report["exit_code"] = proc.returncode
        return report


def test_artifact_exists_is_reproducible_and_passes_the_audit(debs: dict[str, Path], tmp_path: Path) -> None:
    for deb in debs.values():
        proc = subprocess.run([sys.executable, str(HERE / "audit_deb.py"), str(deb)], capture_output=True, text=True)
        report = json.loads(proc.stdout)
        assert proc.returncode == 0 and report["pass"], {k: v for k, v in report["checks"].items() if v != "PASS"}
    again = build(tmp_path, "1")
    assert hashlib.sha256(again.read_bytes()).digest() == hashlib.sha256(debs["1"].read_bytes()).digest()


def test_lifecycle_install_reinstall_upgrade_remove_purge(debs: dict[str, Path], tmp_path: Path) -> None:
    sb = Sandbox(tmp_path)
    proc = sb.dpkg("-i", str(debs["1"]))
    assert proc.returncode == 0, proc.stderr
    assert "Status: install ok installed" in sb.status() and "Version: 0.4.0-1" in sb.status()
    assert "blux doctor" in proc.stdout  # postinst message
    report = sb.validate()
    assert report["pass"], {k: v for k, v in report["results"].items() if v["status"] == "FAIL"}
    expected = {
        "payload",
        "cli",
        "state_paths",
        "providers",
        "tui",
        "tools",
        "skills",
        "plugins",
        "mcp",
        "subagents",
        "cron",
        "gateway",
        "legacy_migration",
        "launcher",
    }
    assert set(report["results"]) == expected
    # User state that exists before upgrade/remove/purge must survive all of them.
    state = sb.home / ".local" / "state" / "blux"
    state.mkdir(parents=True)
    (state / "sessions.db").write_bytes(b"user data")
    assert sb.dpkg("-i", str(debs["1"])).returncode == 0  # reinstall
    proc = sb.dpkg("-i", str(debs["2"]))  # upgrade
    assert proc.returncode == 0 and "Version: 0.4.0-2" in sb.status()
    release = json.loads((sb.prefix / "share" / "blux" / "release.json").read_text())
    assert release["version"] == "0.4.0-2" and release["update"]["self_update"] is False
    assert sb.validate()["pass"]
    assert sb.dpkg("-r", "blux").returncode == 0
    assert not (sb.prefix / "bin" / "blux").exists() and (state / "sessions.db").read_bytes() == b"user data"
    proc = sb.dpkg("-P", "blux")
    assert proc.returncode == 0 and "state is kept" in proc.stdout
    assert "Package: blux" not in (sb.admin / "status").read_text() or "not-installed" in sb.status()
    assert (state / "sessions.db").read_bytes() == b"user data"
    assert not (sb.prefix / "lib" / "blux").exists()


def test_damaged_install_is_detected_and_repaired_by_reinstall(debs: dict[str, Path], tmp_path: Path) -> None:
    sb = Sandbox(tmp_path)
    assert sb.dpkg("-i", str(debs["2"])).returncode == 0
    (sb.prefix / "lib" / "blux" / "site" / "blux_runtime" / "sessions.py").unlink()
    broken = sb.validate()
    assert not broken["pass"] and broken["exit_code"] == 1
    assert sb.dpkg("-i", str(debs["2"])).returncode == 0
    assert sb.validate()["pass"]


def _termux_rpds(dest: Path) -> Path:
    fixture = json.loads((HERE / "vendor.lock.json").read_text())["termux_test_fixtures"]["python-rpds-py"]
    cache = Path(os.environ.get("BLUX_TERMUX_VENDOR", ROOT / "build" / "termux-vendor")) / Path(fixture["url"]).name
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(fixture["url"], timeout=120) as resp:
            cache.write_bytes(resp.read())
    assert hashlib.sha256(cache.read_bytes()).hexdigest() == fixture["sha256"]
    subprocess.run(["dpkg-deb", "-x", str(cache), str(dest / "x")], check=True)
    site = dest / "site"
    shutil.copytree(dest / "x" / PREFIX / "lib" / "python3.14" / "site-packages" / "rpds", site / "rpds")
    return site


@pytest.mark.skipif(not TERMUX_PY.exists(), reason="Termux python3 (Bionic) not on this machine")
def test_bionic_runtime_acceptance(debs: dict[str, Path]) -> None:
    """Runs the package payload under Termux's Bionic python3. This is evidence for the runtime layer only;
    installing through Termux's own APT on the device is a separate, manual acceptance step (ACCEPTANCE.md)."""
    # Android's linker only loads native code from permitted paths (/data/...), so stage in Termux's tmp.
    base = Path(tempfile.mkdtemp(prefix="blux-acceptance-", dir="/data/data/com.termux/files/usr/tmp"))
    try:
        sb = Sandbox(base)
        assert sb.dpkg("-i", str(debs["2"])).returncode == 0
        report = sb.validate(python=str(TERMUX_PY), extra_site=_termux_rpds(base))
        assert report["platform"] == "android" and report["machine"] == "aarch64"
        assert report["pass"], {k: v for k, v in report["results"].items() if v["status"] == "FAIL"}
        out = os.environ.get("BLUX_ACCEPTANCE_OUT")
        if out:
            Path(out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    finally:
        shutil.rmtree(base, ignore_errors=True)
