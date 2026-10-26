"""`blux migrate hermes`: explicit import of legacy BLHermes/Hermes state (ADR-0013).

Dry run by default: it reports what would be imported (counts and credential names, never values).
`--apply` imports sessions into the active BLUX profile. `--credentials` additionally moves supported
provider keys into BluxGPT's config. The legacy home is never modified. The path expires on the date
shown in the report.
"""

from __future__ import annotations

import argparse
from typing import Any, TextIO

from blux_runtime import state
from blux_runtime.compat import hermes
from blux_runtime.sessions import SessionStore
from blux_sdk.serialization import dumps_json

OK, USAGE, EXPIRED = 0, 2, 3


def add_parser(sub: Any) -> None:
    m = sub.add_parser("migrate", help="import legacy state into BLUX (dry run unless --apply)")
    which = m.add_subparsers(dest="source", required=True)
    h = which.add_parser("hermes", help="sessions (and, with --credentials, provider keys) from HERMES_HOME/~/.hermes")
    h.add_argument("--apply", action="store_true", help="import (default: report only)")
    h.add_argument("--credentials", action="store_true", help="with --apply: move provider keys into BluxGPT's config")
    h.add_argument("--json", action="store_true")


def run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    if args.credentials and not args.apply:
        print("blux: --credentials needs --apply", file=err)
        return USAGE
    layout = state.active_layout().ensure()
    store = SessionStore(layout.sessions_db)
    try:
        doc: dict[str, Any] = hermes.plan(store).to_document()
        if args.apply:
            doc["applied"] = hermes.apply(store, credentials=args.credentials)
    except hermes.CompatExpired as exc:
        print(f"blux: {exc}", file=err)
        return EXPIRED
    finally:
        store.close()
    doc["blux_home"] = str(layout.home)
    if args.json:
        out.write(dumps_json(doc, pretty=getattr(args, "pretty", False)) + "\n")
        return OK
    out.write(f"legacy home   {doc['legacy_home'] or 'none found'}\n")
    out.write(f"BLUX profile  {doc['blux_home']}\n")
    out.write(
        f"sessions      {doc['sessions']} to import, {doc['already_imported']} already imported "
        f"({doc['messages']} messages)\n"
    )
    out.write(f"credentials   {', '.join(doc['credential_names']) or 'none'} (names only; values never shown)\n")
    for note in doc["notes"]:
        out.write(f"note          {note}\n")
    if "applied" in doc:
        a = doc["applied"]
        out.write(
            f"imported      {a['imported_sessions']} sessions, {a['imported_messages']} messages, "
            f"credentials: {', '.join(a['imported_credentials']) or 'none'}\n"
        )
    else:
        out.write("dry run       nothing changed; rerun with --apply\n")
    out.write(f"expires       this compatibility path is removed after {doc['compat_expires']}\n")
    return OK
