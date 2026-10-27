"""Explicit offline advisor, never a normal-routing feature flag."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from training.router.common import read
from training.router.inference import advisory_or_fallback


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--capabilities", type=Path, required=True, help="authoritative capabilities.json from the audit"
    )
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    capabilities = read(args.capabilities)
    allowed = {c for group in capabilities.values() for c in group}
    result = advisory_or_fallback(
        args.artifact,
        args.task,
        allowed,
        lambda: {
            "decision": "ABSTAIN",
            "fallback": "existing-provider-path",
            "authority": "advisory-only",
            "certified": False,
        },
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
