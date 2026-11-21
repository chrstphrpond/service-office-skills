#!/usr/bin/env python3
"""Answer "did every routine run today, and if not, why?" from a run log.

Reads the run log (JSONL, written by record_run.py) and routines.json (the schedule),
and writes report.md and report.json for one moment. It changes nothing: it never restarts a
routine, signs in anywhere or edits the log.

Exit codes: 0 every scheduled routine is OK, Running or Not due yet, 1 something needs a person,
2 the input could not be read (nothing written).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
STATUSES = ["Failed", "Stuck", "Missed", "Did nothing", "Running", "Not due yet", "OK"]
NEEDS_PERSON = ["Failed", "Stuck", "Missed", "Did nothing"]
NOT_SCHEDULED = "Not scheduled today"
LOG_STATUSES = ("ok", "failed", "started")
LOG_FIELDS = {"routine", "status", "started", "finished", "records", "message", "host"}
ROUTINE_FIELDS = {"name", "owner", "days", "due_by", "max_minutes", "expect_records"}
TOP_FIELDS = {"timezone", "failure_patterns", "routines"}
PATTERN_FIELDS = {"words", "cause", "check"}
TROUBLE_WINDOW = 5      # scheduled days looked at
TROUBLE_LIMIT = 2       # this many bad days in the window is "repeated trouble"
UNKNOWN_CAUSE = "Unknown, see message"
DEFAULT_FAILED_CHECK = "Read the message, then ask the owner."
CHECKS = {
    "Stuck": "Look at the computer that runs it. It may be frozen or waiting on a sign-in. Stop it and run it again by hand.",
    "Missed": "Make sure the computer that runs it is on and online, and that the routine is still scheduled.",
    "Did nothing": "Make sure the site or export it reads has data for today.",
}


class InputError(Exception):
    """A file or an argument is wrong. Stop; never skip a line silently."""


# ---------- times ----------

def parse_time(text, where, field):
    """Any ISO time with an offset, returned in UTC so every comparison is between real moments."""
    if not isinstance(text, str):
        raise InputError(f"{where}: {field} must be a time like 2026-11-20T07:30:00-06:00")
    raw = text.strip()
    if raw and raw[-1] in "Zz":
        raw = raw[:-1] + "+00:00"
    raw = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", raw) if re.search(r"T.*[+-]\d{4}$", raw) else raw  # -0600 -> -06:00
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise InputError(f"{where}: {field} '{text}' is not a time like 2026-11-20T07:30:00-06:00") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise InputError(f"{where}: {field} '{text}' has no time zone offset")
    return value.astimezone(UTC)


def parse_due(text, where):
    try:
        hh, mm = text.split(":")
        if len(hh) != 2 or len(mm) != 2:
            raise ValueError
        return dt.time(int(hh), int(mm))
    except (ValueError, AttributeError):
        raise InputError(f"{where}: due_by '{text}' is not a time like 07:30") from None


def day_label(d):
    return f"{d:%b} {d.day}"


def clock(t, tz):
    return f"{t.astimezone(tz):%H:%M}"


def stamp(t, tz, today):
    """07:12 for a time today, Nov 18 07:12 for any other day."""
    local = t.astimezone(tz)
    return clock(t, tz) if local.date() == today else f"{day_label(local.date())} {local:%H:%M}"


def minutes_text(seconds):
    m = int(seconds // 60)
    if m < 1:
        return "under 1 min"
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


# ---------- reading the schedule ----------

def load_schedule(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"can't read {path}: {e}") from None
    if not isinstance(data, dict):
        raise InputError("routines.json must be an object")
    unknown = set(data) - TOP_FIELDS
    if unknown:
        raise InputError(f"routines.json has unknown key(s): {', '.join(sorted(unknown))}")
    for key in TOP_FIELDS:
        if key not in data:
            raise InputError(f"routines.json is missing '{key}'")
    tz_name = data["timezone"]
    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, ValueError, OSError, TypeError):
        raise InputError(f"routines.json: unknown timezone '{tz_name}'. Use a name like America/Chicago") from None

    patterns = data["failure_patterns"]
    if not isinstance(patterns, list):
        raise InputError("routines.json: failure_patterns must be a list")
    clean_patterns = []
    for i, p in enumerate(patterns, start=1):
        where = f"routines.json failure_patterns #{i}"
        if not isinstance(p, dict) or set(p) - PATTERN_FIELDS or not {"words", "cause"} <= set(p):
            raise InputError(f"{where}: needs 'words' and 'cause' (and may have 'check')")
        words = p["words"]
        if not isinstance(words, list) or not words or not all(isinstance(w, str) and w.strip() for w in words):
            raise InputError(f"{where}: 'words' must be a list of words")
        if not isinstance(p["cause"], str) or not p["cause"].strip():
            raise InputError(f"{where}: 'cause' is empty")
        if "check" in p and not isinstance(p["check"], str):
            raise InputError(f"{where}: 'check' must be text")
        clean_patterns.append({"words": [w.lower() for w in words], "cause": p["cause"].strip(), "check": (p.get("check") or "").strip()})

    routines = data["routines"]
    if not isinstance(routines, list) or not routines:
        raise InputError("routines.json: 'routines' must be a list with at least one routine")
    out, seen = [], set()
    for i, r in enumerate(routines, start=1):
        where = f"routines.json routine #{i}"
        if not isinstance(r, dict):
            raise InputError(f"{where}: must be an object")
        extra = set(r) - ROUTINE_FIELDS
        if extra:
            raise InputError(f"{where}: unknown key(s): {', '.join(sorted(extra))}")
        for key in ROUTINE_FIELDS:
            if key not in r:
                raise InputError(f"{where}: missing '{key}'")
        name = r["name"]
        if not isinstance(name, str) or not name.strip():
            raise InputError(f"{where}: name is empty")
        where = f"routines.json routine '{name}'"
        if name.strip().casefold() in seen:
            raise InputError(f"{where}: the name appears twice")
        seen.add(name.strip().casefold())
        if not isinstance(r["owner"], str) or not r["owner"].strip():
            raise InputError(f"{where}: owner is empty")
        days = r["days"]
        if not isinstance(days, list) or not days or any(d not in DAYS for d in days) or len(set(days)) != len(days):
            raise InputError(f"{where}: days must be a list of {', '.join(DAYS)} with no repeats")
        mm = r["max_minutes"]
        if isinstance(mm, bool) or not isinstance(mm, int) or mm <= 0:
            raise InputError(f"{where}: max_minutes must be a whole number above 0")
        if not isinstance(r["expect_records"], bool):
            raise InputError(f"{where}: expect_records must be true or false")
        out.append({"name": name.strip(), "owner": r["owner"].strip(), "days": days, "due_by": parse_due(r["due_by"], where),
                    "due_text": r["due_by"], "max_minutes": mm, "expect_records": r["expect_records"]})
    return {"tz": tz, "tz_name": tz_name, "patterns": clean_patterns, "routines": out}


# ---------- reading the log ----------

def load_log(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"can't read the run log {path}: {e}") from None
    out = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"run log line {n}"
        try:
            row = json.loads(line)
        except ValueError:
            raise InputError(f"{where}: not valid JSON") from None
        if not isinstance(row, dict):
            raise InputError(f"{where}: must be a JSON object")
        extra = set(row) - LOG_FIELDS
        if extra:
            raise InputError(f"{where}: unknown field(s): {', '.join(sorted(extra))}")
        name = row.get("routine")
        if not isinstance(name, str) or not name.strip():
            raise InputError(f"{where}: no routine name")
        status = row.get("status")
        if status not in LOG_STATUSES:
            raise InputError(f"{where}: status '{status}' is not one of {', '.join(LOG_STATUSES)}")
        started = parse_time(row.get("started"), where, "started")
        finished = None
        if status == "started":
            if row.get("finished") is not None or row.get("records") is not None:
                raise InputError(f"{where}: a started record can't have finished or records")
        else:
            if row.get("finished") is None:
                raise InputError(f"{where}: status {status} needs a finished time")
            finished = parse_time(row["finished"], where, "finished")
            if finished < started:
                raise InputError(f"{where}: finished is before started")
        records = row.get("records")
        if records is not None and (isinstance(records, bool) or not isinstance(records, int) or records < 0):
            raise InputError(f"{where}: records must be a whole number, 0 or more")
        for key in ("message", "host"):
            if row.get(key) is not None and not isinstance(row[key], str):
                raise InputError(f"{where}: {key} must be text")
        out.append({"line": n, "routine": name.strip(), "status": status, "started": started, "finished": finished,
                    "records": records, "message": (row.get("message") or "").strip(), "host": (row.get("host") or "").strip()})
    return out


def build_runs(records, now, notes):
    """One run per (routine, start time). A finish record replaces its start record.
    As of `now`: runs that start later are ignored, and runs that finish later are still open."""
    runs = {}
    for r in records:
        key = (r["routine"], r["started"])
        have = runs.get(key)
        if r["status"] == "started":
            if have is None:
                runs[key] = {**r, "status": "open"}
            continue
        if have is not None and have["status"] != "open":
            notes.append(f"Run log lines {have['line']} and {r['line']} both finish the same {r['routine']} run. The later line is used.")
        runs[key] = {**r, "host": r["host"] or (have or {}).get("host", "")}
    visible = []
    for run in runs.values():
        if run["started"] > now:
            continue
        if run["finished"] is not None and run["finished"] > now:
            run = {**run, "status": "open", "finished": None, "records": None, "message": ""}
        visible.append(run)
    return sorted(visible, key=lambda x: (x["started"], x["line"]))


# ---------- deciding ----------

def classify(message, patterns):
    low = message.lower()
    for p in patterns:
        if any(w in low for w in p["words"]):
            return p["cause"], p["check"] or DEFAULT_FAILED_CHECK
    return UNKNOWN_CAUSE, DEFAULT_FAILED_CHECK


def due_moment(routine, day, tz):
    return dt.datetime.combine(day, routine["due_by"], tzinfo=tz).astimezone(UTC)


def local_day(t, tz):
    return t.astimezone(tz).date()


def live_open_runs(runs):
    """Open runs, except those followed by a later run that finished (that one was replaced)."""
    done = [r["started"] for r in runs if r["status"] != "open"]
    return [r for r in runs if r["status"] == "open" and not any(d > r["started"] for d in done)]


def decide_today(routine, runs, now, tz, patterns):
    """Exactly one status for a routine scheduled today. `runs` are its runs that started today."""
    limit = dt.timedelta(minutes=routine["max_minutes"])
    due_passed = now > due_moment(routine, local_day(now, tz), tz)
    ok_runs = [r for r in runs if r["status"] == "ok"]
    live = live_open_runs(runs)
    stuck = [r for r in live if now - r["started"] > limit]
    running = [r for r in live if now - r["started"] <= limit]
    latest = runs[-1] if runs else None
    base = {"status": None, "cause": "", "check": "", "shown": None}

    if latest and latest["status"] == "failed":
        cause, check = classify(latest["message"], patterns)
        return {**base, "status": "Failed", "cause": cause, "check": check, "shown": latest}
    if stuck:
        return {**base, "status": "Stuck", "check": CHECKS["Stuck"], "shown": stuck[0]}
    if due_passed and not ok_runs and not running:
        return {**base, "status": "Missed", "check": CHECKS["Missed"]}
    if ok_runs and routine["expect_records"] and ok_runs[-1]["records"] == 0:
        return {**base, "status": "Did nothing", "check": CHECKS["Did nothing"], "shown": ok_runs[-1]}
    if running:
        return {**base, "status": "Running", "shown": running[-1]}
    if not ok_runs:
        return {**base, "status": "Not due yet"}
    return {**base, "status": "OK", "shown": ok_runs[-1]}


def day_outcome(runs):
    """For a past day: 'failed', 'missed' or 'ok'."""
    if runs and runs[-1]["status"] == "failed":
        return "failed"
    return "ok" if any(r["status"] == "ok" for r in runs) else "missed"


def repeated_trouble(routine, all_runs, today, today_result, first_day, tz):
    """Look at the last scheduled days (today included once its time has come). Return the bad ones."""
    mine = [r for r in all_runs if r["routine"] == routine["name"]]
    by_day = {}
    for r in mine:
        by_day.setdefault(local_day(r["started"], tz), []).append(r)
    counted = []
    d = today
    for _ in range(400):
        if len(counted) >= TROUBLE_WINDOW or d < first_day:
            break
        if DAYS[d.weekday()] in routine["days"]:
            if d == today:
                s = today_result["status"] if today_result else None
                if s is not None and s != "Not due yet":
                    counted.append((d, {"Failed": "failed", "Missed": "missed"}.get(s, "ok")))
            else:
                counted.append((d, day_outcome(by_day.get(d, []))))
        d -= dt.timedelta(days=1)
    bad = sorted((d, o) for d, o in counted if o != "ok")
    return {"days_checked": len(counted), "bad": bad} if len(bad) >= TROUBLE_LIMIT else None


# ---------- the whole check ----------

def prove(condition, message):
    if not condition:
        raise AssertionError(message)


def check(schedule, records, now):
    tz, patterns = schedule["tz"], schedule["patterns"]
    notes = []
    runs = build_runs(records, now, notes)
    today = local_day(now, tz)
    first_day = min((local_day(r["started"], tz) for r in runs), default=today)  # no history before this
    known = {r["name"] for r in schedule["routines"]}

    results = []
    for routine in schedule["routines"]:
        mine = [r for r in runs if r["routine"] == routine["name"]]
        today_runs = [r for r in mine if local_day(r["started"], tz) == today]
        finished = [r for r in mine if r["status"] != "open"]
        last_finish = max((r["finished"] for r in finished), default=None)
        res = {"name": routine["name"], "owner": routine["owner"], "due_by": routine["due_text"],
               "scheduled": DAYS[today.weekday()] in routine["days"], "max_minutes": routine["max_minutes"],
               "last_finish": last_finish, "last_record": mine[-1] if mine else None}
        if res["scheduled"]:
            res.update(decide_today(routine, today_runs, now, tz, patterns))
        else:
            res.update({"status": NOT_SCHEDULED, "cause": "", "check": "", "shown": None})
        res["trouble"] = repeated_trouble(routine, runs, today, res if res["scheduled"] else None, first_day, tz)
        results.append(res)

    unknown = {}
    for r in runs:
        if r["routine"] not in known:
            u = unknown.setdefault(r["routine"], {"routine": r["routine"], "runs": 0, "last_started": r["started"]})
            u["runs"] += 1
            u["last_started"] = max(u["last_started"], r["started"])
    for r in live_open_runs(runs):
        if local_day(r["started"], tz) < today:
            notes.append(f"{r['routine']} has a run that started {stamp(r['started'], tz, today)} and has no finish record.")

    scheduled = [r for r in results if r["scheduled"]]
    counts = {s: sum(1 for r in scheduled if r["status"] == s) for s in STATUSES}
    prove(all(r["status"] in STATUSES for r in scheduled), "a scheduled routine has no status")
    prove(sum(counts.values()) == len(scheduled), "status counts do not add up to the scheduled routines")
    prove(all(r["status"] == NOT_SCHEDULED for r in results if not r["scheduled"]), "an unscheduled routine got a status")
    prove(len(results) == len(schedule["routines"]), "a routine is missing from the results")
    return {"now": now, "tz": tz, "tz_name": schedule["tz_name"], "today": today, "results": results,
            "counts": counts, "scheduled": len(scheduled), "patterns": patterns, "unknown": sorted(unknown.values(), key=lambda u: u["routine"]),
            "notes": notes}


def needs_person(result):
    return any(result["counts"][s] for s in NEEDS_PERSON)


# ---------- writing ----------

def cell(text):
    return str(text).replace("|", "/").replace("\n", " ")


def host_of(run):
    return run["host"] if run and run["host"] else "-"


def what_happened(r, result):
    tz, today = result["tz"], result["today"]
    """One short line saying what the log shows for a routine that needs a person."""
    run = r["shown"]
    if r["status"] == "Failed":
        msg = f' Message: "{run["message"]}".' if run["message"] else " No message was recorded."
        return f"Failed at {clock(run['finished'], tz)} on {host_of(run)}. Cause: {r['cause']}.{msg}"
    if r["status"] == "Stuck":
        return (f"Started {clock(run['started'], tz)} on {host_of(run)} and has not finished. "
                f"The longest normal run is {r['max_minutes']} min.")
    if r["status"] == "Did nothing":
        return f"Finished {clock(run['finished'], tz)} on {host_of(run)} with 0 records, and this routine normally handles some."
    last = r["last_record"]
    if last is None:
        return f"Due by {r['due_by']}. Nothing is recorded for this routine in the log."
    seen = f"{last['status'] if last['status'] != 'open' else 'started'} {stamp(last['finished'] or last['started'], tz, today)} on {host_of(last)}"
    if last["status"] == "failed":
        seen += f" ({classify(last['message'], result['patterns'])[0]})"
    return f"Due by {r['due_by']}. Nothing ran today. Last record: {seen}."


def board_row(r, result):
    tz, today = result["tz"], result["today"]
    run = r["shown"]
    last = stamp(r["last_finish"], tz, today) if r["last_finish"] else "-"
    if r["status"] == NOT_SCHEDULED:
        return [r["name"], r["status"], last, "-", "-", "-"]
    if run and run["status"] == "open":
        took = f"running {minutes_text((result['now'] - run['started']).total_seconds())}"
    elif run:
        took = minutes_text((run["finished"] - run["started"]).total_seconds())
    else:
        took = "-"
    records = "-" if not run or run["records"] is None else str(run["records"])
    return [r["name"], r["status"], last, took, records, host_of(run)]


def render(result):
    tz, today, now = result["tz"], result["today"], result["now"]
    counts, results = result["counts"], result["results"]
    needs = [r for r in results if r["scheduled"] and r["status"] in NEEDS_PERSON]
    n_need = len(needs)
    L = [f"# Routine watchdog, {day_label(today)}, {today.year} at {clock(now, tz)}", "",
         f"Time zone: {result['tz_name']}. Log checked up to {clock(now, tz)}.", ""]
    if result["scheduled"] == 0:
        L += ["**No routines are scheduled today.**", ""]
    else:
        L += [f"**{result['scheduled']} routines scheduled today: {counts['OK']} OK, {n_need} need a person.**", ""]
    L += ["| Status | Routines |", "|---|---:|"]
    L += [f"| {s} | {counts[s]} |" for s in STATUSES]
    L += [f"| {NOT_SCHEDULED} | {sum(1 for r in results if not r['scheduled'])} |", ""]

    L += ["## Needs a person", ""]
    if not needs:
        L += ["Nothing.", ""]
    order = {s: i for i, s in enumerate(NEEDS_PERSON)}
    for r in sorted(needs, key=lambda x: order[x["status"]]):
        L += [f"- **{r['name']}**, {r['status']}. Owner: {r['owner']}.",
              f"  - {what_happened(r, result)}",
              f"  - Check: {r['check']}"]
    if needs:
        L += [""]

    L += ["## Status board", "", "| Routine | Status | Last finish | Took | Records | Computer |", "|---|---|---|---:|---:|---|"]
    for r in results:
        L += ["| " + " | ".join(cell(c) for c in board_row(r, result)) + " |"]
    L += [""]

    L += ["## Repeated trouble", ""]
    trouble = [r for r in results if r["trouble"]]
    if not trouble:
        L += [f"None. No routine failed or missed on {TROUBLE_LIMIT} or more of its last {TROUBLE_WINDOW} scheduled days.", ""]
    else:
        L += ["| Routine | Owner | Bad days | Days checked |", "|---|---|---|---:|"]
        for r in trouble:
            days = ", ".join(f"{day_label(d)} {o}" for d, o in r["trouble"]["bad"])
            L += [f"| {cell(r['name'])} | {cell(r['owner'])} | {days} | {r['trouble']['days_checked']} |"]
        L += [""]

    if result["unknown"]:
        L += ["## Not in the schedule", "", "The log has runs for names that are not in routines.json. Check for a typo or a routine that was removed.", "",
              "| Name in the log | Runs | Last started |", "|---|---:|---|"]
        L += [f"| {cell(u['routine'])} | {u['runs']} | {stamp(u['last_started'], tz, today)} |" for u in result["unknown"]]
        L += [""]
    if result["notes"]:
        L += ["## Notes", ""] + [f"- {n}" for n in result["notes"]] + [""]

    L += ["This report is read-only. Nothing was restarted, signed in to or changed, and the run log was not edited."]
    return "\n".join(L) + "\n"


def as_json(result):
    tz, today = result["tz"], result["today"]

    def local(t):
        return t.astimezone(tz).isoformat() if t else None

    routines = []
    for r in result["results"]:
        run = r["shown"]
        seconds = None
        if run and run["finished"]:
            seconds = int((run["finished"] - run["started"]).total_seconds())
        elif run:
            seconds = int((result["now"] - run["started"]).total_seconds())
        routines.append({
            "name": r["name"], "owner": r["owner"], "scheduled_today": r["scheduled"], "due_by": r["due_by"],
            "status": r["status"], "needs_person": r["scheduled"] and r["status"] in NEEDS_PERSON,
            "cause": r["cause"] or None, "check": r["check"] or None,
            "message": run["message"] or None if run else None, "host": run["host"] or None if run else None,
            "run_started": local(run["started"]) if run else None, "run_finished": local(run["finished"]) if run else None,
            "seconds": seconds, "run_still_open": bool(run and run["status"] == "open"),
            "records": run["records"] if run else None, "last_finish": local(r["last_finish"]),
            "repeated_trouble": None if not r["trouble"] else {
                "days_checked": r["trouble"]["days_checked"],
                "bad_days": [{"date": d.isoformat(), "outcome": o} for d, o in r["trouble"]["bad"]]}})
    return {"now": local(result["now"]), "timezone": result["tz_name"], "local_date": today.isoformat(),
            "scheduled_today": result["scheduled"], "counts": result["counts"],
            "not_scheduled_today": sum(1 for r in result["results"] if not r["scheduled"]),
            "needs_person_count": sum(result["counts"][s] for s in NEEDS_PERSON),
            "routines": routines,
            "not_in_schedule": [{"routine": u["routine"], "runs": u["runs"], "last_started": local(u["last_started"])} for u in result["unknown"]],
            "notes": result["notes"]}


def summary_line(result):
    c = result["counts"]
    bits = ", ".join(f"{c[s]} {s}" for s in STATUSES if c[s])
    return f"{result['scheduled']} scheduled today: {bits or 'none'}."


def main(argv=None):
    ap = argparse.ArgumentParser(description="Did every routine run today, and if not, why? Read-only.")
    ap.add_argument("--log", required=True, help="the run log (JSONL)")
    ap.add_argument("--routines", required=True, help="the schedule (routines.json)")
    ap.add_argument("--out", required=True, help="folder for report.md and report.json")
    ap.add_argument("--now", help="the moment to check, with offset (default: the current time)")
    args = ap.parse_args(argv)
    try:
        now = parse_time(args.now, "--now", "time") if args.now else dt.datetime.now(UTC)
        schedule = load_schedule(args.routines)
        result = check(schedule, load_log(args.log), now)
        md = render(result)
        js = json.dumps(as_json(result), indent=1)
    except InputError as e:
        print(f"Stopped, nothing written: {e}", file=sys.stderr)
        return 2
    except AssertionError as e:
        print(f"Stopped, nothing written: internal check failed: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.json").write_text(js, encoding="utf-8")
    print(f"{summary_line(result)} Report: {out / 'report.md'}")
    return 1 if needs_person(result) else 0


if __name__ == "__main__":
    sys.exit(main())
