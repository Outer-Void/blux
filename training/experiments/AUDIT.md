# Phase 1 audit (before model development)

All five checkouts were clean and at fetched `origin/main` on 2026-10-27. BLUX was switched from the
provisioned `work` branch to `main`, tracking `origin/main`, without changing its commit.

| Repository | Starting main commit |
|---|---|
| blux | 560ea4dfc20c13b26f7660af86ec18308b91db39 |
| blux-coga | 3dc86fa7f791a1ffee88438b549651e4f04c9c3c |
| blux-ca | 7cb953a10313276d299170b718f983638be051c2 |
| blux-coga-dataset | 9331f73f7dfb3166969d35a12ec0413f77340973 |
| blux-ca-dataset | 662ec33cf0f02b4166cc3f7ed26366ac6de14388 |

BLUX Model API v1 (`sdk/python/src/blux_sdk/model_api.py`) exposes four logical models:
`blux-main`, `blux-fast`, `blux-reason`, `blux-code`. Its `ModelRouter` filters declared provider routes
by capability, locality, allow/deny lists and hook vetoes, then applies explicit provider fallback.
That router chooses a **model provider**, not a CogA/cA capability. Its defaults use the external
BluxGPT subprocess backend. The SDK's streaming/tools/etc. model capabilities are a different namespace
from engine capability IDs.

`ModelProvider` describes a contract model; `CapabilityRegistry` validates capability descriptors and
rejects duplicates. `Runtime` checks input schemas, resolves capabilities, evaluates default-deny policy,
checks permissions, respects hook vetoes, invokes handlers, and emits results/receipts. Model-call artifacts
carry safe routing metadata linked to the execution context; they do not store prompts or credentials.
BLUX System owns durable receipts. `registry/capabilities.yaml` is the reference echo index; the engines'
`.blux/capabilities.yaml` files contain their federated capabilities and permissions. Engine source
(`blux_coga.reason.CAPABILITIES`, `blux_ca.construction.api.CAPABILITIES`) agrees with those descriptors.

`training/` was reserved and had no implementation. This experiment adds offline tooling there, without
changing the SDK, executor, provider router, registry, CLI, engine code, or their authority boundaries.
BLUX remains intelligence/integration infrastructure; `blux` is direct intelligence and `ax` the ecosystem
control plane. Outer Void remains federated. CogA computes deterministic reasoning artifacts. cA produces
construction/repair changesets with `certified: false`; DAT remains independent validation authority.

The two v2.0.0 datasets contain synthetic-authored, approved proprietary records, with record schema v1,
engine commit provenance, immutable family/split locks, content/input/expected hashes, privacy and
hidden-reasoning scans, contamination checks, protected canaries, and guarded training exports.
The dataset toolkits are distinct sibling modules with the same Python name (`dstool`); invoke each
inside its own pinned environment. Both `dstool validate` and byte-identical `dstool build --check` passed
before model work. Mechanical validation checks all records; protected expected outputs were not manually
inspected for feature design. Only train/validation instructions and input capability IDs were examined.
No engine output, dataset category, seed path, ID, family, input schema structure, or expected output is a
model feature.

cA records can be multi-step. Use the sole distinct explicit capability if exactly one exists; exclude
multi-capability or command-only records rather than inventing a single label. This means the experiment
predicts a capability requested by the record, not task validity, a sequence of operations, or certification.
Rejected/adversarial requests still retain that requested capability as routing ground truth. A correct
route on such an example does not establish safe execution or correct refusal.

Training covers five cA and seven CogA capabilities. `repo.patch` and `artifact.validate` have no
single-label training support; the artifact explicitly lists them as unsupported. Current source provides
14 capabilities total. This missing support is a predeclared integration-gate failure even if other scores
are high. We will still finish the frozen experiment and report protected evaluations without tuning.
