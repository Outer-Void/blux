"""The pipeline's gates. Each takes a candidate and either passes it (possibly sanitized) or rejects it with
reasons. Rejection reasons name the rule and the location, never the offending value.

Policy (v1, fixed in code so it cannot be loosened by an input file):
- eligibility: synthetic sources must be declared synthetic. BLUX operations need an explicit owner approval
  (who, when) and public data. Private user or project data is never training data, approved or not;
- secrets: any credential or secret-named value anywhere means rejection (never redaction), because a partly
  redacted secret is still a leak;
- personal data (e-mail outside reserved example domains, phone numbers, non-documentation IPv4, home-directory
  paths, owner-listed private terms): redacted in place, and the record is marked `privacy_class: redacted`;
- hidden reasoning: any field named like model reasoning (`chain_of_thought`, `scratchpad`, `thinking` …) or a
  `<thinking>` marker means rejection. Only explicit BLUX structures are kept;
- licences: every source needs at least one allowed licence, and every constructed artifact its own.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from blux_runtime.security.environment import redact

ALLOWED_LICENSES = frozenset(
    {"LicenseRef-OuterVoid-Proprietary", "MIT", "Apache-2.0", "BSD-3-Clause", "CC0-1.0", "CC-BY-4.0"}
)

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.invalid")
PHONE = re.compile(r"(?<![\w.])\+?\d{1,3}[ .-]?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])")
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8")]
HOME = re.compile(
    r"(/home/(?!user\b)[a-z_][a-z0-9_-]*|/Users/(?!user\b)[A-Za-z0-9_-]+|/data/data/[a-z]+\.[a-z.]+/files/home)"
)
COT_KEY = re.compile(
    r"^(thoughts?|chain[_-]?of[_-]?thoughts?|cot|scratch[_-]?pad|hidden[_-]?reasoning|reasoning[_-]?(trace|tokens|content)"
    r"|inner[_-]?monologue|internal[_-]?reasoning|thinking)$",
    re.I,
)
COT_MARKER = re.compile(r"</?\s*(thinking|scratchpad|antThinking|reasoning)\s*>", re.I)


@dataclass
class GateResult:
    reasons: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return bool(self.reasons)


def walk(value: Any, path: str = "") -> Iterator[tuple[str, str | None, Any]]:
    """Yield (path, key, value) for every node; key is the dict key that led to it."""
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, str(k), v
            yield from walk(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")


def eligibility(source: dict[str, Any]) -> GateResult:
    out = GateResult()
    e, kind = source["eligibility"], source["source_type"]
    if e["data_class"] == "private":
        out.reasons.append("eligibility: private user/project data is never training data")
    if kind == "synthetic" and (e["basis"] != "synthetic" or e["data_class"] != "synthetic"):
        out.reasons.append("eligibility: a synthetic source must declare basis=synthetic and data_class=synthetic")
    if kind != "synthetic":
        if e["basis"] != "owner-approved" or not e.get("approved_by") or not e.get("approved_at"):
            out.reasons.append("eligibility: non-synthetic sources need owner approval (approved_by, approved_at)")
        if kind == "blux-operation" and not source["receipts"]:
            out.reasons.append("eligibility: a BLUX operation source needs its receipts")
    return out


def hidden_reasoning(trajectory: dict[str, Any]) -> GateResult:
    out = GateResult()
    for path, key, value in walk(trajectory):
        if key is not None and COT_KEY.match(key):
            out.reasons.append(f"hidden-reasoning: field '{path}' is never stored")
        if isinstance(value, str) and COT_MARKER.search(value):
            out.reasons.append(f"hidden-reasoning: reasoning marker in '{path}'")
    return out


def secrets(document: Any) -> GateResult:
    out = GateResult()
    for path, _key, value in walk(document):
        if isinstance(value, str) and redact(value) != value:
            out.reasons.append(f"secret: credential or secret-named value in '{path}'")
    return out


def _redact_text(text: str, private_terms: tuple[str, ...], hits: set[str]) -> str:
    def email(m: re.Match[str]) -> str:
        if m.group(1).lower().endswith(ALLOWED_EMAIL_DOMAINS):
            return m.group(0)
        hits.add("email")
        return "[REDACTED:email]"

    def ip(m: re.Match[str]) -> str:
        try:
            addr = ipaddress.ip_address(m.group(1))
        except ValueError:
            return m.group(0)
        if any(addr in n for n in DOC_NETS):
            return m.group(0)
        hits.add("ipv4")
        return "[REDACTED:ip]"

    text = EMAIL.sub(email, text)
    text = IPV4.sub(ip, text)
    text, n = PHONE.subn("[REDACTED:phone]", text)
    if n:
        hits.add("phone")
    text, n = HOME.subn("/home/user", text)
    if n:
        hits.add("home-path")
    for term in private_terms:
        pattern = re.compile(re.escape(term), re.I)
        if pattern.search(text):
            hits.add("private-term")
            text = pattern.sub("[REDACTED:private]", text)
    return text


def personal_data(value: Any, private_terms: tuple[str, ...] = ()) -> tuple[Any, GateResult]:
    """Return (sanitized copy, result listing which kinds of personal data were redacted)."""
    hits: set[str] = set()

    def clean(v: Any) -> Any:
        if isinstance(v, str):
            return _redact_text(v, private_terms, hits)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    sanitized = clean(value)
    return sanitized, GateResult(redactions=sorted(hits))


def licensing(source: dict[str, Any]) -> GateResult:
    out = GateResult()
    for lic in source["licenses"]:
        if lic["spdx"] not in ALLOWED_LICENSES:
            out.reasons.append(f"license: '{lic['spdx']}' for '{lic['subject']}' is not an allowed licence")
    for i, art in enumerate(source["trajectory"].get("constructed_artifacts", [])):
        if art.get("license") not in ALLOWED_LICENSES:
            out.reasons.append(
                f"license: constructed_artifacts[{i}] ('{art.get('artifact_id')}') has no allowed licence"
            )
    return out
