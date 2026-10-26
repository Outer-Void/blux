# BLUX Termux package: acceptance ledger

Status per evidence layer. Source-only or mock testing never counts as a real-device PASS.

| Layer | Status | Evidence |
|---|---|---|
| Artifact exists and audits clean | **PASS** | `audit_deb.py` checks package identity, Termux prefix, modes, md5sums, release metadata, licences, and that there is no foreign identity, secret or unsafe maintainer script (`tests/packaging/test_termux_package.py::test_artifact_exists_is_reproducible_and_passes_the_audit`) |
| Reproducible build | **PASS** | Two builds of the same commit are byte-identical (same test) |
| Lifecycle under dpkg | **PASS** (sandbox) | Covers clean install, reinstall, upgrade 0.4.0-1 → 0.4.0-2, remove and purge. User state is preserved through remove and purge, and a damaged install is detected and repaired by reinstalling. Runs the real `.deb` and the real maintainer scripts under Termux's Bionic `sh`, using Debian `dpkg --root` into a throwaway root (`test_lifecycle_*`, `test_damaged_install_*`). |
| Runtime areas (glibc) | **PASS** | `validate_installed.py` covers the installed payload, CLI, state paths, providers (bundled BluxGPT), TUI (`blux chat`), tools, skills, plugins, MCP, subagents, cron, gateway and legacy migration. |
| Bionic runtime | **PASS** (runtime layer) | Termux `python3.14` (Bionic, `sys.platform == "android"`, aarch64, on a real Android kernel) runs the package payload. Every area passes; the launcher check is skipped in the test root. Recorded in `acceptance/bionic-runtime-2026-10-26.json`. `python-rpds-py` comes from the Termux repository, extracted only. |
| Installed through Termux APT on a device | **BLOCKED** | See below |
| Upgrade from a published APT release | **BLOCKED** | No BLUX APT repository is published yet; the update channel in `release.json` says so |

## Exact remaining requirement (real-device installation)
This needs to run in a native Termux session (not PRoot), on an aarch64 Android device, by the owner. It
installs software into the live Termux prefix, so it is not done automatically.

```sh
pkg install python python-rpds-py                 # Termux dependencies
dpkg -i blux_0.4.0-2_aarch64.deb                  # or: apt install ./blux_0.4.0-2_aarch64.deb
blux version && blux doctor
python3 -I validate_installed.py --prefix "$PREFIX"   # from packaging/termux/; expects "pass": true, launcher PASS
blux "Reply with the single word: ok"             # one real provider call (BluxGPT credentials configured)
dpkg -r blux && ls ~/.local/state/blux            # state preserved
```

Record the device model, Android version, Termux version, package sha256, the validator JSON and the
exit codes in `acceptance/`. Only then may this row become PASS.
