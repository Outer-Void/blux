"""Build the BLUX Termux package (Stage 15, ADR-0015).

    python packaging/termux/build_deb.py --out dist/ [--vendor-dir DIR] [--revision 1] [--bluxgpt-src ../blux-gpt]

Output: blux_<version>-<revision>_aarch64.deb, a Termux package installed under
/data/data/com.termux/files/usr. It contains BLUX (runtime, CLI, SDK), BluxGPT (the Model API backend,
standard library only) and the pure-Python dependencies pinned in vendor.lock.json, each verified by
sha256 and shipped with its licence. Native dependencies come from Termux: `python` and `python-rpds-py`.

Reproducible: file order, modes, owners and timestamps (SOURCE_DATE_EPOCH, default: the source commit
time) are fixed. The package never updates itself (APT owns updates), never touches user state in its
maintainer scripts, and never migrates legacy state automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PREFIX = "data/data/com.termux/files/usr"
SITE = f"{PREFIX}/lib/blux/site"
LOCK = HERE / "vendor.lock.json"
PACKAGE = "blux"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def fetch(lock: dict, vendor_dir: Path) -> None:
    """Download any missing pinned artifact (build-time network only), then verify every hash."""
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for item in lock["python"]:
        target = vendor_dir / item["file"]
        if not target.exists():
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--quiet",
                    "--no-deps",
                    "--dest",
                    str(vendor_dir),
                    *(["--no-binary=:all:"] if item["file"].endswith(".tar.gz") else ["--only-binary=:all:"]),
                    f"{item['name']}=={item['version']}",
                ],
                check=True,
            )
        if sha256(target) != item["sha256"]:
            raise SystemExit(f"{target.name}: sha256 mismatch (expected {item['sha256']})")


def extract(item: dict, archive: Path, site: Path, licenses: Path) -> None:
    keep = tuple(item["packages"])
    name = item["name"]
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                top = info.filename.split("/")[0]
                if top in keep and not info.is_dir():
                    out = site / info.filename
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zf.read(info))
                elif top.endswith(".dist-info") and not info.is_dir():
                    if "/licenses/" in info.filename:
                        (licenses / f"{name}-{Path(info.filename).name}").write_bytes(zf.read(info))
                    if (
                        Path(info.filename).name in ("METADATA", "WHEEL", "top_level.txt")
                        or "/licenses/" in info.filename
                    ):
                        out = site / info.filename  # metadata, so importlib.metadata works for vendored packages
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(zf.read(info))
    else:  # sdist: copy the pure-Python package directory (e.g. PyYAML's lib/yaml) and its LICENSE
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                parts = member.name.split("/")
                if (
                    len(parts) > 2
                    and parts[1] == "lib"
                    and parts[2] in keep
                    and member.isfile()
                    and member.name.endswith(".py")
                ):
                    out = site / "/".join(parts[2:])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    data = tf.extractfile(member)
                    assert data is not None
                    out.write_bytes(data.read())
                elif len(parts) == 2 and parts[1] in ("LICENSE", "LICENSE.txt") and member.isfile():
                    data = tf.extractfile(member)
                    assert data is not None
                    (licenses / f"{name}-LICENSE").write_bytes(data.read())
        meta = site / f"{name}-{item['version']}.dist-info"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {item['version']}\nLicense: {item['license']}\n"
            "Summary: pure-Python part vendored by the BLUX Termux package\n"
        )


def copy_tree(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc" or path.is_dir():
            continue
        out = dst / path.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out)


LAUNCHER = """#!/data/data/com.termux/files/usr/bin/python3 -I
# {name}: BLUX launcher. -I isolates from PYTHONPATH and user site-packages; the bundled site is found
# relative to this file, so the package also runs from an extracted or test root.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib" / "blux" / "site"))
from {module} import {func}

sys.exit({func}())
"""


def build(out_dir: Path, vendor_dir: Path, revision: str, bluxgpt_src: Path) -> Path:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    fetch(lock, vendor_dir)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    commit = git("rev-parse", "HEAD")
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or git("log", "-1", "--format=%ct"))
    gpt = lock["bluxgpt"]
    if git("rev-parse", f"{gpt['commit']}^{{commit}}", cwd=bluxgpt_src) != gpt["commit"]:
        raise SystemExit(f"bluxgpt commit {gpt['commit']} not available in {bluxgpt_src}")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "root"
        site = stage / SITE
        doc = stage / PREFIX / "share" / "doc" / PACKAGE
        licenses = doc / "licenses"
        licenses.mkdir(parents=True)
        for pkg, src in (
            ("blux_runtime", ROOT / "runtime" / "blux_runtime"),
            ("blux_cli", ROOT / "cli" / "blux_cli"),
            ("blux_sdk", ROOT / "sdk" / "python" / "src" / "blux_sdk"),
        ):
            copy_tree(src, site / pkg)
        archive = subprocess.run(
            ["git", "-C", str(bluxgpt_src), "archive", "--format=tar", gpt["commit"], "bluxgpt", "LICENSE"],
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
            for member in tf.getmembers():
                if member.isfile():
                    data = tf.extractfile(member)
                    assert data is not None
                    dest = (
                        (site / member.name) if member.name.startswith("bluxgpt/") else (licenses / "bluxgpt-LICENSE")
                    )
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data.read())
        for item in lock["python"]:
            extract(item, vendor_dir / item["file"], site, licenses)
        bindir = stage / PREFIX / "bin"
        bindir.mkdir(parents=True)
        for name, module, func in (("blux", "blux_cli.main", "main"), ("bluxgpt", "bluxgpt.cli", "main")):
            (bindir / name).write_text(LAUNCHER.format(name=name, module=module, func=func))
        shutil.copyfile(ROOT / "LICENSE", doc / "LICENSE")
        shutil.copyfile(HERE / "THIRD_PARTY_NOTICES.md", doc / "THIRD_PARTY_NOTICES.md")
        (doc / "copyright").write_text(
            "BLUX: Copyright (c) 2026 Outer-Void / Justadudeinspace (JADIS). All rights reserved.\n"
            "BLUX Proprietary License (LICENSE). Third-party components: THIRD_PARTY_NOTICES.md and licenses/.\n"
            "Contact: outervoid.blux@gmail.com (main), theoutervoid@outlook.com (fallback)\n"
        )
        payload = hashlib.sha256()
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            payload.update(str(path.relative_to(stage)).encode() + b"\0" + path.read_bytes())
        share = stage / PREFIX / "share" / PACKAGE
        share.mkdir(parents=True)
        release = {
            "contract": "blux.release",
            "package": PACKAGE,
            "version": f"{version}-{revision}",
            "source_commit": commit,
            "bluxgpt_commit": gpt["commit"],
            "built_at_epoch": epoch,
            "payload_sha256": payload.hexdigest(),
            "update": {
                "owner": "apt",
                "self_update": False,
                "channel": "Outer-Void Termux APT repository (not yet published)",
            },
            "state": {
                "blux_home": "$HOME/.local/state/blux",
                "config": "$HOME/.config/blux/config.toml",
                "preserved_on_remove_and_purge": True,
            },
            "legacy_import": "blux migrate hermes (manual, dry run by default; expires 2027-04-30)",
        }
        (share / "release.json").write_text(json.dumps(release, indent=1, sort_keys=True) + "\n")
        debian = stage / "DEBIAN"
        debian.mkdir()
        size_kb = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024 + 1
        (debian / "control").write_text(
            f"Package: {PACKAGE}\nVersion: {version}-{revision}\nArchitecture: aarch64\n"
            "Maintainer: Outer-Void <outervoid.blux@gmail.com>\n"
            "Depends: python (>= 3.14), python-rpds-py\nSection: utils\nPriority: optional\n"
            f"Installed-Size: {size_kb}\nHomepage: https://github.com/Outer-Void/blux\n"
            "Description: BLUX direct intelligence CLI and capability runtime\n"
            " BLUX (blux ask/chat/doctor) with the BluxGPT Model API backend. Updates come from APT only.\n"
        )
        for script in ("postinst", "prerm", "postrm"):
            shutil.copyfile(HERE / "maintainer" / script, debian / script)
        files = sorted(p for p in stage.rglob("*") if p.is_file() and "DEBIAN" not in p.relative_to(stage).parts)
        (debian / "md5sums").write_text(
            "".join(f"{hashlib.md5(p.read_bytes()).hexdigest()}  {p.relative_to(stage)}\n" for p in files)
        )
        for path in sorted(stage.rglob("*")):
            executable = (
                path.is_dir()
                or path.parent.name == "bin"
                or (path.parent.name == "DEBIAN" and path.name in ("postinst", "prerm", "postrm"))
            )
            path.chmod(0o755 if executable else 0o644)
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        os.utime(stage, (epoch, epoch))
        out_dir.mkdir(parents=True, exist_ok=True)
        deb = out_dir / f"{PACKAGE}_{version}-{revision}_aarch64.deb"
        env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(deb)],
            check=True,
            capture_output=True,
            env=env,
        )
    return deb


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=ROOT / "dist")
    p.add_argument("--vendor-dir", type=Path, default=ROOT / "build" / "termux-vendor")
    p.add_argument("--revision", default="1")
    p.add_argument("--bluxgpt-src", type=Path, default=ROOT.parent / "blux-gpt")
    args = p.parse_args()
    deb = build(args.out, args.vendor_dir, args.revision, args.bluxgpt_src)
    print(f"{deb} {sha256(deb)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
