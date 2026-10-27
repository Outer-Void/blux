#!/usr/bin/env python3
"""Fail-closed corpus metadata and family-split preflight (not a content-safety replacement)."""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

REQUIRED = {"source_id", "origin", "revision", "license", "permission_evidence", "source_type", "family_id", "split", "data_class", "eligibility", "deduplication_state", "contamination_state", "content_sha256"}
SPLITS = {"train", "validation", "test", "holdout"}


def validate(records, lock):
    if set(lock) != {"version", "families", "digests"} or lock["version"] != 1:
        raise ValueError("invalid lock format")
    result = json.loads(json.dumps(lock))
    for family, split in result["families"].items():
        if not family or split not in SPLITS:
            raise ValueError("invalid family lock")
    seen = set()
    ids = set()
    for index, record in enumerate(records):
        if not REQUIRED.issubset(record):
            raise ValueError(f"record {index}: missing provenance")
        if any(not isinstance(record[k], str) or not record[k].strip() for k in REQUIRED):
            raise ValueError(f"record {index}: invalid provenance")
        if record["eligibility"] != "approved" or record["data_class"] not in {"public_permitted", "synthetic"}:
            raise ValueError(f"record {index}: ineligible data")
        if record["source_type"] in {"provider_hidden_reasoning", "private_user", "private_project", "participant_data"}:
            raise ValueError(f"record {index}: forbidden source")
        if record["deduplication_state"] != "cleared" or record["contamination_state"] != "cleared":
            raise ValueError(f"record {index}: uncleared corpus checks")
        digest = record["content_sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"record {index}: invalid digest")
        if "text" in record and hashlib.sha256(record["text"].encode()).hexdigest() != digest:
            raise ValueError(f"record {index}: digest mismatch")
        if record["source_id"] in ids or digest in seen:
            raise ValueError(f"record {index}: duplicate")
        ids.add(record["source_id"]); seen.add(digest)
        family, split = record["family_id"], record["split"]
        if split not in SPLITS:
            raise ValueError(f"record {index}: invalid split")
        if family in result["families"] and result["families"][family] != split:
            raise ValueError(f"record {index}: family split changed")
        if digest in result["digests"] and result["digests"][digest] != {"family_id":family,"split":split}:
            raise ValueError(f"record {index}: digest reassigned")
        result["families"][family] = split
        result["digests"][digest] = {"family_id":family,"split":split}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args=parser.parse_args()
    lock=json.loads(args.lock.read_text()) if args.lock else {"version":1,"families":{},"digests":{}}
    records=[json.loads(line) for line in args.records.read_text().splitlines() if line.strip()]
    result=validate(records,lock)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=args.out.parent, prefix=".corpus-lock-")
    try:
        with os.fdopen(fd,"w") as file:
            json.dump(result,file,indent=2,sort_keys=True);file.write("\n")
        os.replace(name,args.out)
    finally:
        if os.path.exists(name): os.unlink(name)
    print(json.dumps({"records":len(records),"families":len(result["families"]),"status":"preflight_passed_not_training_approval"}))


if __name__=="__main__": main()
