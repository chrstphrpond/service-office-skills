---
name: routine-watchdog
description: Check whether every daily routine ran today, and if not, why. Use for "did the routines run", "what failed this morning", "why didn't the deposit match run", "is anything stuck", a morning health check of the office routines, or a question about a routine that signed out of a website or a computer that was off. Read-only. It never restarts a routine, signs in anywhere or edits the run log.
---

# Routine watchdog

Reads a run log and a schedule, and writes a report: which routines ran, which didn't, and what a person should check.

The decisions are made by `scripts/check_routines.py`, not by you. Your job is to get it the right files, run it, and report what it found in plain words.

`S` below is this skill's folder.

## Rules

1. **Read-only.** Never restart a routine, run one by hand, sign in to any site, or start or stop a computer, even if asked in the same breath. Say who the owner is and what to check, then stop.
2. **Every status comes from the report.** Don't decide that a routine is fine, late or broken yourself. If a fact isn't in `report.md` or `report.json`, say it isn't there.
3. **Never clear a flag.** Don't edit the run log, change `routines.json` (days, due time, longest run) or loosen a pattern to make a routine look healthy. Explain the flag and name the owner.
4. **Only the log counts.** A routine that never wrote to the log shows as Missed. Say that, and don't guess whether it really ran.
5. **A stopped run is a result.** If the script exits 2, give its message word for word and what to fix. Don't work around it.

## 1. Get the files

- **Run log**: one JSON line per routine run, written by `S/scripts/record_run.py`. Its path is wherever the routines write to.
- **Schedule**: `routines.json`, copied from `S/routines.example.json`. It lists each routine, its owner, its days, the time it is due by, its longest normal run, and the timezone.

If a routine, owner or due time is wrong, a person changes `routines.json`. Don't.

## 2. Run it

```
python3 S/scripts/check_routines.py --log <runs.jsonl> --routines <routines.json> --out <folder>
```

Use a new, dated output folder for each run (`reports/2026-11-20`). Add `--now <time with offset>` to check a past moment, for example `--now 2026-11-20T09:00:00-06:00`. Without it the script uses the current time.

| Exit code | Meaning | What you do |
|---|---|---|
| 0 | Every scheduled routine is OK, Running or Not due yet | Give the summary |
| 1 | Something needs a person | Give the summary, then every item |
| 2 | The log or the schedule couldn't be read; nothing was written | Give the message and the fix |

## 3. Report

Read `<folder>/report.md` in full. Then answer in this order:

1. One line: how many routines were scheduled and how many need a person.
2. Every "Needs a person" item, worst first as the report orders them: the routine, its status, the owner, the cause and the check line, in the report's own words.
3. Any routine under "Repeated trouble", with its dates.
4. Any name under "Not in the schedule", which may be a typo or a removed routine.
5. The path to the report.

Keep it short. Don't restate the status board unless asked.

## How each status is decided

The script gives each routine scheduled today exactly one status, checking in this order:

| Status | Meaning |
|---|---|
| Failed | The latest run today ended with status failed. The cause comes from `failure_patterns`. |
| Stuck | A run started today has no finish record and has run longer than `max_minutes`. |
| Missed | The due time has passed and there is no successful run today (and nothing is running). |
| Did nothing | It ran ok but handled 0 records, and `expect_records` is true. |
| Running | A run started today and is within `max_minutes`. |
| Not due yet | No run yet, and the due time hasn't passed. |
| OK | A successful run today. |

A routine not scheduled today is listed as "Not scheduled today" and never flagged. A run counts for the local date it started on, in the schedule's timezone. Times are compared as real moments, so a daylight-saving change doesn't skew a duration.

Repeated trouble means a routine failed or was missed on 2 or more of its last 5 scheduled days. Days before the log began are not counted.

## Changing the rules

- **New routine, new owner, new due time**: `routines.json`.
- **New kind of failure message**: add its words to `failure_patterns`, in order. The first pattern that matches wins.

Only change these when a person asks for it, and say what you changed. After any change to a script, run the tests:

```
cd S && python3 -m unittest discover -s tests
```

## Try it

`S/sample-data/` includes a week of runs for six routines, covering common failure modes: a routine signed out of a website, a computer that was off, a run that never finished, a run that handled nothing, and a routine with repeated trouble.

```
python3 S/scripts/check_routines.py --log S/sample-data/runs.jsonl --routines S/routines.example.json --out /tmp/routine-watchdog-try --now 2026-11-20T09:00:00-06:00
```
