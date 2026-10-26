"""Audit a built BLUX Termux .deb (Stage 15).

    python packaging/termux/audit_deb.py dist/blux_<version>_aarch64.deb [--json]

Checks the artifact itself, not the source tree:
- **Identity.** Package identity and control fields, Termux architecture, and dependencies.
- **Layout.** Every payload path sits under the Termux prefix. No compiled caches, no setuid, and file
  modes are correct.
- **Integrity.** `md5sums` matches the payload. `release.json` payload hash and version match.
- **Licences.** The BLUX licence, third-party notices, and a licence file for every vendored component
  are present.
- **No foreign identity.** No BLHermes/Hermes/Nous identity, no update feeds and no telemetry endpoints.
- **No secrets.** Nothing credential-shaped in any file.
- **Maintainer scripts.** No network, no deletion of user state, no automatic migration.

Exit 0 when every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PREFIX = "data/data/com.termux/files/usr"
HERE = Path(__file__).resolve().parent
SECRET = re.compile(
    r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
# Exact, documented fakes that are allowed to look like credentials (each proves scrubbing in its component).
KNOWN_FAKES = {"sk-proj-MOCKMOCKMOCKMOCKMOCKMOCKMOCK0000": "bluxgpt mock provider 'leak' behaviour"}
FOREIGN = re.compile(r"nousresearch\.com|hermes-agent|\bhermes_cli\b|\bNOUS_|portal\.nous|shared[_-]metrics|otlp", re.I)
SCRIPT_FORBIDDEN = re.compile(r"\b(curl|wget|pip\s+install|rm\s+-rf?\s+[\"']?\$?\{?HOME|migrate\s+hermes\s+--apply)\b")


def run(deb: Path) -> dict[str, object]:
    checks: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root, ctrl = Path(tmp) / "root", Path(tmp) / "ctrl"
        subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
        subprocess.run(["dpkg-deb", "-e", str(deb), str(ctrl)], check=True)
        control = dict(
            line.split(": ", 1)
            for line in (ctrl / "control").read_text().splitlines()
            if ": " in line and not line.startswith(" ")
        )
        lock = json.loads((HERE / "vendor.lock.json").read_text())

        def ok(name: str, cond: bool, detail: str = "") -> None:
            checks[name] = "PASS" if cond else f"FAIL {detail}".strip()

        ok("control.package", control.get("Package") == "blux", control.get("Package", ""))
        ok("control.architecture", control.get("Architecture") == "aarch64")
        ok("control.maintainer", control.get("Maintainer", "").startswith("Outer-Void <"))
        ok("control.depends", control.get("Depends") == "python (>= 3.14), python-rpds-py", control.get("Depends", ""))
        files = sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink())
        rel = [str(p.relative_to(root)) for p in files]
        ok(
            "layout.prefix",
            all(r.startswith(PREFIX + "/") for r in rel),
            next((r for r in rel if not r.startswith(PREFIX)), ""),
        )
        ok("layout.no_pycache", not any("__pycache__" in r or r.endswith(".pyc") for r in rel))
        bad_modes = [
            r
            for p, r in zip(files, rel, strict=True)
            if p.stat().st_mode & 0o6000 or (p.stat().st_mode & 0o777) != (0o755 if "/bin/" in r else 0o644)
        ]
        ok("layout.modes", not bad_modes, ",".join(bad_modes[:3]))
        sums = {}
        for line in (ctrl / "md5sums").read_text().splitlines():
            digest, path = line.split("  ", 1)
            sums[path] = digest
        ok("integrity.md5sums", sums == {r: hashlib.md5((root / r).read_bytes()).hexdigest() for r in rel})
        release = json.loads((root / PREFIX / "share" / "blux" / "release.json").read_text())
        payload = hashlib.sha256()
        for p, r in zip(files, rel, strict=True):
            if not r.endswith("share/blux/release.json"):
                payload.update(r.encode() + b"\0" + p.read_bytes())
        ok("integrity.release_payload", release["payload_sha256"] == payload.hexdigest())
        ok("integrity.release_version", release["version"] == control.get("Version"))
        ok("update.apt_only", release["update"] == {**release["update"], "owner": "apt", "self_update": False})
        doc = root / PREFIX / "share" / "doc" / "blux"
        ok("licence.blux", (doc / "LICENSE").read_text().startswith("BLUX Proprietary License"))
        ok("licence.notices", (doc / "THIRD_PARTY_NOTICES.md").is_file())
        lic = {p.name for p in (doc / "licenses").iterdir()}
        missing = [i["name"] for i in lock["python"] if not any(n.startswith(i["name"] + "-") for n in lic)]
        ok("licence.vendored", not missing and "bluxgpt-LICENSE" in lic, ",".join(missing))
        texts = {
            r: (root / r).read_text(encoding="utf-8", errors="replace")
            for r in rel
            if not r.startswith(f"{PREFIX}/share/doc/")
        }
        foreign = sorted(
            r
            for r, t in texts.items()
            if FOREIGN.search(t)
            and "/blux_runtime/compat/" not in r
            and not r.endswith(("security/paths.py", "migrate.py"))
        )
        ok("identity.no_foreign", not foreign, ",".join(foreign[:5]))
        secrets = sorted(r for r, t in texts.items() if any(m.group(0) not in KNOWN_FAKES for m in SECRET.finditer(t)))
        ok("secrets.none", not secrets, ",".join(secrets[:5]))
        scripts = {s: (ctrl / s).read_text() for s in ("postinst", "prerm", "postrm") if (ctrl / s).exists()}
        ok("scripts.safe", all(not SCRIPT_FORBIDDEN.search(s) for s in scripts.values()), ",".join(scripts))
        ok("scripts.preserve_state", "never deleted" in scripts.get("postrm", ""))
        ok("bin.launchers", {"blux", "bluxgpt"} <= {Path(r).name for r in rel if "/bin/" in r})
    return {
        "contract": "blux.deb-audit",
        "deb": deb.name,
        "sha256": hashlib.sha256(deb.read_bytes()).hexdigest(),
        "checks": checks,
        "pass": all(v == "PASS" for v in checks.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deb", type=Path)
    args = ap.parse_args()
    report = run(args.deb)
    sys.stdout.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
