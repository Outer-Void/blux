# ADR-0015: BLUX-owned Termux packaging and Android qualification

- Status: Accepted (Stage 15, 2026-10-26)

## Context
BLHermes shipped through its own package manager (PM), bundled bionic CPython/Node/uv, a self-updater and
Nous-hosted install feeds. Stage 15 replaces all of that with packaging BLUX owns.

## Decision
1. **Package identity.** One Termux package, `blux`, for Termux architecture `aarch64`.
   - **Maintainer.** Outer-Void.
   - **Dependencies.** `python (>= 3.14)` and `python-rpds-py`, from the Termux main repository.
   - **Bundled.** BLUX (runtime, CLI and SDK), BluxGPT (pinned commit, standard library only) and the
     pure-Python dependencies pinned by sha256 in `packaging/termux/vendor.lock.json`.
   - **Excluded.** No Node, uv, CPython or native wheels are bundled, and no BLHermes or Hermes code.
2. **Paths.**

   | What | Where |
   |---|---|
   | Payload | `$PREFIX/lib/blux/site` |
   | Launchers | `$PREFIX/bin/{blux,bluxgpt}`, run as `python3 -I`; the site is found relative to the launcher |
   | Docs and licences | `$PREFIX/share/doc/blux` |
   | Release metadata | `$PREFIX/share/blux/release.json` |
   | User state | `$HOME/.local/state/blux` (`BLUX_HOME`) and `$HOME/.config/blux` |

   User state is never in the package.
3. **Lifecycle.** The maintainer scripts use no network and never migrate anything. `prerm` removes only
   the bytecode caches inside the package's own directory. `postrm` never deletes user state, not even on
   purge. Legacy BLHermes state is only mentioned, and imported by hand with `blux migrate hermes`
   (ADR-0013).
4. **Updates.** APT owns updates (`release.json`: `"owner": "apt", "self_update": false`). BLUX has no
   updater and contacts no feed. The BLUX APT repository is not published yet.
5. **Build.** `build_deb.py` is reproducible (fixed order, modes, owners and `SOURCE_DATE_EPOCH`). It
   runs on aarch64 Debian/glibc with `dpkg-deb`; no native compilation is needed. `audit_deb.py`
   checks the artifact itself.
6. **Qualification.** `validate_installed.py` exercises every runtime area against an installed prefix,
   under whichever interpreter runs it.
   - **Sandbox.** Tests drive the real `.deb` through dpkg (`--root`, a throwaway root) for install,
     reinstall, upgrade, remove, purge and recovery.
   - **Bionic.** The Bionic runtime layer is validated with Termux's own python3 on a real Android
     kernel.
   - **Device.** Installing through Termux APT on a device stays **BLOCKED** until the owner runs the
     steps in `packaging/termux/ACCEPTANCE.md`. It is never marked PASS from sandbox or source testing.

## Consequences
- **Retired.** BLHermes PM, Nix, Docker and installer infrastructure (BLH-033/034/035/025) are not used by
  BLUX.
- **Termux packaging.** BLH-033 becomes MIGRATED for the package and runtime layers, with device
  installation as its named blocker.
