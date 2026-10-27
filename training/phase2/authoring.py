"""Owner-supplied seed support. The implementation contains no natural-language training templates."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
from pathlib import Path

from training.phase2.dataset import check_row
from training.phase2.schema import record_schema
from training.router.common import read, write


def owner_seeds(path: Path, approval: dict, capabilities: list[str], canaries: list[str]) -> list[dict]:
    if (
        approval.get("seed_file_sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
        or approval.get("approved") is not True
        or not approval.get("reviewer")
        or not approval.get("training_rights")
    ):
        raise ValueError("missing owner approval, training rights or matching seed-file digest")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for r in rows:
        check_row(r, capabilities, canaries)
        if r["provenance"]["method"] not in ("owner-authored", "deterministic-template"):
            raise ValueError("new seed authoring requires explicit owner-authored provenance")
        if (
            r["provenance"]["reviewer"] != approval["reviewer"]
            or r["provenance"]["rights"] != approval["training_rights"]
        ):
            raise ValueError("seed approval and per-record provenance disagree")
    return rows


def expand_template(owner_record: dict, variables: dict[str, list[str]], *, maximum: int = 100) -> list[dict]:
    """Only caller-supplied rights-cleared strings. Variants retain one semantic/template family and split."""
    if owner_record["provenance"]["method"] != "owner-authored" or not owner_record["provenance"]["approved"]:
        raise ValueError("template must be owner-authored and reviewed")
    count = math_product(len(v) for v in variables.values())
    if count > maximum or not count:
        raise ValueError("template expansion limit or empty variables")
    result = []
    for i, values in enumerate(itertools.product(*variables.values())):
        r = copy.deepcopy(owner_record)
        r["text"] = r["text"].format_map(dict(zip(variables, values, strict=True)))
        r["id"] += ":variant-" + str(i)
        r["template_family"] = r["template_family"] or r["semantic_family"]
        r["paraphrase_family"] = r["paraphrase_family"] or r["semantic_family"]
        r["provenance"]["method"] = "deterministic-template"
        result.append(r)
    return result


def math_product(values) -> int:
    result = 1
    for value in values:
        result *= value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--schema-output", type=Path, required=True)
    args = parser.parse_args()
    groups = read(args.capabilities)
    write(args.schema_output, record_schema(sorted(c for caps in groups.values() for c in caps)))


if __name__ == "__main__":
    main()
