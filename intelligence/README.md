# intelligence/

The direct BLUX intelligence surface: how a user or agent asks BLUX itself to reason, plan or generate.

Stage 01 deliberately ships **no model providers and no prompts**. What exists:

- `blux.model` contract (`contracts/v1/model.schema.json`) to describe models;
- `ModelProvider` protocol and `ModelRegistry` (`runtime/blux_runtime/interfaces.py`, `registry.py`);
- the rule that models are reached **only through capabilities** (ADR-0003), so every model call gets schemas, policy and a receipt.

The existing provider layers (blhermes-agent, blux-gpt, resolver; see `docs/ecosystem/capabilities.json`) are
consolidated in later stages. They are not reimplemented here.
