# `.blux/` specification (Contract v1)

A repository joins the BLUX federation by committing a `.blux/` directory at its root:

```
.blux/
  manifest.yaml       blux.project         identity, role, pointers
  capabilities.yaml   blux.capability-set  capabilities this repo provides
  policy.yaml         blux.policy          default-deny rules for running them
```

## Rules
- All three are YAML encodings of Contract v1 documents. They validate against `contracts/v1/{project,capability-set,policy}.schema.json`.
- **Quote `contract_version`** (`"1.0"`). Unquoted `1.0` is a YAML float and fails with `MALFORMED_CONTRACT_VERSION`.
- `manifest.capabilities` and `manifest.policy` are bare file names inside `.blux/`. Paths and `..` are rejected by schema.
- Duplicate keys, non-string keys, dates, NaN and custom tags are rejected (`MALFORMED_INPUT`).
- Files are untrusted metadata. Loading them never executes code, installs anything or resolves `provider.entrypoint`.
- Each capability's `side_effects` and `required_permissions` must be honest. Policy can only constrain what is declared.

## Validate
```sh
blux project path/to/repo        # one JSON document on stdout; exit 0 valid, 1 invalid
```

```python
from blux_sdk import dot_blux

project = dot_blux.load("path/to/repo")
```

## Run
```sh
blux capabilities --project path/to/repo                    # built-ins plus the repo's capabilities
blux run CAPABILITY --project path/to/repo --input '{...}'  # policy: the repo's policy.yaml unless --policy
```
`provider.kind: subprocess` follows ADR-0009. Kind `python` is not implemented by the reference runtime.

Examples: `examples/project/.blux/` (standalone, read-only capability) and this repository's own `.blux/`.
