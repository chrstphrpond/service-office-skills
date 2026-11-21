#!/usr/bin/env python3
"""Review a day's completed jobs and list what a person has to look at.

Reads one CSV export of jobs, writes report.md and report.json. It changes nothing in any system.
Anything it can't prove is fine goes on a list for a person.

Exit codes: 0 nothing flagged, 1 something needs a person, 2 the input could not be read.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


class InputError(Exception):
    """The file or the config is wrong. Stop; never skip a row silently."""


# ---------- money and dates ----------

def to_cents(text, where):
    """'$1,245.50' -> 124550. Parentheses or a minus sign mean negative. No floats anywhere."""
    raw = (text or "").strip()
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()").replace("$", "").replace(",", "").strip()
    if cleaned.startswith("-"):
        negative, cleaned = not negative, cleaned[1:]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise InputError(f"{where}: '{raw}' is not an amount") from None
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise InputError(f"{where}: '{raw}' is not an amount in dollars and cents")
    amount = int(value * 100)
    return -amount if negative else amount


def money(cents):
    return f"{'-' if cents < 0 else ''}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def to_date(text, fmt, where):
    try:
        return dt.datetime.strptime((text or "").strip(), fmt).date()
    except ValueError:
        raise InputError(f"{where}: '{text}' is not a date in the format {fmt}") from None


def norm(text):
    return (text or "").strip().casefold()


def percent(text, where):
    try:
        value = Decimal(str(text).strip().rstrip("%"))
    except InvalidOperation:
        raise InputError(f"{where}: '{text}' is not a percent") from None
    if not value.is_finite() or value < 0 or value > 100:
        raise InputError(f"{where}: '{text}' is not a percent between 0 and 100")
    return value


# ---------- checks, in report order ----------

CHECKS = [
    ("no_invoice", "Completed with no invoice number"),
    ("zero_dollar", "Zero-dollar invoice on a job that should be billed"),
    ("balance_due", "Balance still due, payment type not billed later"),
    ("short_notes", "Summary notes missing or too short"),
    ("callback", "Callback: a return visit for an earlier job"),
    ("below_minimum", "Invoice total below the minimum for the job type"),
    ("big_discount", "Discount above the allowed percent"),
    ("duplicate_invoice", "Invoice number used on more than one job"),
]
TITLES = dict(CHECKS)

# Columns that must be in the file, and the optional ones. An absent optional column skips its check.
REQUIRED = ["job", "completed", "customer", "technician", "job_type", "status", "invoice", "total", "balance"]
OPTIONAL = {"payment_type": "balance_due", "summary": "short_notes", "recall_of": "callback", "discount": "big_discount"}


# ---------- reading the export ----------

def load_jobs(path, config):
    """Return (jobs, absent). `absent` lists the optional columns the file doesn't have."""
    cols, fmt = config["jobs"]["columns"], config["jobs"]["date_format"]
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [cols[k] for k in REQUIRED if not cols.get(k) or cols[k] not in header]
            if missing:
                raise InputError(f"the jobs file is missing column(s): {', '.join(missing)}. It has: {', '.join(header)}")
            rows = [(n, row) for n, row in enumerate(reader, start=2) if any((v or "").strip() for v in row.values())]
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise InputError(f"can't read the jobs file: {e}") from None
    absent = [k for k in OPTIONAL if not cols.get(k) or cols[k] not in header]
    jobs, seen = [], {}
    for n, row in rows:
        where = f"jobs row {n}"
        get = lambda key: (row.get(cols.get(key) or "") or "").strip()
        number = get("job")
        if not number:
            raise InputError(f"{where}: no job number")
        if norm(number) in seen:
            raise InputError(f"{where}: job number {number} appears twice in the export (also row {seen[norm(number)]})")
        seen[norm(number)] = n
        total = to_cents(get("total"), f"{where} invoice total") if get("total") else 0
        balance = to_cents(get("balance"), f"{where} balance due") if get("balance") else 0
        if total < 0:
            raise InputError(f"{where}: invoice total {money(total)} is negative. Credits aren't handled; fix the export or review it by hand")
        discount = None
        if "discount" not in absent:
            discount = to_cents(get("discount"), f"{where} discount") if get("discount") else 0
            if discount < 0:
                raise InputError(f"{where}: discount {money(discount)} is negative. Give discounts as positive amounts")
        jobs.append({
            "row": n, "job": number, "date": to_date(get("completed"), fmt, where), "customer": get("customer"),
            "technician": get("technician"), "job_type": get("job_type"), "status": get("status"),
            "invoice": get("invoice"), "total": total, "balance": balance,
            "payment_type": None if "payment_type" in absent else get("payment_type"),
            "summary": None if "summary" in absent else get("summary"),
            "recall_of": None if "recall_of" in absent else get("recall_of"),
            "discount": discount,
        })
    return jobs, absent


def read_config(path):
    try:
        config = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise InputError(f"can't read the config: {e}") from None
    for key in ("jobs", "completed_statuses", "no_charge_job_types", "billed_later_payment_types",
                "min_summary_length", "minimum_invoice", "max_discount_percent"):
        if key not in config:
            raise InputError(f"the config is missing '{key}'")
    if not isinstance(config["min_summary_length"], int) or config["min_summary_length"] < 0:
        raise InputError("the config's min_summary_length must be a whole number, 0 or more")
    percent(config["max_discount_percent"], "the config's max_discount_percent")
    for job_type, amount in config["minimum_invoice"].items():
        to_cents(amount, f"the config's minimum_invoice for {job_type}")
    return config


# ---------- review ----------

def review(jobs, config, absent=()):
    completed = {norm(s) for s in config["completed_statuses"]}
    no_charge = {norm(s) for s in config["no_charge_job_types"]}
    billed_later = {norm(s) for s in config["billed_later_payment_types"]}
    minimums = {norm(k): to_cents(v, f"minimum_invoice for {k}") for k, v in config["minimum_invoice"].items()}
    max_pct = percent(config["max_discount_percent"], "max_discount_percent")
    min_len = config["min_summary_length"]

    reviewed = [j for j in jobs if norm(j["status"]) in completed]
    skipped = [j for j in jobs if norm(j["status"]) not in completed]
    by_number = {norm(j["job"]): j for j in jobs}
    by_invoice = {}
    for j in reviewed:
        if j["invoice"]:
            by_invoice.setdefault(norm(j["invoice"]), []).append(j)

    flags = {j["job"]: [] for j in reviewed}  # job number -> [{"check", "reason", ...}]

    def flag(job, check, reason, **extra):
        flags[job["job"]].append({"check": check, "reason": reason, **extra})

    for j in reviewed:
        has_invoice = bool(j["invoice"])
        if not has_invoice:
            flag(j, "no_invoice", "Marked completed but has no invoice number.")
        elif j["total"] == 0 and norm(j["job_type"]) not in no_charge:
            flag(j, "zero_dollar", f"Invoice {j['invoice']} is $0.00 and {j['job_type'] or 'this job type'} is not on the no-charge list "
                                   f"({', '.join(config['no_charge_job_types'])}).")
        if j["payment_type"] is not None and j["balance"] > 0 and norm(j["payment_type"]) not in billed_later:
            flag(j, "balance_due", f"{money(j['balance'])} is still due and the payment type '{j['payment_type'] or 'blank'}' "
                                   f"is not on the billed-later list ({', '.join(config['billed_later_payment_types'])}).")
        if j["summary"] is not None and len(j["summary"]) < min_len:
            what = "are missing" if not j["summary"] else f"are {len(j['summary'])} characters"
            flag(j, "short_notes", f"Summary notes {what}. The minimum is {min_len}.")
        if j["recall_of"]:
            original = by_number.get(norm(j["recall_of"]))
            if original:
                tail = f"The original was done by {original['technician'] or 'no technician listed'} on {original['date']:%b %d, %Y}."
                flag(j, "callback", f"Callback: a recall of job {j['recall_of']}. {tail}", original_job=original["job"],
                     original_technician=original["technician"], original_date=original["date"].isoformat())
            else:
                flag(j, "callback", f"Callback: a recall of job {j['recall_of']}. The original job is not in this file.",
                     original_job=j["recall_of"], original_technician=None, original_date=None)
        floor = minimums.get(norm(j["job_type"]))
        if has_invoice and floor is not None and 0 < j["total"] < floor:
            flag(j, "below_minimum", f"Invoice total {money(j['total'])} is below the {money(floor)} minimum for {j['job_type']} jobs.")
        if j["discount"]:
            before = j["total"] + j["discount"]
            if Decimal(j["discount"]) * 100 > max_pct * Decimal(before):
                pct = (Decimal(j["discount"]) * 100 / Decimal(before)).quantize(Decimal("0.1"))
                flag(j, "big_discount", f"Discount {money(j['discount'])} is {pct}% of the {money(before)} before the discount. "
                                        f"The limit is {max_pct.normalize():f}%.")
        if has_invoice and len(by_invoice[norm(j["invoice"])]) > 1:
            others = [o["job"] for o in by_invoice[norm(j["invoice"])] if o is not j]
            flag(j, "duplicate_invoice", f"Invoice number {j['invoice']} is also on job {', '.join(others)}.", other_jobs=others)

    # Group the same flags two ways: by check and by technician.
    by_check = {key: [] for key, _ in CHECKS}
    for j in reviewed:
        for f in flags[j["job"]]:
            by_check[f["check"]].append({"job": j["job"], "customer": j["customer"], "technician": j["technician"],
                                         "job_type": j["job_type"], "date": j["date"], "invoice": j["invoice"],
                                         "total": j["total"], "balance": j["balance"], **{k: v for k, v in f.items() if k != "check"}})
    techs = {}
    for j in reviewed:
        t = techs.setdefault(norm(j["technician"]), {"technician": j["technician"] or "(no technician)", "jobs": 0, "invoiced": 0,
                                                     "balance_due": 0, "flag_count": 0, "flagged_jobs": []})
        t["jobs"] += 1
        t["invoiced"] += j["total"]
        t["balance_due"] += j["balance"]
        if flags[j["job"]]:
            t["flag_count"] += len(flags[j["job"]])
            t["flagged_jobs"].append({"job": j["job"], "customer": j["customer"], "job_type": j["job_type"], "total": j["total"],
                                      "flags": [{"check": f["check"], "reason": f["reason"]} for f in flags[j["job"]]]})
    by_tech = sorted(techs.values(), key=lambda t: t["technician"].casefold())

    flagged_jobs = [j for j in reviewed if flags[j["job"]]]
    totals = {"invoiced": sum(j["total"] for j in reviewed), "balance_due": sum(j["balance"] for j in reviewed)}
    counts = {"rows": len(jobs), "reviewed": len(reviewed), "skipped": len(skipped), "flagged_jobs": len(flagged_jobs),
              "flags": sum(len(v) for v in flags.values()), "by_check": {k: len(v) for k, v in by_check.items()}}

    # The proof lines. If any fails the tool is wrong, and it must not write a report.
    assert counts["reviewed"] + counts["skipped"] == counts["rows"]
    assert sum(t["jobs"] for t in by_tech) == counts["reviewed"]
    assert sum(t["invoiced"] for t in by_tech) == totals["invoiced"]
    assert sum(t["balance_due"] for t in by_tech) == totals["balance_due"]
    assert sum(t["flag_count"] for t in by_tech) == counts["flags"] == sum(counts["by_check"].values())
    assert sum(len(t["flagged_jobs"]) for t in by_tech) == counts["flagged_jobs"]

    dates = [j["date"] for j in jobs]
    not_run = [{"check": OPTIONAL[k], "title": TITLES[OPTIONAL[k]], "missing_column": k} for k in OPTIONAL if k in absent]
    return {"first_date": min(dates) if dates else None, "last_date": max(dates) if dates else None, "counts": counts,
            "totals": totals, "by_check": by_check, "by_technician": by_tech, "skipped": skipped, "not_run": not_run}


# ---------- report ----------

def title_dates(r):
    if r["first_date"] is None:
        return "no jobs"
    if r["first_date"] == r["last_date"]:
        return f"{r['first_date']:%b %d, %Y}"
    return f"{r['first_date']:%b %d, %Y} to {r['last_date']:%b %d, %Y}"


def describe(i):
    return (f"**{i['job']}** {i['customer'] or '(no customer)'}, technician {i['technician'] or '(none)'}, {i['job_type'] or 'no job type'}, "
            f"invoice {i['invoice'] or '(none)'}, total {money(i['total'])}, balance due {money(i['balance'])}")


def render(r):
    c, t, L = r["counts"], r["totals"], []
    L += [f"# Jobs review, {title_dates(r)}", ""]
    L += ["| | Count |", "|---|---:|",
          f"| Jobs in the file | {c['rows']} |",
          f"| Jobs reviewed | {c['reviewed']} |",
          f"| Skipped, status not completed | {c['skipped']} |",
          f"| **Jobs with at least one flag** | **{c['flagged_jobs']}** |", ""]
    L += ["| Check | Jobs flagged |", "|---|---:|"]
    L += [f"| {title} | {c['by_check'][key]} |" for key, title in CHECKS]
    L += ["", "| | Amount |", "|---|---:|", f"| Total invoiced | {money(t['invoiced'])} |", f"| Total balance due | {money(t['balance_due'])} |", ""]
    L += [f"Check: {c['reviewed']} reviewed + {c['skipped']} skipped = {c['rows']} rows in the file. "
          f"The technicians' totals add up to {money(t['invoiced'])} invoiced and {money(t['balance_due'])} balance due."]
    for n in r["not_run"]:
        L += [f"Not run: \"{n['title']}\". The file has no {n['missing_column']} column."]
    L += [""]

    L += ["## Needs a person", ""]
    if not c["flags"]:
        L += ["Nothing.", ""]
    for key, title in CHECKS:
        items = r["by_check"][key]
        if not items:
            continue
        L += [f"### {title} ({len(items)})", ""]
        for i in items:
            L += [f"- {describe(i)}", f"  - {i['reason']}"]
        L += [""]

    L += ["## By technician", "", "| Technician | Jobs | Invoiced | Balance due | Flags |", "|---|---:|---:|---:|---:|"]
    L += [f"| {x['technician']} | {x['jobs']} | {money(x['invoiced'])} | {money(x['balance_due'])} | {x['flag_count']} |" for x in r["by_technician"]]
    L += [""]
    for x in r["by_technician"]:
        if not x["flagged_jobs"]:
            continue
        L += [f"### {x['technician']}", ""]
        for j in x["flagged_jobs"]:
            L += [f"- **{j['job']}** {j['customer']}, {j['job_type']}, {money(j['total'])}"]
            L += [f"  - {f['reason']}" for f in j["flags"]]
        L += [""]

    if r["skipped"]:
        L += ["## Skipped", ""]
        L += [f"- {j['job']} {j['customer']}, {j['technician']}, status '{j['status']}'" for j in r["skipped"]] + [""]
    L += ["This report is read-only. Nothing was changed in the field-service system, the books or anywhere else."]
    return "\n".join(L) + "\n"


def as_json(r):
    def item(i):
        return {**i, "date": i["date"].isoformat()}

    return {
        "first_date": r["first_date"].isoformat() if r["first_date"] else None,
        "last_date": r["last_date"].isoformat() if r["last_date"] else None,
        "counts": r["counts"], "totals_cents": r["totals"], "checks_not_run": r["not_run"],
        "by_check": {key: {"title": title, "count": len(r["by_check"][key]), "items": [item(i) for i in r["by_check"][key]]} for key, title in CHECKS},
        "by_technician": r["by_technician"],
        "skipped_jobs": [{"job": j["job"], "customer": j["customer"], "technician": j["technician"], "status": j["status"],
                          "date": j["date"].isoformat()} for j in r["skipped"]],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Review a day's completed jobs and list what a person has to look at. Read-only.")
    ap.add_argument("--jobs", required=True, help="jobs export (CSV)")
    ap.add_argument("--config", required=True, help="column names and rules (JSON)")
    ap.add_argument("--out", required=True, help="folder for report.md and report.json")
    args = ap.parse_args(argv)
    try:
        config = read_config(args.config)
        jobs, absent = load_jobs(args.jobs, config)
        result = review(jobs, config, absent)
    except InputError as e:
        print(f"Stopped, nothing written: {e}", file=sys.stderr)
        return 2
    except (KeyError, AttributeError, TypeError) as e:
        print(f"Stopped, nothing written: the config is not in the expected shape ({e!r})", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render(result))
    (out / "report.json").write_text(json.dumps(as_json(result), indent=1))
    c, t = result["counts"], result["totals"]
    print(f"{c['reviewed']} jobs reviewed, {c['skipped']} skipped. {c['flags']} flag(s) on {c['flagged_jobs']} job(s). "
          f"Invoiced {money(t['invoiced'])}, balance due {money(t['balance_due'])}. Report: {out / 'report.md'}")
    return 1 if c["flags"] else 0


if __name__ == "__main__":
    sys.exit(main())
