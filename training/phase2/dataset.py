"""Fail-closed corpus verification and fit boundaries; no generated natural-language training corpus."""

from __future__ import annotations

import hashlib
import itertools
import re
from collections import Counter
from pathlib import Path

import jsonschema

from training.phase2.protocol import (
    ABSTAIN,
    DEVELOPMENT,
    MIN_ABSTAIN_REASON_FAMILIES,
    MIN_FAMILIES,
    PHENOMENA,
    PROTECTED,
    REASONS,
    SPLITS,
)
from training.phase2.schema import record_schema
from training.router.common import canonical, digest, read, write


def fingerprint(text: str) -> str:
    return " ".join(re.sub(r"\d+", "#", text.casefold()).split())


def shingles(text: str) -> set[str]:
    tokens = re.findall(r"\w+|#", fingerprint(text))
    pieces = [" ".join(tokens[i : i + 4]) for i in range(max(1, len(tokens) - 3))]
    return {hashlib.sha256(piece.encode()).hexdigest() for piece in pieces}


def signature(text: str) -> dict:
    return {"exact": digest(fingerprint(text)), "shingles": sorted(shingles(text))}


def similar(a: dict, b: dict) -> bool:
    left, right = set(a["shingles"]), set(b["shingles"])
    return a["exact"] == b["exact"] or (len(left & right) / len(left | right) >= 0.8 if left | right else True)


def check_row(row: dict, labels: list[str], canaries: list[str], *, allow_test: bool = False) -> None:
    jsonschema.Draft202012Validator(record_schema(labels)).validate(row)
    provenance = row["provenance"]
    if not allow_test and (provenance["test_only"] or provenance["method"] == "test-fixture"):
        raise ValueError("test fixture is not a production training example")
    if any(c.casefold() in canonical(row).decode().casefold() for c in canaries):
        raise ValueError("Phase-1 canary found")
    if (row["label"] == ABSTAIN) != (row["reason"] in REASONS):
        raise ValueError("OOD/ambiguity/unsupported label must be ABSTAIN")
    if any(p in REASONS for p in row["phenomena"]) and row["label"] != ABSTAIN:
        raise ValueError("OOD or unsupported example mislabeled as capability")
    if row["split"] in PROTECTED and provenance["method"] == "source-record":
        raise ValueError("protected Phase-2 families must be fresh owner-authored seeds")
    if provenance["method"] == "deterministic-template" and not row["template_family"]:
        raise ValueError("template generation requires a template family")
    if any(name.casefold() in ("codex", "openai", "claude", "anthropic") for name in provenance["authors"]):
        raise ValueError("model-authored prose is not owner-authored seed material")


def verify_rows(
    rows: list[dict], labels: list[str], canaries: list[str], quarantine: list[dict], *, allow_test: bool = False
) -> dict:
    if len(set(labels)) != len(labels) or ABSTAIN in labels or not labels:
        raise ValueError("invalid authoritative capability set")
    ids, groups, signatures = set(), {}, []
    for row in rows:
        check_row(row, labels, canaries, allow_test=allow_test)
        if row["id"] in ids:
            raise ValueError("duplicate record ID")
        ids.add(row["id"])
        for kind in ("semantic_family", "template_family", "paraphrase_family"):
            if row[kind] is None:
                continue
            key = kind, row[kind]
            if key in groups and groups[key] != row["split"]:
                raise ValueError("semantic/template/paraphrase family crosses splits")
            groups[key] = row["split"]
        sig = signature(row["text"])
        if any(similar(sig, historical) for historical in quarantine):
            raise ValueError("Phase-1 protected text or detectable paraphrase reused")
        signatures.append(sig)
    for i, j in itertools.combinations(range(len(rows)), 2):
        if similar(signatures[i], signatures[j]):
            if rows[i]["split"] != rows[j]["split"]:
                raise ValueError("duplicate or detectable paraphrase crosses splits")
            if rows[i]["semantic_family"] != rows[j]["semantic_family"]:
                raise ValueError("near-duplicate variants pretend to be distinct semantic families")
    return coverage(rows, labels)


def coverage(rows: list[dict], labels: list[str]) -> dict:
    problems, counts = [], {}
    for split in SPLITS:
        selected = [r for r in rows if r["split"] == split]
        classes = {}
        for label in [*labels, ABSTAIN]:
            rr = [r for r in selected if r["label"] == label]
            nfamilies = len({r["semantic_family"] for r in rr})
            classes[label] = {"records": len(rr), "semantic_families": nfamilies}
            if label != ABSTAIN and nfamilies < MIN_FAMILIES[split]:
                problems.append(f"{split}:{label}: {nfamilies} families < {MIN_FAMILIES[split]}")
        reasons = {}
        for reason in REASONS:
            rr = [r for r in selected if r["reason"] == reason]
            nf = len({r["semantic_family"] for r in rr})
            reasons[reason] = {"records": len(rr), "semantic_families": nf}
            if nf < MIN_ABSTAIN_REASON_FAMILIES[split]:
                problems.append(f"{split}:ABSTAIN:{reason}: {nf} families < {MIN_ABSTAIN_REASON_FAMILIES[split]}")
        counts[split] = {
            "records": len(selected),
            "classes": classes,
            "abstention_reasons": reasons,
            "semantic_families": len({r["semantic_family"] for r in selected}),
            "template_families": len({r["template_family"] for r in selected if r["template_family"]}),
            "phenomena": dict(Counter(p for r in selected for p in r["phenomena"])),
        }
    dev_phenomena = {p for r in rows if r["split"] in DEVELOPMENT for p in r["phenomena"]}
    if set(PHENOMENA) - dev_phenomena:
        problems.append("missing development phenomena: " + ",".join(sorted(set(PHENOMENA) - dev_phenomena)))
    return {
        "ready": not problems,
        "counts": counts,
        "problems": problems,
        "record_count": len(rows),
        "content_sha256": digest(sorted(rows, key=lambda r: r["id"])),
    }


def fit_rows(
    rows: list[dict], split: str, labels: list[str], canaries: list[str], *, allow_test: bool = False
) -> list[dict]:
    if split not in DEVELOPMENT or not rows:
        raise ValueError("protected split/empty fitting input")
    for row in rows:
        check_row(row, labels, canaries, allow_test=allow_test)
        if row["split"] != split:
            raise ValueError("protected or wrong-role record at fitting boundary")
    return rows


def build(
    rows: list[dict],
    out: Path,
    labels: list[str],
    canaries: list[str],
    quarantine: list[dict],
    lineage: dict,
    *,
    allow_test: bool = False,
) -> dict:
    report = verify_rows(rows, labels, canaries, quarantine, allow_test=allow_test)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("dataset output already exists; immutable version cannot be overwritten")
    files, locks = {}, {}
    for split in SPLITS:
        selected = sorted((r for r in rows if r["split"] == split), key=lambda r: r["id"])
        text = b"".join(canonical(r) + b"\n" for r in selected)
        path = out / f"{split}.jsonl"
        path.write_bytes(text)
        files[path.name] = hashlib.sha256(text).hexdigest()
        for row in selected:
            locks[row["id"]] = {"split": split, "semantic_family": row["semantic_family"], "sha256": digest(row)}
    write(out / "splits.lock.json", locks)
    files["splits.lock.json"] = hashlib.sha256((out / "splits.lock.json").read_bytes()).hexdigest()
    manifest = {
        "schema": "blux.routing-dataset",
        "version": 1,
        "dataset_id": "blux-routing-0.2",
        "labels": labels,
        "files": files,
        "lineage": lineage,
        "report": report,
        "test_only": allow_test,
    }
    write(out / "manifest.json", manifest)
    return manifest


def load_verified(
    out: Path,
    expected_digest: str,
    labels: list[str],
    canaries: list[str],
    quarantine: list[dict],
    *,
    allow_test: bool = False,
) -> tuple[list[dict], dict]:
    manifest = read(out / "manifest.json")
    if digest(manifest) != expected_digest:
        raise ValueError("stale dataset digest")
    if (manifest["schema"], manifest["version"], manifest["dataset_id"]) != (
        "blux.routing-dataset",
        1,
        "blux-routing-0.2",
    ):
        raise ValueError("unsupported dataset schema/version")
    if manifest["labels"] != labels or (manifest["test_only"] and not allow_test):
        raise ValueError("dataset capability/model mismatch or test-only corpus")
    if set(manifest["files"]) != {f"{s}.jsonl" for s in SPLITS} | {"splits.lock.json"}:
        raise ValueError("unexpected dataset files")
    for name, sha in manifest["files"].items():
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != sha:
            raise ValueError("dataset file checksum mismatch")
    import json

    rows = [json.loads(line) for split in SPLITS for line in (out / f"{split}.jsonl").read_text().splitlines()]
    lock = read(out / "splits.lock.json")
    if set(lock) != {r["id"] for r in rows}:
        raise ValueError("split lock ID mismatch")
    for r in rows:
        if lock[r["id"]] != {"split": r["split"], "semantic_family": r["semantic_family"], "sha256": digest(r)}:
            raise ValueError("split lock mismatch")
    report = verify_rows(rows, labels, canaries, quarantine, allow_test=allow_test)
    # Canonical record order is independent of source seed ordering.
    if report != manifest["report"]:
        raise ValueError("dataset coverage/abstention report mismatch")
    return rows, manifest
