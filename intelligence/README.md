# intelligence/

The direct BLUX intelligence surface: how a user or agent asks BLUX itself to reason, plan or generate.

Since Stage 10 the surface is the `blux` command itself ([docs/cli.md](../docs/cli.md), ADR-0011):
`blux "..."`, piped input, `blux chat`, `blux doctor`. It reaches logical models only through the Model
API (`model.generate`, ADR-0010) and never manages projects, devices or the ecosystem (that is `ax`).

Underneath:

- `blux.model` contract (`contracts/v1/model.schema.json`) to describe models;
- `ModelProvider` protocol and `ModelRegistry` (`runtime/blux_runtime/interfaces.py`, `registry.py`);
- the rule that models are reached **only through capabilities** (ADR-0003), so every model call gets schemas, policy and a receipt.

- the Model API router and its backends: BluxGPT is the reference backend and owns provider transport.

BLUX still ships no prompts of its own and no provider SDKs.
