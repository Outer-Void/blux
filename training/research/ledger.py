"""Append-only immutable experiment identities and explicit evidence-consumption metadata."""

from __future__ import annotations

from pathlib import Path

from training.router.common import canonical, digest


def append(path: Path, experiment: dict) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    verify(entries)
    if any(r["experiment"]["id"] == experiment["id"] for r in entries):
        raise ValueError("experiment identity already closed; history cannot be overwritten")
    record = {"experiment": experiment, "previous_sha256": digest(entries[-1]) if entries else None}
    verify([*entries, record])
    with path.open("ab") as f:
        f.write(canonical(record) + b"\n")


def verify(entries: list[dict]) -> None:
    previous, ids = None, set()
    consumed = set()
    for entry in entries:
        if entry["previous_sha256"] != previous or entry["experiment"]["id"] in ids:
            raise ValueError("experiment ledger tampering/duplicate identity")
        exp = entry["experiment"]
        ids.add(exp["id"])
        for dataset_digest in exp["protected_evidence_consumed"]:
            if dataset_digest in consumed:
                raise ValueError("protected evidence is historical and cannot be reused")
            consumed.add(dataset_digest)
        previous = digest(entry)


def load(path: Path) -> list[dict]:
    import json

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    verify(entries)
    return entries
