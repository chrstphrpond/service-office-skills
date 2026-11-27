---
name: jobs-review
description: Review yesterday's completed jobs and list what a person has to look at, grouped by check and by technician. Use for "review yesterday's jobs", "check the completed jobs", "which jobs have no invoice", "any callbacks yesterday", "job audit", a daily jobs check, or a question about a missing invoice, a zero-dollar invoice, a balance still due, thin notes or a big discount on a job. Read-only. It never edits a job, invoice or payment in ServiceTitan, QuickBooks or anywhere else.
---

# Jobs review

Reads one export of completed jobs and writes a report: which jobs a person has to look at, why, and which technician each one belongs to.

The checking is done by `scripts/review_jobs.py`, not by you. Your job is to get it the right file, run it, and report what it found in plain words.

`S` below is this skill's folder.

## Rules

1. **Read-only.** Never edit a job, invoice, note or payment in any system, even if asked in the same breath. Say what a person should do and stop.
2. **Every number comes from the report.** Don't add, subtract, round, average or estimate amounts or counts yourself. If a figure isn't in `report.md` or `report.json`, say it isn't there.
3. **Never clear a flag.** Don't edit the CSV, loosen a rule in the config or change a list to make a job pass. Explain the flag and who would know the answer (the office manager, the technician, the owner).
4. **A flag is not a verdict.** Say "needs a look", not "the technician did something wrong". A callback or a discount may be fine.
5. **A stopped run is a result.** If the script exits 2, give its message word for word and what to fix. Don't work around it.

## 1. Get the file

You need one CSV export of the jobs completed on the day (or days) to review. One row per job. It needs:

- job number, completed date, customer, technician, job type, status, invoice number, invoice total, balance due.

These are optional. If one is missing, its check is not run and the report says so:

- payment type, summary notes, recall-of job number, discount amount.

If the column names differ from `S/config.example.json`, copy that file and change the names under `columns`. Change the config, never the data.

Job numbers must be unique in the file. Blank amounts count as zero. Invoice totals can't be negative.

## 2. Run it

```
python3 S/scripts/review_jobs.py --jobs <jobs.csv> --config <config.json> --out <folder>
```

Use a new, dated output folder for each run (`reports/2026-11-20`).

| Exit code | Meaning | What you do |
|---|---|---|
| 0 | Nothing flagged | Give the summary |
| 1 | Something needs a person | Give the summary, then every item |
| 2 | The file or the config couldn't be read; nothing was written | Give the message and the fix |

## 3. Report

Read `<folder>/report.md` in full. Then answer in this order:

1. One line: jobs reviewed, jobs flagged, and the totals invoiced and balance due, as the report shows them.
2. Every "needs a person" item by check: the job number, customer, technician, the amounts, and the reason.
3. If asked, one technician's list from "By technician". Use `report.json` for a per-person dashboard.
4. Any check the report says was not run, and why.
5. The path to the report.

Keep it short. Don't list jobs that had no flag.

## What gets checked

| Check | A job is flagged when |
|---|---|
| No invoice | It is completed and has no invoice number |
| Zero-dollar invoice | The invoice is $0.00 and the job type isn't on the no-charge list (Warranty, Recall) |
| Balance due | A balance is still due and the payment type isn't on the billed-later list (On account, Financing) |
| Short notes | The summary is blank or shorter than the minimum length |
| Callback | It is a recall of an earlier job. The report shows the original job, and its technician and date when the original is in the same file |
| Below minimum | The invoice total is under the minimum for its job type (for example the dispatch fee on a service call) |
| Big discount | The discount is more than the allowed percent of the total before the discount |
| Duplicate invoice | The same invoice number is on more than one job |

Jobs whose status isn't a completed status are counted and skipped. A job can have more than one flag. Two jobs with the same job number stop the run (exit 2).

## Changing the rules

All of these are in the config: `completed_statuses`, `no_charge_job_types`, `billed_later_payment_types`, `min_summary_length`, `minimum_invoice` (by job type) and `max_discount_percent`.

Only change them when a person asks for it, and say what you changed. After any change to the script, run the tests:

```
cd S && python3 -m unittest discover -s tests
```

## Try it

`S/sample-data/jobs.csv` covers a full day of dispatch records (Nov 20, 2026: 26 rows, 5 technicians, 2 jobs not completed) with common audit exceptions: a missing invoice, a zero-dollar service call, a balance due on a check, thin notes, two callbacks, a service call under the dispatch fee, a 20% discount and an invoice number used twice.

```
python3 S/scripts/review_jobs.py --jobs S/sample-data/jobs.csv --config S/config.example.json --out /tmp/jobs-review-try
```
