"""Settings for the direct `blux` commands (ADR-0011).

Precedence: command-line flag, then environment variable, then the config file, then the default.
The config file is TOML at $BLUX_CONFIG, else $XDG_CONFIG_HOME/blux/config.toml, else
~/.config/blux/config.toml. It holds preferences only. BLUX never holds provider credentials: the
model backend owns them (BluxGPT: ~/.config/bluxgpt/.env), so a credential-like key or value here
is a configuration error rather than something to be read.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blux_sdk import model_api

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

FOOTERS = ("auto", "always", "never")
DEFAULTS: dict[str, Any] = {
    "model": "blux-main",
    "timeout_seconds": None,  # None: the routing table's default (it is also the upper bound)
    "routes": None,  # None: $BLUX_MODEL_ROUTES or the packaged default table
    "system_store": None,  # None: receipts are not recorded in BLUX System
    "stream": True,
    "footer": "auto",
    "redact_input": True,
    "max_input_bytes": 1024 * 1024,
}
ENVIRONMENT = {
    "model": "BLUX_MODEL",
    "timeout_seconds": "BLUX_TIMEOUT",
    "routes": "BLUX_MODEL_ROUTES",
    "system_store": "BLUX_SYSTEM_STORE",
}
CREDENTIAL_NAME = re.compile(r"key|token|secret|passw|credential|auth", re.I)
SECRET_VALUE = re.compile("|".join(f"(?:{p})" for p in model_api.SECRET_PATTERNS))


class ConfigError(Exception):
    """The configuration is unusable. The message names the source and never repeats a value."""


@dataclass
class Settings:
    values: dict[str, Any]
    sources: dict[str, str]
    path: Path
    found: bool
    notes: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None

    def to_document(self) -> dict[str, Any]:
        return {
            "config_file": str(self.path),
            "config_found": self.found,
            "settings": {k: {"value": self.values[k], "source": self.sources[k]} for k in DEFAULTS},
            "credentials": "never stored by BLUX; the model backend owns them (see `blux doctor`)",
        }


def config_path(environ: Mapping[str, str]) -> Path:
    if environ.get("BLUX_CONFIG"):
        return Path(environ["BLUX_CONFIG"]).expanduser()
    base = Path(environ["XDG_CONFIG_HOME"]) if environ.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "blux" / "config.toml"


def load(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    path = config_path(env)
    file_values: dict[str, Any] = {}
    found = path.is_file()
    if found:
        try:
            file_values = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: not readable TOML ({type(exc).__name__})") from None
        _check_file(path, file_values)
    values, sources = dict(DEFAULTS), dict.fromkeys(DEFAULTS, "default")
    for key, value in file_values.items():
        values[key], sources[key] = value, f"file {path}"
    for key, name in ENVIRONMENT.items():
        if env.get(name):
            values[key], sources[key] = _from_env(name, key, env[name]), f"env {name}"
    _check_values(values, sources)
    for key in ("routes", "system_store"):
        if values[key]:
            values[key] = str(Path(values[key]).expanduser())
    return Settings(values, sources, path, found)


def override(settings: Settings, key: str, value: Any, flag: str) -> None:
    """Apply a command-line flag (highest precedence) when it was given."""
    if value is not None:
        settings.values[key], settings.sources[key] = value, f"flag {flag}"
        _check_values(settings.values, settings.sources)


def _check_file(path: Path, doc: dict[str, Any]) -> None:
    for key, value in doc.items():
        if CREDENTIAL_NAME.search(key) and key not in DEFAULTS:
            raise ConfigError(
                f"{path}: {key!r} looks like a credential. BLUX does not store provider credentials; "
                "put them in the model backend's configuration (BluxGPT: ~/.config/bluxgpt/.env)."
            )
        if isinstance(value, str) and SECRET_VALUE.search(value):
            raise ConfigError(f"{path}: the value of {key!r} looks like a credential; remove it from the file.")
        if key not in DEFAULTS:
            raise ConfigError(f"{path}: unknown setting {key!r} (known: {', '.join(DEFAULTS)})")


def _from_env(name: str, key: str, raw: str) -> Any:
    if key == "timeout_seconds":
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(f"{name} must be a number of seconds") from None
    return raw


def _check_values(values: dict[str, Any], sources: dict[str, str]) -> None:
    def bad(key: str, why: str) -> ConfigError:
        return ConfigError(f"{key} ({sources[key]}): {why}")

    if values["model"] not in model_api.LOGICAL_MODELS:
        raise bad("model", f"not a logical BLUX model; use one of {', '.join(model_api.LOGICAL_MODELS)}")
    t = values["timeout_seconds"]
    if t is not None and (isinstance(t, bool) or not isinstance(t, int | float) or t <= 0):
        raise bad("timeout_seconds", "must be a positive number of seconds")
    for key in ("routes", "system_store"):
        if values[key] is not None and (not isinstance(values[key], str) or not values[key]):
            raise bad(key, "must be a path")
    for key in ("stream", "redact_input"):
        if not isinstance(values[key], bool):
            raise bad(key, "must be true or false")
    if values["footer"] not in FOOTERS:
        raise bad("footer", f"must be one of {', '.join(FOOTERS)}")
    m = values["max_input_bytes"]
    if isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= 16 * 1024 * 1024:
        raise bad("max_input_bytes", "must be an integer from 1 to 16777216")
