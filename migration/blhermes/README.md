# BLHermes absorption: forensic inventory and parity matrix (Stage 11)

BLHermes (`Outer-Void/blhermes-agent`) is a fork of Hermes Agent by Nous Research (MIT). BLUX absorbs
the behaviour it keeps in later stages. This directory is the evidence base for that work. Nothing here
deletes, renames or copies upstream code. The fork stays intact. See [UPSTREAM-NOTICE.md](UPSTREAM-NOTICE.md)
for the MIT notice that covers the derived data.

| File | What it is |
|---|---|
| `inventory.py` | Read-only scanner. It reads a clean fork checkout at one commit and writes `inventory.json` (`--check` verifies it). |
| `inventory.json` | Every tracked Hermes/BLHermes/Nous reference, by category, with locations and one classification and the rule that decided it. |
| `parity-matrix.json` | One row per feature or subsystem: source locations, target BLUX module, tests, migration state, provenance and licence requirement, network dependency, blocker and evidence plan. It is validated by `parity-matrix.schema.json`. |
| `characterization/probe.py` | Probes that run the fork's own code (isolated, no network, throwaway home) and print its behaviour. |
| `characterization/golden/` | The recorded behaviour that every BLUX port must reproduce, or deliberately improve on with an explicit test. |
| `characterization/requirements.txt` | Exact pins for the probe interpreter (from the fork's `pyproject.toml`). |

## Inventoried commit
`Outer-Void/blhermes-agent` at `ac51a44f1f73452446ddea8e51c58e9e5e9098fc` contains 20,709 tracked files.

**Files compared with the upstream merge base `3fa571a5`:**
- unchanged upstream: 16,456
- rebrand-only: 4,198 (text only: BLHermes in place of Hermes)
- substantively modified by the fork: 36
- added by the fork: 19
- deleted: 0

The per-subsystem figures are recorded in Outer-Void/credits `LIN-001`.

## Classes
| Class | Meaning |
|---|---|
| `REMOVE` | An upstream-only hook BLUX does not keep: the Nous Portal account, billing and inference service, upstream update feeds, and telemetry and shared-metrics export. |
| `REPLACE` | A Hermes-branded identifier, package, installer or doc link that BLUX re-implements under its own name. |
| `MIGRATE` | Behaviour BLUX keeps. It moves to the target module named in the matrix. |
| `TEMPORARY_COMPATIBILITY` | Kept working during migration, then retired: the `hermes` entry points, `HERMES_HOME`, `~/.hermes` and credential import. |
| `UPSTREAM_ATTRIBUTION` | Must stay: the upstream licence, nested licences, contributor records and links to the upstream repository. |

## Inventory at a glance
The inventory has 9,574 items: 7,571 MIGRATE, 1,294 REPLACE, 279 REMOVE, 225 TEMPORARY_COMPATIBILITY and
205 UPSTREAM_ATTRIBUTION.

**Network hooks.** Each item is one `file :: hook` pair. There are 74 REMOVE items: telemetry export, update
checks and the Nous Portal. The other 374 are MIGRATE: each is the network use of a retained feature, which
its BLUX capability must declare.

**Personal data.** E-mail addresses are scrubbed. `contributors/` (upstream contributor records) appears
only as a count.

**URLs.** URLs from tests and docs are inventoried only when they point at Hermes or Nous. The rest are
counted under `omitted`.

## Characterized behaviour
These behaviours are recorded in goldens:
- state paths and profiles
- dangerous-command detection
- session approvals and YOLO
- file-write safety
- secret redaction
- child-process environment scrubbing
- the provider registry
- toolsets and the tool registry
- slash commands
- the session store
- cron schedule parsing

**One recorded behaviour is a gap BLUX must not reproduce.** The upstream child-environment scrub passes
`AWS_SECRET_ACCESS_KEY` and `DATABASE_URL` to child processes (BLH-006).

## Running the checks
```sh
pytest tests/migration                                   # inventory and matrix consistency (no fork needed)
BLHERMES_SOURCE=~/ov/blhermes-agent \
BLHERMES_PYTHON=/path/to/venv/bin/python \
pytest tests/migration                                   # also fork locations, no-deletion, and characterization
BLHERMES_INVENTORY_CHECK=1 ...                           # also rescan the fork (minutes)
python migration/blhermes/inventory.py --source ~/ov/blhermes-agent   # regenerate after the fork moves
```
The probe interpreter is any Python 3.11 or newer with `characterization/requirements.txt` installed.
