"""Forensic inventory of BLHermes (fork of NousResearch/hermes-agent) for its absorption into BLUX (Stage 11).

Reads the tracked files of a clean blhermes-agent checkout at one commit and writes `inventory.json`: every
Hermes/BLHermes/Nous reference this stage tracks, each with locations and one classification:

  REMOVE                   upstream-only hook BLUX does not keep (vendor feeds, portal, telemetry export)
  REPLACE                  Hermes-branded identifier that BLUX re-implements under its own name
  MIGRATE                  behaviour BLUX keeps; it moves to a target BLUX module (see parity-matrix.json)
  TEMPORARY_COMPATIBILITY  kept working during migration (aliases, old paths and env vars), then retired
  UPSTREAM_ATTRIBUTION     must stay: licence, notices, credits, links that attribute the upstream work

Classification is by ordered rules (`RULES`); each item records the rule that decided it. Nothing is deleted:
this tool only reads. Personal data is never emitted: e-mail addresses are scrubbed and `contributors/`
is summarized by count.

    python migration/blhermes/inventory.py --source ~/ov/blhermes-agent            # write inventory.json
    python migration/blhermes/inventory.py --source ~/ov/blhermes-agent --check    # fail if it drifted
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "inventory.json"
MAX_LOCATIONS = 12
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CLASSES = ("REMOVE", "REPLACE", "MIGRATE", "TEMPORARY_COMPATIBILITY", "UPSTREAM_ATTRIBUTION")

CATEGORIES = {
    "module": "first-party Python module or package (import name)",
    "package_id": "distribution, npm, Debian, Nix, container or application identifier",
    "command": "executable entry point, `hermes` subcommand or slash command",
    "env_var": "environment variable read by code (or a branded name that code reads from the environment)",
    "identifier": "branded constant/name that is not read from the environment",
    "path": "config/state/install path literal",
    "url": "URL literal (query strings dropped)",
    "updater": "update channel, feed or self-update code",
    "network_hook": "one outbound-network or telemetry hook in one runtime file (`file :: hook`)",
    "provider": "model/inference provider identifier",
    "tool": "agent tool registered with the tool registry",
    "toolset": "named toolset",
    "skill": "bundled or optional skill (SKILL.md)",
    "plugin": "plugin (plugin.yaml) or plugin-catalog entry",
    "mcp": "MCP server/client surface or optional MCP definition",
    "cron": "cron/scheduler module",
    "gateway": "messaging gateway module or platform adapter",
    "tui": "TUI or TUI-gateway module",
    "session": "session store/behaviour module",
    "memory": "memory/state provider module",
    "subagent": "subagent/delegation module",
    "approval": "approval/YOLO path module",
    "credential": "credential/auth storage or routing module",
    "termux": "Termux packaging/build file",
    "android": "file with Android/Bionic-specific behaviour",
    "install": "install/update/uninstall script or module",
    "attribution": "upstream licence, notice or credit material",
}


# ----------------------------------------------------------------------------------------------- files


@dataclass
class Source:
    root: Path
    commit: str
    tree: str
    files: list[str]
    text: dict[str, str] = field(default_factory=dict)

    def read(self, path: str) -> str:
        if path not in self.text:
            try:
                self.text[path] = (self.root / path).read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                self.text[path] = ""
        return self.text[path]


def git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git argv
        ["git", "-C", str(root), *args],  # noqa: S607 - git from PATH, as every caller expects
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def open_source(root: Path) -> Source:
    if git(root, "status", "--porcelain", "--untracked-files=no").strip():
        sys.exit(f"{root}: tracked files are modified; inventory needs a clean checkout")
    commit = git(root, "rev-parse", "HEAD").strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").strip()
    files = git(root, "ls-files", "-z").split("\0")
    return Source(root, commit, tree, sorted(f for f in files if f))


BINARY = re.compile(
    r"\.(png|jpe?g|gif|webp|ico|icns|svg|woff2?|ttf|otf|mp3|wav|ogg|mp4|zip|gz|tgz|whl|so|jar|pdf|lock)$", re.I
)


def scope(path: str) -> str:
    p = path.lower()
    if p.startswith("contributors/"):
        return "attribution"
    if (
        p.startswith(("tests/", "tests-js/", "evals/"))
        or "/tests/" in p
        or "/__tests__/" in p
        or "/e2e/" in p
        or re.search(r"(^|/)(test_[^/]*|[^/]*_test\.py|[^/]*\.(spec|test)\.[jt]sx?)$", p)
    ):
        return "test"
    if p.startswith(("website/", "docs/")) or p.endswith((".md", ".mdx", ".rst", ".txt")):
        return "docs"
    if p.startswith(("locales/", "plugin-catalog/", "assets/")):
        return "data"
    if p.startswith(("scripts/", "nix/", "docker/", ".github/", "pm/")) or p in (
        "Dockerfile",
        "justfile",
        "flake.nix",
        "setup.py",
        "setup-hermes.sh",
        "setup-hermes.ps1",
        "lefthook.yml",
    ):
        return "build"
    return "runtime"


# ------------------------------------------------------------------------------------------- subsystems

# path prefix -> subsystem (first match wins). These are the provenance units recorded in the credits ledger.
SUBSYSTEMS: list[tuple[str, str]] = [
    ("contributors/", "attribution.contributors"),
    ("LICENSE", "attribution.license"),
    ("agent/monitoring/", "agent.telemetry"),
    ("agent/lsp/", "agent.lsp"),
    ("agent/", "agent.core"),
    ("tools/environments/", "tools.environments"),
    ("tools/approval", "tools.approval"),
    ("tools/delegate", "tools.subagents"),
    ("tools/mcp", "mcp.client"),
    ("tools/browser", "tools.browser"),
    ("tools/", "tools.builtin"),
    ("toolset", "tools.toolsets"),
    ("model_tools.py", "tools.registry_glue"),
    ("hermes_cli/", "cli"),
    ("hermes_state", "state.sessions"),
    ("hermes_constants", "state.paths"),
    ("hermes_dir_acl.py", "state.paths"),
    ("hermes_platform/", "platform"),
    ("gateway/platforms/", "gateway.platforms"),
    ("gateway/", "gateway.core"),
    ("tui_gateway/", "tui.gateway"),
    ("ui-tui/", "tui.ink"),
    ("cron/", "cron"),
    ("acp_adapter/", "acp"),
    ("mcp_serve.py", "mcp.server"),
    ("optional-mcps/", "mcp.optional"),
    ("plugins/platforms/", "gateway.platform_plugins"),
    ("plugins/memory/", "memory.providers"),
    ("plugins/model-providers/", "providers.plugins"),
    ("plugins/", "plugins"),
    ("plugin-catalog/", "plugins.catalog"),
    ("skills/", "skills.bundled"),
    ("optional-skills/", "skills.optional"),
    ("providers/", "providers.core"),
    ("pm/", "packaging.pm"),
    ("scripts/termux/", "packaging.termux"),
    ("scripts/", "packaging.scripts"),
    ("nix/", "packaging.nix"),
    ("flake.", "packaging.nix"),
    ("docker", "packaging.docker"),
    ("Dockerfile", "packaging.docker"),
    ("setup-hermes", "install"),
    ("setup.py", "install"),
    ("apps/desktop/", "desktop.app"),
    ("apps/bootstrap-installer/", "desktop.installer"),
    ("apps/", "desktop.shared"),
    ("web/", "web.dashboard"),
    ("website/", "docs.website"),
    ("locales/", "i18n"),
    ("evals/", "evals"),
    ("tests-js/", "tests"),
    ("tests/", "tests"),
    ("native/", "native"),
    (".github/", "ci"),
    ("docs/", "docs"),
]


def subsystem(path: str) -> str:
    for prefix, name in SUBSYSTEMS:
        if path.startswith(prefix):
            return name
    return "core" if path.endswith(".py") and "/" not in path else "repository"


# ------------------------------------------------------------------------------------------- rules

# (category, regex over "identifier", regex over the first location's path or "", class, rationale, rule id)
RULES: list[tuple[str, str, str, str, str, str]] = [
    # attribution
    (
        "*",
        r"",
        r"^(LICENSE|contributors/|.*(NOTICE|AUTHORS|THIRD_PARTY|CREDITS)[^/]*$)",
        "UPSTREAM_ATTRIBUTION",
        "upstream licence/notice/credit material; must travel with any distribution",
        "R-ATTR-FILES",
    ),
    ("attribution", r"", r"", "UPSTREAM_ATTRIBUTION", "upstream licence, notice or credit material", "R-ATTR"),
    (
        "url",
        r"github\.com/(nousresearch|NousResearch)/hermes-agent/?($|blob|tree|graphs|commits)",
        r"",
        "UPSTREAM_ATTRIBUTION",
        "points at the upstream source; keep as attribution, never as a runtime feed",
        "R-URL-UPSTREAM-REPO",
    ),
    # vendor services and telemetry
    (
        "url",
        r"(portal|welcome-api|inference-api)\.nousresearch\.com",
        r"",
        "REMOVE",
        "Nous Portal account/billing/inference service; BLUX routes models through the Model API (BluxGPT)",
        "R-URL-NOUS-SERVICE",
    ),
    (
        "url",
        r"hermes-agent\.nousresearch\.com/(install\.(sh|ps1)|.*update|.*release|.*latest)",
        r"",
        "REMOVE",
        "upstream installer/update feed; BLUX ships its own install path",
        "R-URL-NOUS-FEED",
    ),
    (
        "url",
        r"hermes-agent\.nousresearch\.com/docs",
        r"",
        "REPLACE",
        "upstream documentation link; replace with BLUX docs, cite upstream in credits",
        "R-URL-NOUS-DOCS",
    ),
    (
        "url",
        r"(hermes-assets|hermes-agent)\.nousresearch\.com|nousresearch\.com",
        r"",
        "REPLACE",
        "Nous-hosted asset or page; replace with BLUX-owned or bundled equivalent",
        "R-URL-NOUS-OTHER",
    ),
    (
        "url",
        r"(api\.openai\.com|api\.anthropic\.com|openrouter\.ai|api\.deepseek\.com|generativelanguage\.googleapis|"
        r"api\.x\.ai|api\.mistral\.ai|api\.groq\.com|api\.together|api\.fireworks|bedrock|api\.cohere|"
        r"api\.moonshot|dashscope|api\.minimax|api\.z\.ai|ollama|lmstudio)",
        r"",
        "MIGRATE",
        "model provider endpoint; provider transport moves to BluxGPT behind the Model API",
        "R-URL-PROVIDER",
    ),
    (
        "url",
        r"(example\.(com|org|net|dev|test)|\.example|\.test\b|\.invalid\b|localhost|127\.0\.0\.1|0\.0\.0\.0|"
        r"\b10\.\d|192\.168\.|\[::1\])",
        r"",
        "MIGRATE",
        "placeholder or local endpoint used by retained behaviour/tests (no external dependency)",
        "R-URL-LOCAL",
    ),
    (
        "url",
        r"packages\.termux\.dev|termux",
        r"",
        "MIGRATE",
        "Termux package source used by Android packaging",
        "R-URL-TERMUX",
    ),
    (
        "url",
        r"registry\.npmjs\.org|pypi\.org|files\.pythonhosted|raw\.githubusercontent\.com|github\.com|"
        r"objects\.githubusercontent",
        r"",
        "MIGRATE",
        "package/source registry referenced by build or plugin install; re-pinned under BLUX packaging",
        "R-URL-REGISTRY",
    ),
    (
        "url",
        r"",
        r"^(website/|docs/|.*\.md$)",
        "REPLACE",
        "documentation link in upstream docs; BLUX writes its own docs",
        "R-URL-DOCS",
    ),
    (
        "url",
        r"",
        r"",
        "MIGRATE",
        "third-party service endpoint of a retained tool, plugin, MCP or gateway; "
        "kept behind that feature's network declaration",
        "R-URL-SERVICE",
    ),
    # telemetry / network
    (
        "network_hook",
        r":: telemetry-export$",
        r"",
        "REMOVE",
        "telemetry/metrics export; BLUX records receipts locally (BLUX System) and exports nothing by default. "
        "Only the hook goes: the file's other behaviour follows its subsystem",
        "R-NET-TELEMETRY",
    ),
    (
        "network_hook",
        r":: update-check$",
        r"",
        "REMOVE",
        "upstream update/release check; BLUX owns updates",
        "R-NET-UPDATE",
    ),
    (
        "network_hook",
        r"",
        r"^hermes_cli/auth_nous|nous",
        "REMOVE",
        "Nous Portal account/inference transport",
        "R-NET-NOUS",
    ),
    (
        "network_hook",
        r"",
        r"^(agent/(auxiliary|chat_completion|model_metadata|provider)|providers/|plugins/model-providers/"
        r"|hermes_cli/(auth|provider|model))",
        "MIGRATE",
        "model-provider transport; moves behind the Model API (BluxGPT backend)",
        "R-NET-PROVIDER",
    ),
    (
        "network_hook",
        r"",
        r"",
        "MIGRATE",
        "outbound network use of a retained feature; must be declared by its BLUX capability",
        "R-NET-FEATURE",
    ),
    (
        "updater",
        r"plugins?_(cmd_)?updates?|plugin_update",
        r"",
        "MIGRATE",
        "plugin update flow; becomes the BLUX plugin adapter's reviewed update",
        "R-UPDATER-PLUGIN",
    ),
    ("updater", r"", r"", "REMOVE", "upstream self-update/channel machinery; BLUX/APT own updates", "R-UPDATER"),
    # identifiers
    ("package_id", r"", r"", "REPLACE", "Hermes-branded package identity; BLUX publishes under its own names", "R-PKG"),
    (
        "command",
        r"^(hermes|hermes-agent|hermes-acp)$",
        r"",
        "TEMPORARY_COMPATIBILITY",
        "entry point users already run; keep as an alias that forwards to BLUX during migration",
        "R-CMD-ENTRY",
    ),
    (
        "command",
        r"^(update|uninstall|upgrade|login|logout|portal|subscribe|billing|nous)",
        r"",
        "REPLACE",
        "Hermes lifecycle/account command; BLUX/ax provide their own",
        "R-CMD-LIFECYCLE",
    ),
    ("command", r"", r"", "MIGRATE", "user-facing command behaviour retained; target in parity matrix", "R-CMD"),
    (
        "env_var",
        r"^(HERMES_HOME|HERMES_RUNTIME_DIR|HERMES_PROFILE|HERMES_CONFIG\w*)$",
        r"",
        "TEMPORARY_COMPATIBILITY",
        "existing installs set it; BLUX reads it as a fallback for its own variable",
        "R-ENV-HOME",
    ),
    (
        "env_var",
        r"TELEMETRY_DISABLED|DISABLE_TELEMETRY|DO_NOT_TRACK|NO_TELEMETRY",
        r"",
        "MIGRATE",
        "keeps a third-party tool's telemetry off; BLUX keeps setting it",
        "R-ENV-TELEMETRY-OFF",
    ),
    (
        "env_var",
        r"^NOUS_|_NOUS_|NOUS_PORTAL|^HERMES_PORTAL|DASHBOARD_PORTAL",
        r"",
        "REMOVE",
        "Nous Portal/account variable",
        "R-ENV-NOUS",
    ),
    (
        "env_var",
        r"TELEMETRY|OTEL|OTLP|SENTRY|POSTHOG|SHARED_METRICS|ANALYTICS",
        r"",
        "REMOVE",
        "telemetry switch/endpoint (BLUX exports no telemetry)",
        "R-ENV-TELEMETRY",
    ),
    (
        "env_var",
        r"^(HERMES|BLHERMES)_",
        r"",
        "REPLACE",
        "Hermes-prefixed setting; BLUX_* equivalent (old name kept as fallback where users set it)",
        "R-ENV-HERMES",
    ),
    (
        "env_var",
        r"(_API_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS?)$",
        r"",
        "MIGRATE",
        "credential variable; read only by the owning backend/capability (BluxGPT for providers)",
        "R-ENV-CRED",
    ),
    ("env_var", r"", r"", "MIGRATE", "third-party/platform variable read by retained behaviour", "R-ENV"),
    (
        "identifier",
        r"^NOUS_(ACCENT|BLUE|ALT_|CREAM|NAVY|GOLD|RED|GREEN)",
        r"",
        "REPLACE",
        "Nous brand palette constant; BLUX palette",
        "R-ID-PALETTE",
    ),
    ("identifier", r"NOUS|PORTAL", r"", "REMOVE", "Nous Portal/account constant", "R-ID-NOUS"),
    ("identifier", r"", r"", "REPLACE", "Hermes-branded constant; renamed with its module", "R-ID"),
    (
        "path",
        r"\.hermes|hermes-agent|/hermes\b|hermes_home",
        r"",
        "TEMPORARY_COMPATIBILITY",
        "existing state location; BLUX imports/reads it during migration, then uses its own state root",
        "R-PATH-HERMES",
    ),
    ("path", r"", r"", "MIGRATE", "state/config location of retained behaviour", "R-PATH"),
    ("provider", r"^nous", r"", "REMOVE", "Nous Portal inference provider", "R-PROV-NOUS"),
    ("provider", r"", r"", "MIGRATE", "provider moves to a BluxGPT route behind a logical model", "R-PROV"),
    ("module", r"^hermes_", r"", "REPLACE", "Hermes-named module; re-homed under a BLUX package name", "R-MOD-HERMES"),
    ("module", r"", r"^(tests|tests-js|evals)/", "MIGRATE", "test module; characterization source", "R-MOD-TEST"),
    ("module", r"", r"", "MIGRATE", "first-party module; target in parity matrix", "R-MOD"),
    ("tool", r"", r"", "MIGRATE", "agent tool; becomes a BLUX capability with policy and receipts", "R-TOOL"),
    ("toolset", r"", r"", "MIGRATE", "toolset; becomes a capability bundle/policy grant", "R-TOOLSET"),
    ("skill", r"", r"", "MIGRATE", "skill; loaded through the BLUX skill adapter", "R-SKILL"),
    (
        "plugin",
        r"",
        r"^plugin-catalog/",
        "MIGRATE",
        "catalog entry for a third-party plugin; re-reviewed before BLUX lists it",
        "R-PLUGIN-CATALOG",
    ),
    ("plugin", r"", r"", "MIGRATE", "plugin; loaded through the BLUX plugin adapter", "R-PLUGIN"),
    ("mcp", r"", r"", "MIGRATE", "MCP surface; BLUX MCP adapter", "R-MCP"),
    ("android", r"", r"", "MIGRATE", "Android/Bionic behaviour BLUX must keep for Termux", "R-ANDROID"),
    ("termux", r"", r"", "MIGRATE", "Termux packaging BLUX must keep", "R-TERMUX"),
    ("install", r"update|upgrade", r"", "REPLACE", "Hermes update path; BLUX update path", "R-INSTALL-UPDATE"),
    (
        "install",
        r"",
        r"",
        "REPLACE",
        "Hermes installer/uninstaller; BLUX install path (state import offered)",
        "R-INSTALL",
    ),
    ("*", r"", r"", "MIGRATE", "retained subsystem file; target in parity matrix", "R-SUBSYSTEM"),
]


def classify(category: str, identifier: str, first_path: str) -> tuple[str, str, str]:
    for cat, ident, path, cls, why, rule in RULES:
        if cat not in ("*", category):
            continue
        if ident and not re.search(ident, identifier, re.I):
            continue
        if path and not re.search(path, first_path):
            continue
        return cls, why, rule
    raise AssertionError("RULES ends with a catch-all")  # pragma: no cover


# ------------------------------------------------------------------------------------------- collection


class Inventory:
    def __init__(self) -> None:
        self.refs: dict[tuple[str, str], dict[str, Any]] = {}

    def add(self, category: str, identifier: str, path: str, line: int | None = None, **extra: Any) -> None:
        identifier = EMAIL.sub("[email]", identifier)
        key = (category, identifier)
        item = self.refs.setdefault(key, {"locations": [], "files": set(), "references": 0, "scopes": set(), **extra})
        item["references"] += 1
        item["files"].add(path)
        item["scopes"].add(scope(path))
        loc = f"{path}:{line}" if line else path
        if len(item["locations"]) < MAX_LOCATIONS and loc not in item["locations"]:
            item["locations"].append(loc)

    def items(self) -> list[dict[str, Any]]:
        out = []
        for (category, identifier), item in sorted(self.refs.items()):
            first = item["locations"][0].rsplit(":", 1)[0] if item["locations"] else ""
            # a runtime location decides the classification when there is one
            runtime_first = next(
                (loc.rsplit(":", 1)[0] for loc in item["locations"] if scope(loc.rsplit(":", 1)[0]) == "runtime"), first
            )
            cls, why, rule = classify(category, identifier, runtime_first)
            row = {
                "id": f"{category}:{identifier}",
                "category": category,
                "identifier": identifier,
                "classification": cls,
                "rule": rule,
                "rationale": why,
                "subsystems": sorted({subsystem(f) for f in item["files"]}),
                "scopes": sorted(item["scopes"]),
                "references": item["references"],
                "files": len(item["files"]),
                "locations": item["locations"],
            }
            row.update({k: v for k, v in item.items() if k not in ("locations", "files", "references", "scopes")})
            out.append(row)
        return out


def lines(text: str) -> Iterator[tuple[int, str]]:
    yield from enumerate(text.splitlines(), 1)


CODE = re.compile(r"\.(py|ts|tsx|js|mjs|cjs|sh|bash|ps1|toml|ya?ml|json|nix|kts?|rs|go|service|desktop|plist)$")
TEXT = re.compile(
    r"\.(py|ts|tsx|js|mjs|cjs|sh|bash|ps1|toml|ya?ml|json|nix|md|mdx|txt|rst|html|css|cfg|ini|env|example)$|"
    r"(^|/)(Dockerfile|justfile|Makefile|LICENSE[^/]*|NOTICE[^/]*|activate[^/]*)$"
)


def text_files(src: Source) -> list[str]:
    return [
        f
        for f in src.files
        if TEXT.search(f)
        and not BINARY.search(f)
        and not f.startswith("contributors/")
        and not f.endswith(("package-lock.json", "uv.lock"))
    ]


def collect(src: Source, omitted: dict[str, int]) -> Inventory:
    inv = Inventory()
    files = text_files(src)
    py = [f for f in files if f.endswith(".py")]
    runtime_py = [f for f in py if scope(f) == "runtime"]

    # attribution ---------------------------------------------------------------------------------
    contributors = [f for f in src.files if f.startswith("contributors/")]
    inv.add(
        "attribution",
        "contributors/ (upstream contributor records; names and e-mails not reproduced)",
        "contributors/README.md",
        count=len(contributors),
    )
    for f in src.files:
        name = f.rsplit("/", 1)[-1]
        if re.match(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)([.-][^/]*)?$", name, re.I) and not f.startswith(
            "contributors/"
        ):
            first = src.read(f).strip().splitlines()[:1]
            inv.add("attribution", f"{f} ({first[0][:60] if first else 'empty'})", f)

    # modules -------------------------------------------------------------------------------------
    top: dict[str, str] = {}
    for f in py:
        parts = f.split("/")
        if len(parts) == 1:
            top[parts[0][:-3]] = f
        elif f"{parts[0]}/__init__.py" in src.files or any(
            g.startswith(parts[0] + "/") and g.endswith(".py") for g in py[:0]
        ):
            top.setdefault(parts[0], f"{parts[0]}/__init__.py")
    for d in sorted({f.split("/")[0] for f in py if "/" in f}):
        if d not in top and d not in (
            "tests",
            "tests-js",
            "evals",
            "scripts",
            "website",
            "docs",
            "optional-skills",
            "skills",
            "optional-mcps",
            "plugin-catalog",
            "apps",
            "web",
            "ui-tui",
            "nix",
            "docker",
            "locales",
        ):
            top[d] = next(f for f in py if f.startswith(d + "/"))
    importers: dict[str, set[str]] = defaultdict(set)
    imp = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for f in runtime_py:
        for _n, ln in lines(src.read(f)):
            m = imp.match(ln)
            if m and m.group(1) in top:
                importers[m.group(1)].add(f)
    for name, where in sorted(top.items()):
        n_files = sum(1 for f in py if f == where or f.startswith(name + "/"))
        inv.add("module", name, where, py_files=n_files, imported_by_runtime_files=len(importers[name]))

    # package identifiers --------------------------------------------------------------------------
    for f in src.files:
        if f.endswith("package.json") and "node_modules" not in f:
            try:
                doc = json.loads(src.read(f))
            except ValueError:
                continue
            if isinstance(doc, dict) and isinstance(doc.get("name"), str):
                inv.add("package_id", f"npm:{doc['name']}", f)
            if isinstance(doc, dict):
                for k, v in (doc.get("build") or {}).items() if isinstance(doc.get("build"), dict) else []:
                    if k in ("appId", "productName") and isinstance(v, str):
                        inv.add("package_id", f"desktop.{k}:{v}", f)
                bins = doc.get("bin")
                for b in ([doc["name"]] if isinstance(bins, str) else list(bins or {})) if bins else []:
                    inv.add("command", b, f, kind="npm-bin")
        elif f.endswith("pyproject.toml"):
            t = src.read(f)
            m = re.search(r'^\[project\][^\[]*?^name\s*=\s*"([^"]+)"', t, re.M | re.S)
            if m:
                inv.add("package_id", f"pypi:{m.group(1)}", f)
            sec = re.search(r"^\[project\.scripts\]\n((?:[^\[\n].*\n?)*)", t, re.M)
            for _n, ln in lines(sec.group(1) if sec else ""):
                mm = re.match(r'\s*"?([\w.-]+)"?\s*=\s*"([^"]+)"', ln)
                if mm:
                    inv.add("command", mm.group(1), f, kind="python-entry-point", target=mm.group(2))
    for f in files:
        if scope(f) in ("build", "runtime") and not f.endswith(".md"):
            for n, ln in lines(src.read(f)):
                for m in re.finditer(r"(?:^|\s)Package:\s*([a-z0-9][a-z0-9.+-]+)", ln):
                    inv.add("package_id", f"deb:{m.group(1)}", f, n)
                for m in re.finditer(r"\bpname\s*=\s*\"([^\"]+)\"", ln):
                    inv.add("package_id", f"nix:{m.group(1)}", f, n)
                for m in re.finditer(
                    r"\b((?:ghcr\.io|docker\.io)/[a-z0-9_.-]*/[a-z0-9_.-]*hermes[a-z0-9_.-]*|"
                    r"nousresearch/hermes[a-z0-9_.-]*)",
                    ln,
                    re.I,
                ):
                    inv.add("package_id", f"image:{m.group(1).lower()}", f, n)

    # commands: argparse subcommands and slash commands (AST, literal names only) -------------------
    for f in [p for p in runtime_py if p.startswith(("hermes_cli/", "acp_adapter/", "agent/legacy"))]:
        try:
            tree = ast.parse(src.read(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            name = node.args[0].value
            fn = node.func
            if not isinstance(name, str):
                continue
            if isinstance(fn, ast.Attribute) and fn.attr == "add_parser":
                inv.add("command", f"hermes {name}", f, node.lineno, kind="cli-subcommand")
            elif isinstance(fn, ast.Name) and fn.id == "CommandDef":
                inv.add("command", f"/{name}", f, node.lineno, kind="slash-command")

    # env vars ------------------------------------------------------------------------------------
    env_read = re.compile(
        r"(?:os\.environ(?:\.get|\.setdefault|\.pop)?\(\s*|os\.getenv\(\s*|environ\[\s*|getenv\(\s*|"
        r"process\.env\.|process\.env\[\s*)[\"']?([A-Z][A-Z0-9_]{2,})"
    )
    branded = re.compile(r"\b((?:HERMES|BLHERMES|NOUS)_[A-Z0-9_]+)\b")
    env_names: set[str] = set()
    branded_hits: list[tuple[str, str, int]] = []
    for f in files:
        sc = scope(f)
        for n, ln in lines(src.read(f)):
            for m in env_read.finditer(ln):
                env_names.add(m.group(1))
                if sc in ("runtime", "build"):
                    inv.add("env_var", m.group(1), f, n)
            for m in branded.finditer(ln):
                branded_hits.append((m.group(1), f, n))
    shell_env = re.compile(r"\$\{?((?:HERMES|BLHERMES|NOUS)_[A-Z0-9_]+)")
    for f in files:
        if f.endswith((".sh", ".ps1", ".nix", ".yml", ".yaml", "Dockerfile")) or "/" not in f:
            env_names.update(m.group(1) for m in shell_env.finditer(src.read(f)))
    for name, f, n in branded_hits:
        inv.add("env_var" if name in env_names else "identifier", name, f, n)

    # paths ---------------------------------------------------------------------------------------
    path_lit = re.compile(
        r"[\"'`]((?:~|\$HOME|\$\{HOME\}|\$PREFIX|\$\{PREFIX\}|/data/data/com\.termux[^\"'`]*|"
        r"/etc|/var|/usr|/opt|%APPDATA%|%LOCALAPPDATA%)?[^\"'`\s]*?(?:\.hermes|hermes-agent|"
        r"/hermes)(?:/[^\"'`\s]*)?)[\"'`]"
    )
    for f in files:
        if scope(f) not in ("runtime", "build"):
            continue
        for n, ln in lines(src.read(f)):
            for m in path_lit.finditer(ln):
                value = m.group(1)
                if "://" in value or len(value) > 120 or value.startswith(("import", "from")):
                    continue
                if value.startswith(("~", "$", "/", "%", ".")) or ".hermes" in value:
                    inv.add("path", value, f, n)
            if "get_hermes_home(" in ln and f.endswith(".py"):
                inv.add("path", "get_hermes_home() (profile-aware state root)", f, n)

    # urls ----------------------------------------------------------------------------------------
    url = re.compile(r"https?://[A-Za-z0-9.\[\]:_-]+(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*)?")
    upstream_url = re.compile(r"nous|hermes", re.I)
    for f in files:
        sc = scope(f)
        for n, ln in lines(src.read(f)):
            for m in url.finditer(ln):
                u = m.group(0).rstrip(".,);:'\"")
                if EMAIL.search(u):
                    continue
                if sc in ("runtime", "build", "data") or upstream_url.search(u):
                    inv.add("url", u, f, n)
                else:
                    omitted["url (test/docs scope, not upstream-branded)"] += 1

    # network hooks and updaters (runtime code only) ----------------------------------------------
    client = re.compile(
        r"\b(requests\.(?:get|post|put|patch|delete|head|request|Session)|httpx\.(?:get|post|put|stream|request|Client|"
        r"AsyncClient)|urllib\.request\.urlopen|urlopen\(|aiohttp\.ClientSession|websockets?\.connect|"
        r"socket\.create_connection|smtplib\.SMTP|imaplib\.IMAP4|ftplib|paramiko|OpenAI\(|AsyncOpenAI\(|"
        r"Anthropic\(|AsyncAnthropic\(|fetch\(|new WebSocket\(|EventSource\(|axios\.|net\.request|https?\.request\(|"
        r"grpc\.|boto3\.client)"
    )
    # exporters only: a local counter named "telemetry" is not a network hook
    telemetry = re.compile(
        r"\b(opentelemetry|otlp_exporter|OTLPSpanExporter|OTLP_ENDPOINT|posthog|sentry_sdk|@sentry/|"
        r"mixpanel\.track|amplitude\.(?:track|logEvent|getInstance)|segment\.io/v1|shared[-_]metrics[-_]?(?:send|endpoint|sender)|"
        r"crash-metrics|update-metrics|onboarding-metrics|desktop-metrics|TELEMETRY_ENDPOINT|"
        r"SHARED_METRICS_ENDPOINT)",
        re.I,
    )
    updater_marker = re.compile(
        r"(autoUpdater|electron-updater|self[-_]?update|update[-_]channel|release[-_]channel|"
        r"check_for_updates?|latest\.json|releases/latest|pkg upgrade hermes)",
        re.I,
    )
    for f in files:
        if scope(f) != "runtime" or not CODE.search(f):
            continue
        t = src.read(f)
        kinds = sorted({m.group(1).rstrip("(") for m in client.finditer(t)})
        for kind in kinds:
            line = next(n for n, ln in lines(t) if kind in ln)
            inv.add("network_hook", f"{f} :: {kind}", f, line)
        tele = sorted({m.group(1).lower() for m in telemetry.finditer(t)})
        if tele and not f.startswith(("optional-mcps/", "optional-skills/", "skills/")):  # tools for those services
            line = next(n for n, ln in lines(t) if telemetry.search(ln))
            inv.add("network_hook", f"{f} :: telemetry-export", f, line, markers=tele)
        if kinds and updater_marker.search(t):
            line = next(n for n, ln in lines(t) if updater_marker.search(ln))
            inv.add("network_hook", f"{f} :: update-check", f, line)
        if f.startswith("plugins/platforms/"):
            continue  # a messaging platform's "update" is an incoming message, not an updater
        if (
            updater_marker.search(t)
            or re.search(r"(^|/)(update|updater|updates?_[a-z_]+|[a-z_]*_update)\.(py|ts|js)$", f)
            or "/updater/" in f
        ):
            inv.add("updater", f, f)

    # providers -----------------------------------------------------------------------------------
    for f in ("hermes_cli/auth.py",):
        if f in src.files:
            for node in ast.walk(ast.parse(src.read(f))):
                if isinstance(node, ast.AnnAssign | ast.Assign):
                    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "PROVIDER_REGISTRY"
                        and isinstance(node.value, ast.Dict)
                    ):
                        for k in node.value.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                inv.add("provider", k.value, f, k.lineno, source="PROVIDER_REGISTRY")
    for f in src.files:
        m = re.match(r"plugins/model-providers/([^/]+)/plugin\.yaml$", f)
        if m:
            inv.add("provider", m.group(1), f, source="model-provider plugin")

    # tools and toolsets --------------------------------------------------------------------------
    for f in [p for p in runtime_py if p.startswith(("tools/", "plugins/", "agent/"))]:
        try:
            tree = ast.parse(src.read(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register":
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id in ("registry", "tool_registry", "ctx"):
                    for kw in node.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            inv.add("tool", kw.value.value, f, node.lineno)
    if "toolsets.py" in src.files:
        for node in ast.walk(ast.parse(src.read("toolsets.py"))):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "TOOLSETS" for t in node.targets)
                and isinstance(node.value, ast.Dict)
            ):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        inv.add("toolset", k.value, "toolsets.py", k.lineno)

    # skills, plugins, MCP ------------------------------------------------------------------------
    for f in src.files:
        if re.match(r"(optional-)?skills/.+/SKILL\.md$", f):
            inv.add("skill", f.rsplit("/", 1)[0], f)
        if f.endswith("plugin.yaml") and f.startswith("plugins/"):
            inv.add("plugin", f.rsplit("/", 1)[0], f)
        m = re.match(r"plugin-catalog/([^/]+)\.ya?ml$", f)
        if m:
            inv.add("plugin", f"catalog:{m.group(1)}", f)
        m = re.match(r"optional-mcps/([^/]+)/", f)
        if m:
            inv.add("mcp", f"optional-mcps/{m.group(1)}", f)
    for f in runtime_py:
        if f == "mcp_serve.py" or re.search(r"(^|/)mcp[_a-z]*\.py$|/mcp/", f):
            inv.add("mcp", f, f)

    # subsystem file categories -------------------------------------------------------------------
    rules = [
        ("cron", r"^cron/|scheduler"),
        ("gateway", r"^gateway/|^plugins/platforms/"),
        ("tui", r"^tui_gateway/|^ui-tui/src/"),
        ("session", r"^hermes_state|session"),
        ("memory", r"memory|^plugins/memory/|context_engine"),
        ("subagent", r"delegate|subagent|child_agent|spawn_agent"),
        ("approval", r"approval|yolo"),
        ("credential", r"(auth|credential|secret|keyring|oauth|token_store|api_key)[^/]*\.(py|ts)$"),
        ("termux", r"termux"),
        ("install", r"(^setup-hermes|install|uninstall|bootstrap)[^/]*\.(sh|ps1|py|ts)$|^scripts/install"),
    ]
    for f in src.files:
        if scope(f) not in ("runtime", "build") or not (CODE.search(f) or f.endswith(("Dockerfile",))):
            continue
        for cat, pattern in rules:
            if re.search(pattern, f, re.I):
                inv.add(cat, f, f)
    android = re.compile(
        r"sys\.platform\s*==\s*[\"']android|is_android|is_termux|bionic|ANDROID_ROOT|TERMUX_VERSION|"
        r"com\.termux|PREFIX.*termux",
        re.I,
    )
    for f in files:
        if scope(f) in ("runtime", "build") and CODE.search(f) and android.search(src.read(f)):
            inv.add("android", f, f)
    return inv


# ------------------------------------------------------------------------------------------- output


def build(root: Path) -> dict[str, Any]:
    src = open_source(root)
    omitted: dict[str, int] = defaultdict(int)
    items = collect(src, omitted).items()
    counts: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(CLASSES, 0))
    for it in items:
        counts[it["category"]][it["classification"]] += 1
    subsystems: dict[str, int] = defaultdict(int)
    for f in src.files:
        subsystems[subsystem(f)] += 1
    origin = ""
    with contextlib.suppress(subprocess.CalledProcessError):
        origin = git(root, "remote", "get-url", "origin").strip()
    return {
        "contract": "blux.blhermes-inventory",
        "inventory_version": 1,
        "generated_by": "migration/blhermes/inventory.py",
        "source": {
            "repository": re.sub(r"^git@github\.com:|\.git$|^https://github\.com/", "", origin),
            "commit": src.commit,
            "tree": src.tree,
            "tracked_files": len(src.files),
            "content_sha256": hashlib.sha256("\n".join(src.files).encode()).hexdigest(),
        },
        "upstream": {
            "repository": "NousResearch/hermes-agent",
            "shared_base": "3fa571a51a6bf1220dfe9a7a23fcd5bb9482a1af",
            "license": "MIT",
            "copyright": "Copyright (c) 2025 Nous Research",
            "credits_lineage": "credits:LIN-001",
        },
        "classes": list(CLASSES),
        "categories": CATEGORIES,
        "rules": [{"id": r[5], "category": r[0], "classification": r[3], "rationale": r[4]} for r in RULES],
        "subsystem_files": dict(sorted(subsystems.items())),
        "summary": {k: counts[k] for k in sorted(counts)},
        "omitted": dict(omitted),
        "items": items,
    }


def render(doc: dict[str, Any]) -> str:
    """Stable, diff-friendly JSON: header fields indented, one item per line."""
    items = doc.pop("items")
    head = json.dumps(doc, indent=1, ensure_ascii=False)
    body = ",\n".join(" " + json.dumps(i, ensure_ascii=False, separators=(",", ":")) for i in items)
    return head[:-2] + ',\n "items": [\n' + body + "\n ]\n}\n"


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", type=Path, required=True, help="clean blhermes-agent checkout")
    p.add_argument("--output", type=Path, default=OUTPUT)
    p.add_argument("--check", action="store_true", help="fail if --output differs from a fresh scan")
    args = p.parse_args(list(argv) if argv is not None else None)
    doc = render(build(args.source.expanduser()))
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != doc:
            print(f"{args.output} is stale; rerun without --check", file=sys.stderr)
            return 1
        return 0
    args.output.write_text(doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
