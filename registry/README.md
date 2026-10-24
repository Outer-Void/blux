# registry/

`capabilities.yaml` is the federated capability index: a `blux.capability-set` listing every capability known to
the federation and the repository that provides it.

Stage 01 lists only the built-in `echo.test`. `tests/runtime/test_policy_registry.py` fails if it drifts from the
runtime descriptor.
