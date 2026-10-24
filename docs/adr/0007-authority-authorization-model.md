# ADR-0007: Authority and authorization model

- Status: Accepted (Stage 01, 2026-10-24)

## Decision
**Authority comes only from the policy document the runtime was started with.** Nothing in a request grants it.

- `actor` (`user | agent | service`, id) is a *claim* used as policy input. An actor id such as `root` changes nothing.
- `blux.policy` is **default deny**. The schema fixes `default_effect` to `deny`.
- A matching **deny** rule wins over any allow, regardless of order.
- An allow rule applies only if:
  - the capability matches it (exact id or a `namespace.*` glob);
  - `actor_types` matches, if set;
  - the capability's `side_effects` are at or below `max_side_effects`, if set;
  - its `grants` cover every `required_permission`.
- **Destructive capabilities can only be allowed by a rule naming the exact `capability_id`.** A glob never authorizes `destructive`.
- Every decision (`effect`, `policy_id`, `rule_id`, `reason`) is written to the receipt.
- Handlers receive an `ExecutionContext` without policy or credentials.

This follows the Stage 00 BLHermes security note. Device-destructive operations (e.g. VoidForge flashing) must
additionally sit behind a separately authenticated service. BLUX policy approval is necessary but never
sufficient for them.

The CLI's built-in reference policy allows exactly `echo.test`.
