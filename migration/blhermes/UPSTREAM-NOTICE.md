# Upstream notice: Hermes Agent (MIT)

The files in this directory are derived from **Hermes Agent** (https://github.com/NousResearch/hermes-agent),
through the Outer-Void fork **blhermes-agent** at commit `ac51a44f1f73452446ddea8e51c58e9e5e9098fc`:

- `inventory.json` lists upstream identifiers, paths, URLs and file names;
- `characterization/golden/*.json` record outputs of upstream code (command names, provider table,
  approval descriptions, redaction results and similar);
- `characterization/probe.py` imports upstream code at run time from a separate checkout. No upstream source
  code is copied into BLUX.

That material is used under the MIT License of Hermes Agent, reproduced verbatim below. Credits:
Outer-Void/credits `proj:hermes-agent`, lineage `LIN-001` (fork) and `LIN-008` (this derived data).

```text
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
