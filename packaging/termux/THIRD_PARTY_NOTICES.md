# Third-party components in the BLUX Termux package

BLUX itself is under the BLUX Proprietary License (`LICENSE`). The package bundles these components. They
keep their own licences, and each licence text is reproduced in `licenses/`.

| Component | Version | Licence | Copyright | Licence file |
|---|---|---|---|---|
| jsonschema | 4.26.0 | MIT | Julian Berman | `licenses/jsonschema-COPYING` |
| referencing | 0.37.0 | MIT | Julian Berman | `licenses/referencing-COPYING` |
| jsonschema-specifications | 2025.9.1 | MIT | Julian Berman | `licenses/jsonschema-specifications-COPYING` |
| attrs | 26.1.0 | MIT | Hynek Schlawack and the attrs contributors | `licenses/attrs-LICENSE` |
| PyYAML (pure-Python part) | 6.0.3 | MIT | Ingy döt Net, Kirill Simonov | `licenses/PyYAML-LICENSE` |
| BluxGPT | pinned commit | BLUX Proprietary License | Outer-Void / Justadudeinspace (JADIS) | `licenses/bluxgpt-LICENSE` |

From Termux, not bundled: `python` (PSF-2.0) and `python-rpds-py` (MIT), installed by APT as dependencies.
No code from BLHermes or Hermes Agent is in this package.
