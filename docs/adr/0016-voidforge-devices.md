# ADR-0016: VoidForge device capabilities

Status: accepted (Stage 22, 2026-10-26)

## Context

Android device work belongs to VoidForge, a standalone, members-only workstation with its own policy,
write barrier, local authorization and journal. BLUX users and agents want to ask for device actions
without BLUX becoming a second device authority.

## Decision

- `blux_runtime.devices.voidforge` registers VoidForge's 17 `device.*` capabilities as BLUX tools. They
  come from VoidForge's catalog, vendored byte-for-byte from `Outer-Void/voidforge`
  `integration/protocol/v1/capabilities.json`; a test checks it when both checkouts are present.
- Each call sends one `voidforge-integration/1` request over VoidForge's local socket and returns VoidForge's
  receipt.
- BLUX can only ask:
  - device tools are labelled `device_operation`, so no hook can mutate them;
  - input schemas reject unknown fields;
  - the handler refuses authorization-shaped fields;
  - VoidForge refuses them again.
- Writes are confirmed by the person at the VoidForge device. BLUX approvals, YOLO and policy never
  substitute for that.
- BLUX policy (`device_policy()`) narrows who may ask, never widens it:

  | Side effects | Who may ask |
  |---|---|
  | read | users, agents, services |
  | write | users, agents |
  | privileged or destructive | users only |
- `blux agent --tools device.list,...` can use device tools; agents run as actor `agent`.

## Consequences

- VoidForge disabled or absent: `device.*` calls fail with `reason: voidforge_unavailable`; nothing else
  in BLUX changes.
- BLUX absent: VoidForge is unaffected.
- `device.boot.patch` returns VoidForge's `NOT_PROVIDED`: VoidForge never patches boot images.
