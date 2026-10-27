"""python -m blux_training build|validate. Exit codes: 0 ok, 1 validation problems, 2 usage error."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from blux_training import pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blux_training", description="Build or validate a BLUX trajectory dataset.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="run every trajectory source under INPUTS through the pipeline")
    b.add_argument("inputs", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--version", required=True, help="dataset version written to the manifest")
    b.add_argument("--lock", type=Path, help="previous split-lock.json; assignments in it never change")
    b.add_argument(
        "--protected", type=Path, action="append", default=[], help="evaluation JSONL to protect (repeatable)"
    )
    b.add_argument(
        "--private-terms", type=Path, help="untracked file, one private term per line, redacted wherever found"
    )
    v = sub.add_parser("validate", help="re-check a built dataset directory")
    v.add_argument("dataset", type=Path)
    args = parser.parse_args(argv)

    if args.command == "build":
        if not args.inputs.is_dir():
            print(f"blux_training: {args.inputs} is not a directory", file=sys.stderr)
            return 2
        terms = (
            tuple(t.strip() for t in args.private_terms.read_text().splitlines() if t.strip())
            if args.private_terms
            else ()
        )
        result = pipeline.build(
            args.inputs,
            args.out,
            version=args.version,
            lock_path=args.lock,
            protected=args.protected,
            private_terms=terms,
        )
        print(
            json.dumps({"counts": result["manifest"]["counts"], "summary": result["report"]["summary"]}, sort_keys=True)
        )
        problems = pipeline.validate(args.out)
    else:
        problems = pipeline.validate(args.dataset)
        if not problems:
            print("dataset OK")
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
