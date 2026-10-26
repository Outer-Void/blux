"""Scheduled executions (Stage 14; parity matrix BLH-020).

Schedule syntax and parsing match the BLHermes cron_schedules golden:
- `every 30m`, `30m`, `every 2h` (intervals in minutes);
- a 5-field cron expression;
- an ISO datetime for a one-shot run.

A job is an ordinary capability execution run as the `service` actor `scheduler`, so policy, approvals,
hooks and receipts all apply. Unattended runs have no session approvals, so dangerous commands are
refused. `ax` drives the runner (orchestration belongs to ax, ADR-0011); BLUX owns parsing, the job
store and execution. Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from blux_sdk import envelopes

UTC = dt.timezone.utc
_DURATION = re.compile(r"^(?:every\s+)?(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$", re.I)
_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


class ScheduleError(ValueError):
    pass


def _cron_field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for raw in text.split(","):
        part, step = raw, 1
        if "/" in raw:
            part, s = raw.split("/", 1)
            step = int(s)
            if step < 1:
                raise ScheduleError("cron step must be >= 1")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ScheduleError(f"cron value out of range: {part}")
        out.update(range(a, b + 1, step))
    return out


def parse_schedule(schedule: str) -> dict[str, Any]:
    s = " ".join(schedule.split())
    m = _DURATION.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        minutes = n * (60 if unit.startswith("h") else 1440 if unit.startswith("d") else 1)
        if minutes < 1:
            raise ScheduleError("interval must be at least 1 minute")
        return {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}
    parts = s.split(" ")
    if len(parts) == 5:
        try:
            for p, (lo, hi) in zip(parts, _FIELDS, strict=True):
                _cron_field(p, lo, hi)
        except ValueError as exc:
            raise ScheduleError(f"invalid cron expression {s!r}: {exc}") from None
        return {"kind": "cron", "expr": s, "display": s}
    try:
        when = dt.datetime.fromisoformat(s)
    except ValueError:
        raise ScheduleError(f"unrecognized schedule {schedule!r}") from None
    when = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
    return {"kind": "once", "run_at": when.isoformat(), "display": f"once at {when:%Y-%m-%d %H:%M}"}


def next_run(schedule: dict[str, Any], after: dt.datetime) -> dt.datetime | None:
    after = after.astimezone(UTC)
    if schedule["kind"] == "interval":
        return after + dt.timedelta(minutes=int(schedule["minutes"]))
    if schedule["kind"] == "once":
        at = dt.datetime.fromisoformat(schedule["run_at"])
        return at if at > after else None
    minute, hour, dom, month, dow = (
        _cron_field(p, lo, hi) for p, (lo, hi) in zip(schedule["expr"].split(" "), _FIELDS, strict=True)
    )
    t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    end = t + dt.timedelta(days=366 * 4)
    while t < end:
        if t.month in month and t.day in dom and (t.weekday() + 1) % 7 in dow:
            if t.hour in hour:
                if t.minute in minute:
                    return t
                t += dt.timedelta(minutes=1)
            else:
                t = t.replace(minute=0) + dt.timedelta(hours=1)
        else:
            t = (t + dt.timedelta(days=1)).replace(hour=0, minute=0)
    return None


@dataclass
class Job:
    job_id: str
    schedule: dict[str, Any]
    capability_id: str
    input: dict[str, Any]
    enabled: bool = True
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[Job]:
        if not self.path.exists():
            return []
        return [Job(**j) for j in json.loads(self.path.read_text(encoding="utf-8"))["jobs"]]

    def save(self, jobs: list[Job]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {"contract": "blux.scheduled-jobs", "jobs": [asdict(j) for j in jobs]}, fh, indent=1, sort_keys=True
            )
        os.replace(tmp, self.path)

    def add(self, schedule: str, capability_id: str, payload: dict[str, Any], *, now: dt.datetime) -> Job:
        parsed = parse_schedule(schedule)
        first = next_run(parsed, now)
        job = Job(
            f"job-{uuid.uuid4().hex[:12]}",
            parsed,
            capability_id,
            dict(payload),
            next_run_at=first.isoformat() if first else None,
            enabled=first is not None,
        )
        self.save([*self.load(), job])
        return job


SCHEDULER_ACTOR = {"type": "service", "id": "scheduler"}


def run_due(store: JobStore, execute: Callable[[dict[str, Any]], Any], *, now: dt.datetime) -> list[dict[str, Any]]:
    """Run every enabled job whose next_run_at has passed. One failing job never stops the others."""
    jobs = store.load()
    ran: list[dict[str, Any]] = []
    for job in jobs:
        if not job.enabled or job.next_run_at is None or dt.datetime.fromisoformat(job.next_run_at) > now:
            continue
        request = envelopes.execution_request(job.capability_id, job.input, actor=dict(SCHEDULER_ACTOR))
        try:
            outcome = execute(request)
            status = outcome.result["status"]
            entry = {
                "job_id": job.job_id,
                "operation_id": outcome.result["operation_id"],
                "receipt_id": outcome.receipt["receipt_id"],
                "status": status,
            }
        except Exception as exc:  # noqa: BLE001 - failure isolation between jobs
            status = "failed"
            entry = {
                "job_id": job.job_id,
                "operation_id": None,
                "receipt_id": None,
                "status": status,
                "error": type(exc).__name__,
            }
        job.last_run_at, job.last_status = now.isoformat(), status
        job.history = [*job.history[-19:], entry]
        nxt = next_run(job.schedule, now)
        job.next_run_at = nxt.isoformat() if nxt else None
        job.enabled = nxt is not None
        ran.append(entry)
    store.save(jobs)
    return ran
