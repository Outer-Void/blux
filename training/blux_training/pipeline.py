"""Trajectory -> dataset pipeline. Deterministic: the same inputs, lock and version give identical bytes.

Order (each stage sees only what the previous one passed):
  source schema -> eligibility -> hidden reasoning -> secrets -> licences -> receipt validation
  -> personal-data redaction -> normalization -> canonical record -> record schema -> quality
  -> exact and near-duplicate removal -> contamination against protected evaluation sets
  -> split assignment (lock-protected) -> versioned dataset (records, manifest, report, lock)

Outputs never include rejected content: the report lists each input file, its outcome and the reasons.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from blux_sdk import schemas as contract_schemas
from jsonschema import Draft202012Validator

from blux_training import PIPELINE_VERSION, SCHEMA_VERSION, gates

SPLITS = ("train", "validation", "test", "holdout", "regression")
PROTECTED_SPLITS = ("test", "holdout", "regression")  # never reassigned to train or validation
NEAR_DUPLICATE = 0.9  # shingle Jaccard similarity at or above this is a duplicate
CONTAMINATION = 0.8  # share of a record's input found in one protected evaluation item that excludes it
MIN_QUALITY = 0.5
CONTENT_FIELDS = (
    "input",
    "intent",
    "context_summary",
    "reasoning_structure",
    "planned_actions",
    "tool_calls",
    "tool_results",
    "constructed_artifacts",
    "validation_results",
    "repair_iterations",
    "final_result",
    "accept_reject",
)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


@cache
def schema(name: str) -> Draft202012Validator:
    doc = json.loads(resources.files("blux_training").joinpath(f"schemas/{name}.v1.schema.json").read_text())
    return Draft202012Validator(doc)


def schema_errors(name: str, doc: Any) -> list[str]:
    return [
        f"{name}: {'/'.join(map(str, e.path)) or '(root)'}: {e.message[:160]}" for e in schema(name).iter_errors(doc)
    ]


def normalize(value: Any) -> Any:
    """Unicode NFC, newline and trailing-whitespace normalization of every string; structure unchanged."""
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def shingles(text: str, n: int = 3) -> set[str]:
    words = [w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w]
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_of(value: Any) -> str:
    """The words of a value: its string leaves (and numbers), without JSON syntax or key names."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(text_of(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(text_of(v) for v in value)
    return "" if value is None or isinstance(value, bool) else str(value)


def containment(part: set[str], whole: set[str]) -> float:
    """Share of `part` found in `whole`: high when a candidate's task appears inside a protected item."""
    return len(part & whole) / len(part) if part else 0.0


def learning_text(record: dict[str, Any]) -> str:
    return " ".join((canonical(record["input"]), str(record["intent"]), canonical(record["final_result"])))


def quality(t: dict[str, Any]) -> dict[str, float]:
    optional = (
        "context_summary",
        "reasoning_structure",
        "planned_actions",
        "tool_calls",
        "tool_results",
        "validation_results",
    )
    completeness = sum(1 for k in optional if t.get(k)) / len(optional)
    checks = t.get("validation_results") or []
    validation = sum(1 for c in checks if c["passed"]) / len(checks) if checks else 0.5
    repair = 1 / (1 + int(t.get("repair_iterations") or 0))
    overall = round((completeness + validation + repair) / 3, 4)
    return {
        "completeness": round(completeness, 4),
        "validation": round(validation, 4),
        "repair": round(repair, 4),
        "overall": overall,
    }


@dataclass
class Outcome:
    source: str
    status: str = "accepted"  # accepted | sanitized | rejected
    reasons: list[str] = field(default_factory=list)
    record_id: str | None = None
    split: str | None = None

    def reject(self, *reasons: str) -> None:
        self.status = "rejected"
        self.reasons.extend(reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "source": self.source,
                "status": self.status,
                "reasons": self.reasons,
                "record_id": self.record_id,
                "split": self.split,
            }.items()
            if v not in (None, [])
        }


def to_record(
    source: dict[str, Any], source_bytes: bytes, private_terms: tuple[str, ...], outcome: Outcome
) -> dict[str, Any] | None:
    errors = schema_errors("source", source)
    if errors:
        outcome.reject(*errors[:5])
        return None
    for gate in (
        gates.eligibility(source),
        gates.hidden_reasoning(source["trajectory"]),
        gates.secrets(source),
        gates.licensing(source),
    ):
        outcome.reasons.extend(gate.reasons)
    if outcome.reasons:
        outcome.status = "rejected"
        return None

    receipts = source["receipts"]
    for i, receipt in enumerate(receipts):
        problems = contract_schemas.schema_errors("receipt", receipt)
        if problems:
            outcome.reject(f"receipt[{i}]: not a valid blux.receipt ({len(problems)} problem(s))")
    if outcome.status == "rejected":
        return None
    root = next((r for r in receipts if r.get("parent_operation_id") is None), receipts[0] if receipts else None)

    trajectory, pii = gates.personal_data(normalize(source["trajectory"]), private_terms)
    if pii.redactions:
        outcome.status = "sanitized"
        outcome.reasons.extend(f"redacted: {kind}" for kind in pii.redactions)

    data_class = source["eligibility"]["data_class"]
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_type": source["source_type"],
        "source_project": source["source_project"],
        "operation_id": root["operation_id"] if root else None,
        "parent_operation_id": root["parent_operation_id"] if root else None,
        "input": trajectory["input"],
        "intent": trajectory["intent"],
        "context_summary": trajectory.get("context_summary", ""),
        "reasoning_structure": trajectory.get("reasoning_structure", {}),
        "planned_actions": trajectory.get("planned_actions", []),
        "tool_calls": trajectory.get("tool_calls", []),
        "tool_results": trajectory.get("tool_results", []),
        "constructed_artifacts": trajectory.get("constructed_artifacts", []),
        "validation_results": trajectory.get("validation_results", []),
        "repair_iterations": trajectory.get("repair_iterations", 0),
        "final_result": trajectory["final_result"],
        "accept_reject": trajectory["accept_reject"],
        "quality_scores": quality(trajectory),
        "safety_class": source.get("safety_class", "benign"),
        "privacy_class": "redacted" if pii.redactions else ("synthetic" if data_class == "synthetic" else "public"),
        "provenance": {
            "source_sha256": sha256(source_bytes),
            "receipt_ids": sorted(r["receipt_id"] for r in receipts),
            "eligibility": source["eligibility"],
            "pipeline_version": PIPELINE_VERSION,
            **({"redactions": pii.redactions} if pii.redactions else {}),
        },
        "licenses": sorted(source["licenses"], key=canonical),
        "model_versions": source["model_versions"],
        "engine_versions": source["engine_versions"],
    }
    content = sha256(canonical({k: record[k] for k in CONTENT_FIELDS}))
    record["record_id"] = "trj_" + sha256(content + source["source_project"])[:24]
    record["hashes"] = {"content_sha256": content, "record_sha256": "0" * 64}
    record["dataset_split"] = "train"  # placeholder until split assignment
    return record


def seal(record: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in record.items() if k != "hashes"}
    record["hashes"]["record_sha256"] = sha256(canonical(body))
    return record


def assign_split(record: dict[str, Any], hint: str | None, lock: dict[str, str]) -> str:
    locked = lock.get(record["record_id"])
    if locked:
        return locked  # a record never moves between splits across versions
    if hint in PROTECTED_SPLITS:
        return hint
    bucket = int(record["hashes"]["content_sha256"][:8], 16) % 10
    return "train" if bucket < 8 else ("validation" if bucket == 8 else "test")


def load_protected(paths: list[Path]) -> list[set[str]]:
    """Evaluation items to protect (e.g. a dataset's holdout/test/regression JSONL). Each line's text is used."""
    out = []
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    out.append(shingles(text_of(json.loads(line))))
                except ValueError:
                    out.append(shingles(line))
    return out


def build(
    inputs: Path,
    out: Path,
    *,
    version: str,
    lock_path: Path | None = None,
    protected: list[Path] | None = None,
    private_terms: tuple[str, ...] = (),
) -> dict[str, Any]:
    files = sorted(p for p in inputs.rglob("*.json") if p.is_file())
    lock: dict[str, str] = json.loads(lock_path.read_text())["assignments"] if lock_path and lock_path.exists() else {}
    protected_sets = load_protected(protected or [])
    outcomes, kept, hints = [], [], {}
    seen_content: dict[str, str] = {}
    kept_shingles: list[tuple[str, set[str]]] = []

    for path in files:
        raw = path.read_bytes()
        outcome = Outcome(source=str(path.relative_to(inputs)))
        outcomes.append(outcome)
        try:
            source = json.loads(raw)
        except ValueError:
            outcome.reject("source: not valid JSON")
            continue
        record = to_record(source, raw, private_terms, outcome) if isinstance(source, dict) else None
        if record is None:
            if outcome.status != "rejected":
                outcome.reject("source: not an object")
            continue
        problems = schema_errors("record", record)
        if problems:
            outcome.reject(*problems[:5])
            continue
        if record["quality_scores"]["overall"] < MIN_QUALITY:
            outcome.reject(f"quality: overall {record['quality_scores']['overall']} is below {MIN_QUALITY}")
            continue
        content = record["hashes"]["content_sha256"]
        if content in seen_content:
            outcome.reject(f"duplicate: same content as {seen_content[content]}")
            continue
        sh = shingles(learning_text(record))
        near = next((rid for rid, other in kept_shingles if similarity(sh, other) >= NEAR_DUPLICATE), None)
        if near:
            outcome.reject(f"duplicate: near-duplicate of {near}")
            continue
        task = shingles(text_of(record["input"]))
        contaminated = max((containment(task, p) for p in protected_sets), default=0.0)
        if contaminated >= CONTAMINATION:
            outcome.reject(f"contamination: {contaminated:.0%} of the input appears in a protected evaluation item")
            continue
        seen_content[content] = record["record_id"]
        kept_shingles.append((record["record_id"], sh))
        hints[record["record_id"]] = source.get("split_hint")
        outcome.record_id = record["record_id"]
        kept.append(record)

    by_split: dict[str, list[dict[str, Any]]] = {s: [] for s in SPLITS}
    new_lock = dict(lock)
    for record in kept:
        split = assign_split(record, hints[record["record_id"]], lock)
        record["dataset_split"] = split
        new_lock[record["record_id"]] = split
        by_split[split].append(seal(record))
    for o in outcomes:
        if o.record_id:
            o.split = new_lock[o.record_id]

    out.mkdir(parents=True, exist_ok=True)
    (out / "splits").mkdir(exist_ok=True)
    split_digests = {}
    for split, records in by_split.items():
        body = "".join(canonical(r) + "\n" for r in sorted(records, key=lambda r: r["record_id"]))
        (out / "splits" / f"{split}.jsonl").write_text(body)
        split_digests[split] = sha256(body)
    lock_doc = {"kind": "blux.training.split-lock", "version": "1", "assignments": dict(sorted(new_lock.items()))}
    (out / "split-lock.json").write_text(json.dumps(lock_doc, indent=1, sort_keys=True) + "\n")
    report = {
        "kind": "blux.training.report",
        "version": "1",
        "dataset_version": version,
        "outcomes": [o.to_dict() for o in outcomes],
        "summary": {s: sum(1 for o in outcomes if o.status == s) for s in ("accepted", "sanitized", "rejected")},
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    manifest = {
        "kind": "blux.training.dataset",
        "version": "1",
        "dataset_version": version,
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "counts": {s: len(r) for s, r in by_split.items()},
        "split_sha256": split_digests,
        "inputs_sha256": sha256("".join(sha256(p.read_bytes()) for p in files)),
        "lock_sha256": sha256(json.dumps(lock_doc, indent=1, sort_keys=True) + "\n"),
        "protected_sources": len(protected_sets),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return {"manifest": manifest, "report": report}


def validate(dataset: Path) -> list[str]:
    """Re-check a built dataset from its files alone: digests, record schema, split placement, and that no
    secret, hidden reasoning or unredacted personal data is present."""
    problems: list[str] = []
    manifest = json.loads((dataset / "manifest.json").read_text())
    lock = json.loads((dataset / "split-lock.json").read_text())["assignments"]
    ids: set[str] = set()
    for split in SPLITS:
        body = (dataset / "splits" / f"{split}.jsonl").read_text()
        if sha256(body) != manifest["split_sha256"][split]:
            problems.append(f"{split}: digest does not match the manifest")
        for n, line in enumerate(body.splitlines(), 1):
            record = json.loads(line)
            where = f"{split}:{n}"
            problems += [f"{where}: {e}" for e in schema_errors("record", record)]
            if record.get("dataset_split") != split or lock.get(record.get("record_id")) != split:
                problems.append(f"{where}: split does not match the record or the lock")
            if record.get("record_id") in ids:
                problems.append(f"{where}: duplicate record_id")
            ids.add(record.get("record_id"))
            body_hash = sha256(canonical({k: v for k, v in record.items() if k != "hashes"}))
            if record.get("hashes", {}).get("record_sha256") != body_hash:
                problems.append(f"{where}: record_sha256 mismatch")
            for gate in (gates.secrets(record), gates.hidden_reasoning(record)):
                problems += [f"{where}: {r}" for r in gate.reasons]
            _, pii = gates.personal_data(record)
            problems += [f"{where}: unredacted personal data ({k})" for k in pii.redactions]
        if len(body.splitlines()) != manifest["counts"][split]:
            problems.append(f"{split}: count does not match the manifest")
    return problems
