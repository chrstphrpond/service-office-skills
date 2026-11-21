#!/usr/bin/env python3
"""Check a financing company's funding statement, loan by loan, to the cent.

Reads the funding statement (CSV), the plan fee rates (config) and, if given, the company's own
financed payments (CSV). Writes report.md and report.json. It posts and changes nothing.

Exit codes: 0 nothing flagged, 1 something needs a person, 2 the input could not be read.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path


class InputError(Exception):
    """The files or the config are wrong. Stop; never skip a row silently."""


# ---------- money, rates and dates ----------

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


def fee_cents(gross, percent):
    """Fee on a gross amount, rounded half up to the cent."""
    return int((Decimal(gross) * Decimal(str(percent)) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def percent_text(part, whole):
    """part / whole as a percent with two decimals, e.g. '5.50%'. Decimal only."""
    if whole == 0:
        return ""
    return f"{(Decimal(part) * 100 / Decimal(whole)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def to_date(text, fmt, where):
    try:
        return dt.datetime.strptime((text or "").strip(), fmt).date()
    except ValueError:
        raise InputError(f"{where}: '{text}' is not a date in the format {fmt}") from None


def load_rates(config):
    """Plan name (any case) -> rate as a percent string. A bad rate stops the run."""
    rates = {}
    for name, rate in config["plan_rates"].items():
        key = name.strip().lower()
        if not key:
            raise InputError("config plan_rates: a plan has no name")
        if key in rates:
            raise InputError(f"config plan_rates: plan '{name}' is listed twice")
        if not isinstance(rate, str):
            raise InputError(f"config plan_rates: the rate for '{name}' must be a string like \"5.50\"")
        try:
            value = Decimal(rate.strip().rstrip("%"))
        except InvalidOperation:
            raise InputError(f"config plan_rates: '{rate}' is not a rate for '{name}'") from None
        if not value.is_finite() or value < 0 or value >= 100:
            raise InputError(f"config plan_rates: the rate for '{name}' must be at least 0 and under 100, not '{rate}'")
        rates[key] = format(value, "f")
    return rates


def window_days(config):
    value = config["funding_window_days"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError("config funding_window_days must be a whole number of days, 0 or more")
    return value


# ---------- reading the exports ----------

REQUIRED = {
    "funding statement": {"date", "reference", "plan", "financed", "fee", "net"},
    "payments": {"id", "date", "invoice", "method", "amount"},
}


def read_rows(path, columns, label):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for key, c in columns.items() if c and c not in header and key in REQUIRED[label]]
            absent = [key for key in REQUIRED[label] if not columns.get(key)]
            if absent:
                raise InputError(f"config: no column name set for {', '.join(sorted(absent))} in the {label} columns")
            if missing:
                raise InputError(f"{label} file is missing column(s): {', '.join(missing)}. It has: {', '.join(header)}")
            return [(n, row) for n, row in enumerate(reader, start=2) if any((v or "").strip() for v in row.values())]
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"can't read the {label} file: {e}") from None


def load_statement(path, config):
    cols, fmt = config["funding"]["columns"], config["funding"]["date_format"]
    out = []
    for n, row in read_rows(path, cols, "funding statement"):
        where = f"funding statement row {n}"
        get = lambda key: (row.get(cols.get(key) or "") or "").strip()
        ref = get("reference")
        if not ref:
            raise InputError(f"{where}: no reference or invoice number")
        line = {"row": n, "date": to_date(get("date"), fmt, where), "reference": ref, "customer": get("customer"),
                "plan": get("plan"), "financed": to_cents(get("financed"), where),
                "fee": to_cents(get("fee"), where), "net": to_cents(get("net"), where)}
        if line["financed"] <= 0:
            raise InputError(f"{where}: the financed amount must be more than zero (reversals are not handled)")
        if line["fee"] < 0 or line["net"] < 0:
            raise InputError(f"{where}: the fee and the net funded can't be negative (reversals are not handled)")
        out.append(line)
    if not out:
        raise InputError("the funding statement has no lines")
    return out


def load_payments(path, config):
    cols, fmt = config["payments"]["columns"], config["payments"]["date_format"]
    out, seen = [], set()
    for n, row in read_rows(path, cols, "payments"):
        where = f"payments row {n}"
        get = lambda key: (row.get(cols.get(key) or "") or "").strip()
        pid = get("id")
        if not pid:
            raise InputError(f"{where}: no payment ID")
        if pid in seen:
            raise InputError(f"{where}: payment ID {pid} appears twice in the export")
        seen.add(pid)
        out.append({"id": pid, "date": to_date(get("date"), fmt, where), "invoice": get("invoice"),
                    "customer": get("customer"), "method": get("method"), "amount": to_cents(get("amount"), where),
                    "plan": get("plan")})
    return out


# ---------- the checks ----------

def new_group():
    return {"count": 0, "financed": 0, "charged": 0, "expected": 0, "charged_rated": 0, "unrated": 0, "net": 0}


def add_to(group, line):
    group["count"] += 1
    group["financed"] += line["financed"]
    group["charged"] += line["fee"]
    group["net"] += line["net"]
    if line["expected"] is None:
        group["unrated"] += 1
    else:
        group["expected"] += line["expected"]
        group["charged_rated"] += line["fee"]


def finish_group(group, label):
    out = dict(group)
    out["label"] = label
    out["difference"] = group["charged_rated"] - group["expected"]
    out["effective_rate"] = percent_text(group["charged"], group["financed"])
    return out


def analyze(lines, config, payments=None, as_of=None):
    rates = load_rates(config)
    items = []

    def item(check, line, message, numbers):
        items.append({"check": check, "reference": line["reference"], "customer": line["customer"],
                      "date": line["date"], "row": line.get("row"), "message": message, "numbers": numbers})

    # Check 4 first, so later checks can skip the repeats.
    by_ref = {}
    for line in lines:
        by_ref.setdefault(line["reference"], []).append(line)
    repeats = set()  # row numbers of the second and later funding of a reference
    for ref, group in by_ref.items():
        if len(group) > 1:
            repeats.update(g["row"] for g in group[1:])
            parts = ", ".join(f"{g['date']:%b %d} {money(g['financed'])} (row {g['row']})" for g in group)
            item("funded_twice", group[0],
                 f"Reference {ref} was funded {len(group)} times: {parts}. "
                 f"Together the financed amounts come to {money(sum(g['financed'] for g in group))}.",
                 {"times": len(group), "financed_total": sum(g["financed"] for g in group),
                  "fee_total": sum(g["fee"] for g in group)})

    for line in lines:
        rate = rates.get(line["plan"].lower())
        line["rate"] = rate
        line["expected"] = fee_cents(line["financed"], rate) if rate is not None else None
        line["arithmetic_gap"] = line["net"] - (line["financed"] - line["fee"])
        plan = line["plan"] or "(blank)"
        if rate is None:  # check 3
            item("no_rate", line, f"No fee rate is set for plan '{plan}', so the fee of {money(line['fee'])} on "
                 f"{money(line['financed'])} can't be checked. Nothing was guessed.",
                 {"financed": line["financed"], "charged": line["fee"]})
        elif line["fee"] != line["expected"]:  # check 1
            diff = line["fee"] - line["expected"]
            item("fee_differs", line,
                 f"The fee differs from the plan rate. Plan '{plan}' at {rate}% on {money(line['financed'])} should be "
                 f"{money(line['expected'])}. Charged {money(line['fee'])}, {money(abs(diff))} {'over' if diff > 0 else 'under'}.",
                 {"financed": line["financed"], "expected": line["expected"], "charged": line["fee"], "difference": diff})
        if line["arithmetic_gap"] != 0:  # check 2
            gap = line["arithmetic_gap"]
            item("arithmetic", line,
                 f"The statement doesn't add up. {money(line['financed'])} less the fee {money(line['fee'])} is "
                 f"{money(line['financed'] - line['fee'])}. The statement shows {money(line['net'])} funded, "
                 f"{money(abs(gap))} {'more' if gap > 0 else 'less'}.",
                 {"financed": line["financed"], "charged": line["fee"], "net": line["net"],
                  "calculated_net": line["financed"] - line["fee"], "difference": gap})

    # Check 5, only when the payments export is given.
    unfunded, financed_payments = [], None
    if payments is not None:
        methods = {m.strip().lower() for m in config["financing_methods"]}
        window = window_days(config)
        financed_payments = [p for p in payments if p["method"].lower() in methods]
        by_invoice = {}
        for p in financed_payments:
            if p["invoice"]:
                by_invoice.setdefault(p["invoice"], []).append(p)
        for line in lines:
            if line["row"] in repeats:
                continue
            found = by_invoice.get(line["reference"], [])
            if not found:
                item("no_payment", line, f"Funded {money(line['financed'])}, but no financed payment for invoice "
                     f"{line['reference']} is in the payments export.", {"financed": line["financed"]})
            elif len(found) > 1:
                item("several_payments", line, f"{len(found)} financed payments carry invoice {line['reference']} "
                     f"({', '.join(p['id'] for p in found)}, together {money(sum(p['amount'] for p in found))}). "
                     f"A person has to say which one this funding is for.",
                     {"financed": line["financed"], "payments_total": sum(p["amount"] for p in found)})
            else:
                p = found[0]
                if p["amount"] != line["financed"]:
                    diff = line["financed"] - p["amount"]
                    item("amount_differs", line,
                         f"The financed amount differs. The statement says {money(line['financed'])}; payment {p['id']} "
                         f"is {money(p['amount'])}, {money(abs(diff))} {'more' if diff > 0 else 'less'} on the statement.",
                         {"financed": line["financed"], "payment": p["amount"], "difference": diff})
                if p["plan"].strip().lower() != line["plan"].strip().lower():
                    item("plan_differs", line,
                         f"The plan differs. The statement says '{line['plan'] or '(blank)'}'; payment {p['id']} says "
                         f"'{p['plan'] or '(blank)'}'. Fees were checked against the statement's plan.",
                         {"financed": line["financed"]})
        as_of = as_of or max(line["date"] for line in lines)
        funded_refs = {line["reference"] for line in lines}
        for p in sorted(financed_payments, key=lambda p: (p["date"], p["id"])):
            if p["invoice"] and p["invoice"] in funded_refs:
                continue
            days = (as_of - p["date"]).days
            row = {"payment": p, "days": days, "late": days > window}
            unfunded.append(row)
            if row["late"]:
                items.append({"check": "not_funded", "reference": p["invoice"], "customer": p["customer"],
                              "date": p["date"], "row": None,
                              "message": f"Financed payment {p['id']} ({money(p['amount'])}) is {days} days old and not on "
                              f"the statement. The window is {window} days.",
                              "numbers": {"financed": p["amount"], "days": days, "window_days": window}})
    items.sort(key=lambda i: (i["date"], i["row"] or 0, i["check"]))

    # Totals and groups.
    overall, plans, months = new_group(), {}, {}
    for line in lines:
        add_to(overall, line)
        key = line["plan"].lower()
        plans.setdefault(key, [line["plan"] or "(blank)", new_group()])
        add_to(plans[key][1], line)
        mkey = f"{line['date']:%Y-%m}"
        months.setdefault(mkey, [f"{line['date']:%b %Y}", new_group()])
        add_to(months[mkey][1], line)
    totals = finish_group(overall, "All")
    by_plan = [finish_group(g, label) for _, (label, g) in sorted(plans.items(), key=lambda kv: kv[1][0].lower())]
    by_month = [finish_group(g, label) for _, (label, g) in sorted(months.items())]

    # The proof lines. If any fails the tool is wrong, and it must not write a report.
    for rows in (by_plan, by_month):
        for field in ("count", "financed", "charged", "expected", "charged_rated", "unrated", "net"):
            assert sum(r[field] for r in rows) == totals[field], f"internal check: {field} by group must add up to the total"
    passing = [line for line in lines if line["arithmetic_gap"] == 0]
    assert sum(l["financed"] for l in passing) - sum(l["fee"] for l in passing) == sum(l["net"] for l in passing)
    assert totals["financed"] - totals["charged"] + sum(l["arithmetic_gap"] for l in lines) == totals["net"]
    assert totals["charged"] == totals["charged_rated"] + sum(l["fee"] for l in lines if l["expected"] is None)
    assert totals["difference"] == sum(l["fee"] - l["expected"] for l in lines if l["expected"] is not None)
    assert totals["count"] == len(lines)

    return {"lines": lines, "totals": totals, "by_plan": by_plan, "by_month": by_month, "items": items,
            "unfunded": unfunded if payments is not None else None, "as_of": as_of if payments is not None else None,
            "first": min(l["date"] for l in lines), "last": max(l["date"] for l in lines),
            "window": window_days(config) if payments is not None else None,
            "financed_payments": len(financed_payments) if financed_payments is not None else None}


# ---------- report ----------

def render(result):
    t, L = result["totals"], []
    L += [f"# Financing fees, {result['first']:%b %d} to {result['last']:%b %d, %Y}", ""]
    L += ["| | Count | Amount |", "|---|---:|---:|",
          f"| Loans funded | {t['count']} | |",
          f"| Financed total | | {money(t['financed'])} |",
          f"| Fees charged | | {money(t['charged'])} |",
          f"| Fees expected at the plan rates | | {money(t['expected'])} |",
          f"| Difference (charged less expected) | | {money(t['difference'])} |"]
    if t["unrated"]:
        unrated_fees = t["charged"] - t["charged_rated"]
        L += [f"| Fees on plans with no rate (not in expected) | {t['unrated']} | {money(unrated_fees)} |"]
    L += [f"| Net funded, as the statement shows | | {money(t['net'])} |",
          f"| **Items for a person** | **{len(result['items'])}** | |", ""]
    L += ["The fee totals are for a person to post to the books. This tool posts nothing.", ""]

    L += ["## Needs a person", ""]
    if not result["items"]:
        L += ["Nothing.", ""]
    for i in result["items"]:
        who = f" {i['customer']}," if i["customer"] else ""
        L += [f"- **{i['reference'] or '(no invoice)'}**,{who} {i['date']:%b %d}"
              + (f" (statement row {i['row']})" if i["row"] else " (payments export)"), f"  - {i['message']}"]
    L += [""]

    def table(title, first_col, rows, note_unrated=False):
        out = [f"## {title}", "", f"| {first_col} | Loans | Financed | Fee charged | Fee expected | Difference | Effective rate |",
               "|---|---:|---:|---:|---:|---:|---:|"]
        for r in rows:
            if r["unrated"] == r["count"]:
                exp, diff = "no rate", "no rate"
            else:
                exp, diff = money(r["expected"]), money(r["difference"])
            out += [f"| {r['label']} | {r['count']} | {money(r['financed'])} | {money(r['charged'])} | {exp} | {diff} | {r['effective_rate']} |"]
        out += [f"| **All** | **{t['count']}** | **{money(t['financed'])}** | **{money(t['charged'])}** | **{money(t['expected'])}** | **{money(t['difference'])}** | **{t['effective_rate']}** |", ""]
        if t["unrated"]:
            out += [f"Fee expected and difference cover only lines on a plan with a rate. {t['unrated']} line(s) on a plan with no rate are in the fee charged column only.", ""]
        return out

    L += table("Fees by plan", "Plan", result["by_plan"])
    L += table("Fees by month", "Month", result["by_month"])

    if result["unfunded"] is not None:
        L += ["## Financed but not funded yet", ""]
        if not result["unfunded"]:
            L += ["None.", ""]
        else:
            L += [f"As of {result['as_of']:%b %d, %Y}. A payment is late after {result['window']} days.", "",
                  "| Payment | Date | Customer | Invoice | Plan | Amount | Days | |", "|---|---|---|---|---|---:|---:|---|"]
            for u in result["unfunded"]:
                p = u["payment"]
                L += [f"| {p['id']} | {p['date']:%b %d} | {p['customer']} | {p['invoice']} | {p['plan']} | {money(p['amount'])} | {u['days']} | {'late' if u['late'] else ''} |"]
            L += [""]
    L += ["This report is read-only. Nothing was posted or changed in the field-service system, the books or the bank. "
          "The fee totals are for a person to post."]
    return "\n".join(L) + "\n"


def as_json(result):
    def grp(g):
        return {k: v for k, v in g.items()}

    t = result["totals"]
    return {
        "first_funding_date": result["first"].isoformat(), "last_funding_date": result["last"].isoformat(),
        "as_of": result["as_of"].isoformat() if result["as_of"] else None,
        "note": "All money is in cents. The fee totals are for a person to post; the tool posts nothing.",
        "totals_cents": {**grp(t), "items_for_a_person": len(result["items"])},
        "by_plan": [grp(g) for g in result["by_plan"]],
        "by_month": [grp(g) for g in result["by_month"]],
        "needs_person": [{**i, "date": i["date"].isoformat()} for i in result["items"]],
        "lines": [{"row": l["row"], "date": l["date"].isoformat(), "reference": l["reference"], "customer": l["customer"],
                   "plan": l["plan"], "rate_percent": l["rate"], "financed": l["financed"], "fee_charged": l["fee"],
                   "fee_expected": l["expected"], "net_funded": l["net"], "net_gap": l["arithmetic_gap"]} for l in result["lines"]],
        "not_funded_yet": None if result["unfunded"] is None else
        [{"payment": {**u["payment"], "date": u["payment"]["date"].isoformat()}, "days": u["days"], "late": u["late"]} for u in result["unfunded"]],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a financing funding statement against plan rates and payments. Read-only.")
    ap.add_argument("--statement", required=True, help="the financing company's funding statement (CSV)")
    ap.add_argument("--config", required=True, help="column names, plan rates and the funding window (JSON)")
    ap.add_argument("--out", required=True, help="folder for report.md and report.json")
    ap.add_argument("--payments", help="payments export (CSV); turns on the checks against your own financed payments")
    ap.add_argument("--as-of", help="count days outstanding up to this date (YYYY-MM-DD); default is the last funding date")
    args = ap.parse_args(argv)
    try:
        try:
            config = json.loads(Path(args.config).read_text())
        except (OSError, ValueError) as e:
            raise InputError(f"can't read the config: {e}") from None
        as_of = to_date(args.as_of, "%Y-%m-%d", "--as-of") if args.as_of else None
        load_rates(config)
        lines = load_statement(args.statement, config)
        payments = load_payments(args.payments, config) if args.payments else None
        result = analyze(lines, config, payments, as_of)
    except InputError as e:
        print(f"Stopped, nothing written: {e}", file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError) as e:
        print(f"Stopped, nothing written: the config is missing or has the wrong shape at {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render(result))
    (out / "report.json").write_text(json.dumps(as_json(result), indent=1))
    t = result["totals"]
    print(f"{t['count']} loans funded ({money(t['financed'])}). Fees charged {money(t['charged'])}, expected {money(t['expected'])}. "
          f"{len(result['items'])} need a person. Report: {out / 'report.md'}")
    return 1 if result["items"] else 0


if __name__ == "__main__":
    sys.exit(main())
