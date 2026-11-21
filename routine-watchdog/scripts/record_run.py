#!/usr/bin/env python3
"""Append one line to a routine run log (JSONL).

A routine calls this at its start (--status started) and at its end (--status ok or failed).
The log is only ever appended to: this script never rewrites, truncates or deletes anything.

Exit codes: 0 one line written, 1 the line could not be written, 2 the arguments are wrong (nothing written).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

STATUSES = ("ok", "failed", "started")


class InputError(Exception):
    """The arguments are wrong. Nothing is written."""


def parse_time(text, field):
    raw = (text or "").strip()
    if raw and raw[-1] in "Zz":
        raw = raw[:-1] + "+00:00"
    raw = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", raw) if re.search(r"T.*[+-]\d{4}$", raw) else raw  # -0600 -> -06:00
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise InputError(f"{field} '{text}' is not a time like 2026-11-20T07:30:00-06:00") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise InputError(f"{field} '{text}' has no time zone offset. Add one, for example -06:00")
    return value


def build_line(routine, status, started, finished=None, records=None, message=None, host=None):
    """Check the arguments and return the one JSON line (without the newline)."""
    if status not in STATUSES:
        raise InputError(f"--status must be one of {', '.join(STATUSES)}, not '{status}'")
    name = (routine or "").strip()
    if not name:
        raise InputError("--routine is empty")
    start = parse_time(started, "--started")
    entry = {"routine": name, "status": status, "started": start.isoformat()}
    if status == "started":
        if finished or records is not None:
            raise InputError("--status started takes no --finished and no --records")
    else:
        if not finished:
            raise InputError(f"--status {status} needs --finished")
        end = parse_time(finished, "--finished")
        if end < start.astimezone(end.tzinfo):
            raise InputError(f"--finished {finished} is before --started {started}")
        entry["finished"] = end.isoformat()
        if records is not None:
            if isinstance(records, bool) or not isinstance(records, int) or records < 0:
                raise InputError("--records must be a whole number, 0 or more")
            entry["records"] = records
    if message:
        entry["message"] = " ".join(message.split())
    if host:
        entry["host"] = host.strip()
    return json.dumps(entry, ensure_ascii=False)


def append_line(path, line):
    """Add the line to the end of the log in a single write. Existing bytes are never touched."""
    data = line.encode("utf-8") + b"\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    except OSError as e:
        raise OSError(f"can't open the log for appending: {e}") from None
    try:
        # A crash can leave a last line with no newline. Start on a fresh line so our line stays whole.
        # The check_routines.py check will then name the damaged line instead of hiding it.
        with open(path, "rb") as probe:
            probe.seek(0, os.SEEK_END)
            if probe.tell() > 0:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    data = b"\n" + data
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"only {written} of {len(data)} bytes were written")
    except OSError as e:
        raise OSError(f"can't write to the log: {e}") from None
    finally:
        os.close(fd)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Append one run record to a routine log (JSONL). Append-only.")
    ap.add_argument("--log", required=True, help="the run log file (created if it does not exist)")
    ap.add_argument("--routine", required=True, help="routine name, exactly as in routines.json")
    ap.add_argument("--status", required=True, help="ok, failed or started")
    ap.add_argument("--started", required=True, help="start time with offset, e.g. 2026-11-20T07:30:00-06:00")
    ap.add_argument("--finished", help="finish time with offset (needed for ok and failed)")
    ap.add_argument("--records", type=int, help="how many records the routine handled")
    ap.add_argument("--message", help="what went wrong, in the site's or script's own words")
    ap.add_argument("--host", help="name of the computer it ran on")
    args = ap.parse_args(argv)
    try:
        line = build_line(args.routine, args.status, args.started, args.finished, args.records, args.message, args.host)
    except InputError as e:
        print(f"Not written: {e}", file=sys.stderr)
        return 2
    try:
        append_line(args.log, line)
    except OSError as e:
        print(f"Not written: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
