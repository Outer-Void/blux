"""Copy canonical contracts/v1 schemas into the SDK package, or verify they match (--check)."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "contracts" / "v1"
TARGET = ROOT / "sdk" / "python" / "src" / "blux_sdk" / "schemas" / "v1"


def drift() -> list[str]:
    src = {p.name: p.read_bytes() for p in SOURCE.glob("*.schema.json")}
    dst = {p.name: p.read_bytes() for p in TARGET.glob("*.schema.json")} if TARGET.is_dir() else {}
    problems = [f"missing in package: {n}" for n in sorted(src.keys() - dst.keys())]
    problems += [f"extra in package: {n}" for n in sorted(dst.keys() - src.keys())]
    problems += [f"differs: {n}" for n in sorted(src.keys() & dst.keys()) if src[n] != dst[n]]
    if not LOCK.exists() or LOCK.read_text(encoding="utf-8") != checksums():
        problems.append(f"{LOCK.name} is stale: schemas changed without an ADR-0005 review (--write-lock)")
    return problems


def sync() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for stale in TARGET.glob("*.schema.json"):
        if not (SOURCE / stale.name).exists():
            stale.unlink()
    for p in sorted(SOURCE.glob("*.schema.json")):
        shutil.copyfile(p, TARGET / p.name)


LOCK = SOURCE / "CHECKSUMS.sha256"


def checksums() -> str:
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(SOURCE.glob("*.schema.json"))]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the packaged copy differs")
    parser.add_argument(
        "--write-lock", action="store_true", help="record schema checksums (only after an ADR-0005 review)"
    )
    args = parser.parse_args()
    if args.write_lock:
        LOCK.write_text(checksums(), encoding="utf-8")
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
