"""Run inside a dataset's pinned environment. Never emit expected outputs or protected text at audit."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from training.router.common import canonical, digest, read


def project_record(record: dict, config: dict, lock: dict, allowed: set[str], fitting: bool) -> dict:
    split = record["split"]
    if fitting and split not in ("train", "validation"):
        raise ValueError("protected split at fitting boundary")
    entry = lock["records"].get(record["record_id"], {})
    if entry.get("status") != "active" or entry.get("split") != split:
        raise ValueError("split lock mismatch")
    if record["schema"] != "blux.dataset-record" or record["schema_version"] != 1:
        raise ValueError("unsupported record schema")
    if record["dataset_version"] != "2.0.0" or record["dataset"] != config["dataset_id"]:
        raise ValueError("unsupported dataset version/identity")
    prov = record["provenance"]
    if (
        not prov["authors"]
        or prov["origin"] != "synthetic-authored"
        or prov["review"]["status"] != "approved"
        or prov["engines"] != config["engines"]
        or prov["license"] != config["license"]
        or not prov["generator"]
        or not prov["seed"]
    ):
        raise ValueError("incomplete/unapproved provenance")
    if fitting and (record.get("canary") or config["canary"] in canonical(record).decode()):
        raise ValueError("canary in training record")
    inp = record["input"]
    caps = (
        {step["capability"] for step in inp["steps"] if "capability" in step} if "steps" in inp else {inp["capability"]}
    )
    if not caps <= allowed:
        raise ValueError("unknown capability")
    return {
        "id": record["record_id"],
        "family": config["dataset_id"] + ":" + record["family"],
        "split": split,
        "text": record["task"]["instruction"],
        "label": next(iter(caps)) if len(caps) == 1 else None,
        "exclusion": None if len(caps) == 1 else "multi-capability-or-command-only",
        "canary": record.get("canary"),
        "dataset": config["dataset_id"],
    }


def main() -> None:
    root, mode, caps_file = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    sys.path.insert(0, str(root))
    from dstool.common import load_config
    from dstool.export import training_records
    from dstool.validate import read_all, read_split, validate

    config = load_config(root)
    if config.raw["version"] != "2.0.0" or config.raw["schema"] != "blux.dataset-config":
        raise ValueError("unsupported dataset configuration")
    capabilities = read(caps_file)
    allowed = set(capabilities[config.raw["adapter"]])
    lock = read(root / "splits.lock.json")
    if mode == "audit":
        report = validate(config)
        if not report["ok"]:
            raise ValueError("authoritative integrity validation failed: " + str(report["problems"]))
        records = read_all(config)  # mechanical integrity checks only; outputs never leave this process
        manifest = read(root / "data/manifest.json")
        pairs = [[r["record_id"], r["hashes"]["content_sha256"]] for r in sorted(records, key=lambda r: r["record_id"])]
        # The builder orders records globally by record_id.
        if digest(pairs) != manifest["records_sha256"]:
            raise ValueError("manifest records digest mismatch")
        fingerprints = []
        families = {}
        for r in records:
            project_record(r, config.raw, lock, allowed, r["split"] in ("train", "validation"))
            family = config.dataset_id + ":" + r["family"]
            if family in families and families[family] != r["split"]:
                raise ValueError("family crosses splits")
            families[family] = r["split"]
            text = re.sub(r"\d+", "#", r["task"]["instruction"].lower())
            fingerprints.append({"split": r["split"], "sha256": hashlib.sha256(text.encode()).hexdigest()})
        print(
            json.dumps(
                {
                    "report": report,
                    "manifest_sha256": hashlib.sha256((root / "data/manifest.json").read_bytes()).hexdigest(),
                    "records_sha256": manifest["records_sha256"],
                    "counts": manifest["splits"],
                    "canary": config.canary,
                    "fingerprints": fingerprints,
                }
            )
        )
    elif mode in ("train", "validation"):
        # Invoke the repository-supported protected-record gate before projecting away expected outputs.
        training_records(config, (mode,))
        print(json.dumps([project_record(r, config.raw, lock, allowed, True) for r in read_split(config, mode)]))
    elif mode in ("test", "adversarial", "regression", "holdout"):
        print(json.dumps([project_record(r, config.raw, lock, allowed, False) for r in read_split(config, mode)]))
    else:
        raise ValueError("unknown operation")


if __name__ == "__main__":
    main()
