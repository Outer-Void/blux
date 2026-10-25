"""Copy the canonical schema trees into the SDK package, or verify they match (--check).

Trees: Contract v1 (contracts/v1) and Model API v1 (contracts/model-api/v1). Each tree has its own
CHECKSUMS.sha256 lock; editing a schema without a reviewed lock update fails --check.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "sdk" / "python" / "src" / "blux_sdk" / "schemas"
TREES = (
    (ROOT / "contracts" / "v1", PKG / "v1", "ADR-0005"),
    (ROOT / "contracts" / "model-api" / "v1", PKG / "model_api" / "v1", "ADR-0010"),
)


def checksums(source: Path = ROOT / "contracts" / "v1") -> str:
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(source.glob("*.schema.json"))]
    return "\n".join(lines) + "\n"


def drift() -> list[str]:
    problems = []
    for source, target, adr in TREES:
        rel = source.relative_to(ROOT)
        src = {p.name: p.read_bytes() for p in source.glob("*.schema.json")}
        dst = {p.name: p.read_bytes() for p in target.glob("*.schema.json")} if target.is_dir() else {}
        problems += [f"{rel}: missing in package: {n}" for n in sorted(src.keys() - dst.keys())]
        problems += [f"{rel}: extra in package: {n}" for n in sorted(dst.keys() - src.keys())]
        problems += [f"{rel}: differs: {n}" for n in sorted(src.keys() & dst.keys()) if src[n] != dst[n]]
        lock = source / "CHECKSUMS.sha256"
        if not lock.exists() or lock.read_text(encoding="utf-8") != checksums(source):
            problems.append(f"{rel}/{lock.name} is stale: schemas changed without an {adr} review (--write-lock)")
    return problems


def sync() -> None:
    for source, target, _adr in TREES:
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.glob("*.schema.json"):
            if not (source / stale.name).exists():
                stale.unlink()
        for p in sorted(source.glob("*.schema.json")):
            shutil.copyfile(p, target / p.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the packaged copy differs")
    parser.add_argument("--write-lock", action="store_true", help="record schema checksums (only after review)")
    args = parser.parse_args()
    if args.write_lock:
        for source, _target, _adr in TREES:
            (source / "CHECKSUMS.sha256").write_text(checksums(source), encoding="utf-8")
        return 0
    if args.check:
        problems = drift()
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
