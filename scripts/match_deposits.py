#!/usr/bin/env python3
"""Match bank deposits to recorded payments, to the cent.

Reads two CSV exports (payments and bank), writes report.md and report.json.
It changes nothing in either system. Anything it can't prove goes on a list for a person.

Exit codes: 0 everything matched, 1 something needs a person, 2 the input could not be read.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

SUBSET_LIMIT = 24  # more open checks than this and a unique match can't be trusted to finish quickly


class InputError(Exception):
    """The files or the config are wrong. Stop; never skip a row silently."""


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


def fee_cents(gross, percent):
    """Fee on a gross amount, rounded half up to the cent."""
    return int((Decimal(gross) * Decimal(str(percent)) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_date(text, fmt, where):
    try:
        return dt.datetime.strptime((text or "").strip(), fmt).date()
    except ValueError:
        raise InputError(f"{where}: '{text}' is not a date in the format {fmt}") from None


# ---------- reading the exports ----------

def read_rows(path, columns, label):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for key, c in columns.items() if c and c not in header and key in REQUIRED[label]]
            if missing:
                raise InputError(f"{label} file is missing column(s): {', '.join(missing)}. It has: {', '.join(header)}")
            return [(n, row) for n, row in enumerate(reader, start=2) if any((v or "").strip() for v in row.values())]
    except OSError as e:
        raise InputError(f"can't read the {label} file: {e}") from None


REQUIRED = {"payments": {"id", "date", "method", "amount"}, "bank": {"date", "description", "amount"}}


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
                    "batch": get("batch"), "plan": get("plan")})
    return out


def load_bank(path, config):
    cols, fmt = config["bank"]["columns"], config["bank"]["date_format"]
    out = []
    for n, row in read_rows(path, cols, "bank"):
        where = f"bank row {n}"
        get = lambda key: (row.get(cols.get(key) or "") or "").strip()
        out.append({"row": n, "date": to_date(get("date"), fmt, where), "description": get("description"),
                    "amount": to_cents(get("amount"), where), "reference": get("reference")})
    return out


# ---------- matching ----------

def names_payment(description, payment):
    """True when the bank line names this payment's invoice number or customer."""
    text = description.upper()
    inv, cust = payment["invoice"].upper(), payment["customer"].upper()
    if inv and re.search(r"(?<![A-Z0-9])" + re.escape(inv) + r"(?![A-Z0-9])", text):
        return True
    return bool(cust) and cust in text


def unique_subsets(items, target, cap=2):
    """Up to `cap` sets of payments whose amounts add up to target exactly. Amounts are positive."""
    items = sorted(items, key=lambda p: -p["amount"])
    tail = [0] * (len(items) + 1)
    for i in range(len(items) - 1, -1, -1):
        tail[i] = tail[i + 1] + items[i]["amount"]
    found = []

    def walk(i, left, chosen):
        if len(found) >= cap:
            return
        if left == 0:
            found.append(list(chosen))
            return
        if i == len(items) or left < 0 or tail[i] < left:
            return
        chosen.append(items[i])
        walk(i + 1, left - items[i]["amount"], chosen)
        chosen.pop()
        walk(i + 1, left, chosen)

    walk(0, target, [])
    return found


def reconcile(payments, bank, config, as_of=None):
    sources = config["sources"]
    plans = config.get("financing_plans", {})
    by_method = {m.lower(): s for s in sources for m in s["methods"]}
    open_pay = {p["id"]: p for p in payments}
    matched, review = [], []

    deposits = sorted((b for b in bank if b["amount"] > 0), key=lambda b: (b["date"], b["row"]))
    skipped = [b for b in bank if b["amount"] <= 0]

    def source_of(payment):
        return by_method.get(payment["method"].lower())

    def in_window(payment, deposit, source):
        return 0 <= (deposit["date"] - payment["date"]).days <= source.get("window_days", 5)

    def candidates(deposit, source):
        return [p for p in open_pay.values() if source_of(p) is source and in_window(p, deposit, source)]

    def settle(deposit, source, pays, fee=0):
        for p in pays:
            del open_pay[p["id"]]
        gross = sum(p["amount"] for p in pays)
        assert gross - fee == deposit["amount"], "internal check: a match must add up to the bank amount"
        matched.append({"bank": deposit, "source": source["name"], "payments": pays, "gross": gross, "fee": fee})

    def flag(deposit, source, reason, hint=""):
        review.append({"bank": deposit, "source": source["name"] if source else None, "reason": reason, "hint": hint})

    def one_to_one(deposit, source):
        cands = candidates(deposit, source)
        exact = [p for p in cands if p["amount"] == deposit["amount"]]
        if len(exact) > 1:
            named = [p for p in exact if names_payment(deposit["description"], p)]
            if len(named) != 1:
                ids = ", ".join(p["id"] for p in exact)
                return flag(deposit, source, f"{len(exact)} open payments are for exactly this amount, and the bank line doesn't say which one.", f"Candidates: {ids}.")
            exact = named
        if exact:
            return settle(deposit, source, exact)
        hint = ""
        if cands:
            near = min(cands, key=lambda p: abs(p["amount"] - deposit["amount"]))
            hint = f"Closest open payment: {near['id']} {near['customer']} {money(near['amount'])}, {money(abs(near['amount'] - deposit['amount']))} apart."
        flag(deposit, source, "No open payment is for exactly this amount.", hint)

    def batch(deposit, source):
        groups = {}
        for p in open_pay.values():
            if source_of(p) is source:
                groups.setdefault(p["batch"] or p["date"].isoformat(), []).append(p)
        ready = {k: g for k, g in groups.items() if all(in_window(p, deposit, source) for p in g)}
        pct = source.get("fee_percent")
        nets = {k: (sum(p["amount"] for p in g), fee_cents(sum(p["amount"] for p in g), pct) if pct else 0) for k, g in ready.items()}
        exact = [k for k, (gross, fee) in nets.items() if gross - fee == deposit["amount"]]
        if len(exact) > 1:
            return flag(deposit, source, f"{len(exact)} batches add up to exactly this amount.", f"Batches: {', '.join(exact)}.")
        if exact:
            return settle(deposit, source, ready[exact[0]], nets[exact[0]][1])
        hint = ""
        if nets:
            k = min(nets, key=lambda k: abs(nets[k][0] - nets[k][1] - deposit["amount"]))
            expected = nets[k][0] - nets[k][1]
            diff = deposit["amount"] - expected
            hint = (f"Closest batch: {k}, {len(ready[k])} payment(s) adding up to {money(expected)}. "
                    f"The bank shows {money(abs(diff))} {'less' if diff < 0 else 'more'}.")
        flag(deposit, source, "No batch adds up to exactly this amount.", hint)

    def net_of_fee(deposit, source):
        cands = candidates(deposit, source)
        priced = [(p, fee_cents(p["amount"], plans[p["plan"]])) for p in cands if p["plan"] in plans]
        no_plan = [p for p in cands if p["plan"] not in plans]
        exact = [(p, fee) for p, fee in priced if p["amount"] - fee == deposit["amount"]]
        if len(exact) > 1:
            named = [(p, fee) for p, fee in exact if names_payment(deposit["description"], p)]
            if len(named) != 1:
                return flag(deposit, source, f"{len(exact)} financed payments would fund exactly this amount, and the bank line doesn't say which one.",
                            f"Candidates: {', '.join(p['id'] for p, _ in exact)}.")
            exact = named
        if exact:
            return settle(deposit, source, [exact[0][0]], exact[0][1])
        named = [(p, fee) for p, fee in priced if names_payment(deposit["description"], p)]
        if len(named) == 1:
            p, fee = named[0]
            deducted = p["amount"] - deposit["amount"]
            return flag(deposit, source,
                        f"The fee differs. Plan '{p['plan']}' should deduct {money(fee)} ({plans[p['plan']]}%) from {money(p['amount'])}; the bank shows {money(deducted)} deducted.",
                        f"Payment {p['id']} {p['customer']}, invoice {p['invoice']}. Expected deposit {money(p['amount'] - fee)}.")
        hint = ""
        if priced:
            p, fee = min(priced, key=lambda x: abs(x[0]["amount"] - x[1] - deposit["amount"]))
            hint = f"Closest: {p['id']} {p['customer']}, expected deposit {money(p['amount'] - fee)}."
        if no_plan:
            hint = (hint + " " if hint else "") + f"No fee rate is set for: {', '.join(sorted({p['plan'] or '(blank plan)' for p in no_plan}))}."
        flag(deposit, source, "No financed payment, less its plan fee, comes to exactly this amount.", hint)

    def subset(deposit, source, final):
        cands = [p for p in candidates(deposit, source) if p["amount"] > 0]
        if len(cands) > SUBSET_LIMIT:
            if final:
                flag(deposit, source, f"{len(cands)} open payments could be in this deposit, too many to match safely.", "Match it by hand, or shorten window_days for this source.")
            return False
        found = unique_subsets(cands, deposit["amount"])
        if len(found) == 1:
            settle(deposit, source, sorted(found[0], key=lambda p: p["id"]))
            return True
        if final:
            if found:
                a, b = ({p["id"] for p in s} for s in found)
                flag(deposit, source, "More than one set of open payments adds up to exactly this amount.",
                     f"For example {', '.join(sorted(a))} or {', '.join(sorted(b))}.")
            else:
                flag(deposit, source, "No set of open payments adds up to exactly this amount.",
                     f"{len(cands)} open payment(s) in the window, adding up to {money(sum(p['amount'] for p in cands))}.")
        return False

    modes = {"one_to_one": one_to_one, "batch": batch, "net_of_fee": net_of_fee}
    seen_refs, waiting = set(), []
    for d in deposits:
        key = (d["date"], d["amount"], d["description"].upper(), d["reference"].upper())
        if d["reference"] and key in seen_refs:
            flag(d, None, "This bank line repeats an earlier one: same date, amount, description and reference.", "Check with the bank before counting it twice.")
            continue
        seen_refs.add(key)
        text = d["description"].upper()
        source = next((s for s in sources if any(pat.upper() in text for pat in s["bank_patterns"])), None)
        if source is None:
            flag(d, None, "No rule covers this kind of bank line.", "If it is a customer payment, add its wording to a source in the config.")
        elif source["mode"] == "subset":
            waiting.append((d, source))
        elif source["mode"] in modes:
            modes[source["mode"]](d, source)
        else:
            raise InputError(f"source '{source['name']}' has an unknown mode '{source['mode']}'")

    # Counter deposits: one match can make the next one unique, so repeat until nothing changes.
    progress = True
    while progress and waiting:
        progress, still = False, []
        for d, source in waiting:
            if subset(d, source, final=False):
                progress = True
            else:
                still.append((d, source))
        waiting = still
    for d, source in waiting:
        subset(d, source, final=True)
    review.sort(key=lambda r: (r["bank"]["date"], r["bank"]["row"]))
    matched.sort(key=lambda m: (m["bank"]["date"], m["bank"]["row"]))

    as_of = as_of or max((b["date"] for b in bank), default=dt.date.today())
    still_open = []
    for p in open_pay.values():
        source, days = source_of(p), (as_of - p["date"]).days
        note = "no rule covers this payment type" if source is None else ("late" if days > source.get("window_days", 5) else "")
        still_open.append({"payment": p, "days": days, "note": note})

    totals = {
        "bank_deposits": sum(d["amount"] for d in deposits),
        "matched": sum(m["bank"]["amount"] for m in matched),
        "needs_person": sum(r["bank"]["amount"] for r in review),
        "fees": sum(m["fee"] for m in matched),
        "payments": sum(p["amount"] for p in payments),
        "payments_matched": sum(m["gross"] for m in matched),
        "payments_open": sum(o["payment"]["amount"] for o in still_open),
    }
    # The proof lines. If either fails the tool is wrong, and it must not write a report.
    assert totals["bank_deposits"] == totals["matched"] + totals["needs_person"]
    assert totals["payments"] == totals["payments_matched"] + totals["payments_open"]
    assert totals["payments_matched"] - totals["fees"] == totals["matched"]
    return {"as_of": as_of, "matched": matched, "review": review, "open": still_open, "skipped": skipped,
            "deposits": len(deposits), "totals": totals}


# ---------- report ----------

def render(result):
    t, L = result["totals"], []
    attention = [o for o in result["open"] if o["note"]]
    L += [f"# Deposit match, as of {result['as_of']:%b %d, %Y}", ""]
    L += ["| | Count | Amount |", "|---|---:|---:|",
          f"| Bank deposits | {result['deposits']} | {money(t['bank_deposits'])} |",
          f"| Matched | {len(result['matched'])} | {money(t['matched'])} |",
          f"| **Needs a person** | **{len(result['review'])}** | **{money(t['needs_person'])}** |",
          f"| Payments not in the bank yet | {len(result['open'])} | {money(t['payments_open'])} |", ""]
    L += [f"Check: {money(t['matched'])} matched + {money(t['needs_person'])} needs a person = {money(t['bank_deposits'])} in bank deposits.",
          f"Fees taken out of matched deposits: {money(t['fees'])}.",
          f"Bank lines that are not deposits (skipped): {len(result['skipped'])}.", ""]

    L += ["## Needs a person", ""]
    if not result["review"]:
        L += ["Nothing.", ""]
    for r in result["review"]:
        b = r["bank"]
        L += [f"- **{money(b['amount'])}**, {b['date']:%b %d}, \"{b['description']}\" (bank row {b['row']})", f"  - {r['reason']}"]
        if r["hint"]:
            L += [f"  - {r['hint']}"]
    L += [""]

    L += ["## Matched", "", "| Bank date | Bank line | Deposit | Payments | Gross | Fee |", "|---|---|---:|---|---:|---:|"]
    for m in result["matched"]:
        b = m["bank"]
        pays = ", ".join(f"{p['id']} ({p['invoice'] or p['customer']})" for p in m["payments"])
        L += [f"| {b['date']:%b %d} | {b['description']} | {money(b['amount'])} | {pays} | {money(m['gross'])} | {money(m['fee']) if m['fee'] else ''} |"]
    L += [""]

    L += ["## Payments not in the bank yet", ""]
    if not result["open"]:
        L += ["None.", ""]
    else:
        if attention:
            L += [f"{len(attention)} of these need a look (marked in the last column).", ""]
        L += ["| Payment | Date | Customer | Invoice | Type | Amount | Days | |", "|---|---|---|---|---|---:|---:|---|"]
        for o in result["open"]:
            p = o["payment"]
            L += [f"| {p['id']} | {p['date']:%b %d} | {p['customer']} | {p['invoice']} | {p['method']} | {money(p['amount'])} | {o['days']} | {o['note']} |"]
        L += [""]
    L += ["This report is read-only. Nothing was posted or changed in the payments system, the books or the bank."]
    return "\n".join(L) + "\n"


def as_json(result):
    def pay(p):
        return {**p, "date": p["date"].isoformat()}

    def line(b):
        return {**b, "date": b["date"].isoformat()}

    return {
        "as_of": result["as_of"].isoformat(), "totals_cents": result["totals"],
        "matched": [{"bank": line(m["bank"]), "source": m["source"], "gross": m["gross"], "fee": m["fee"],
                     "payments": [pay(p) for p in m["payments"]]} for m in result["matched"]],
        "needs_person": [{"bank": line(r["bank"]), "source": r["source"], "reason": r["reason"], "hint": r["hint"]} for r in result["review"]],
        "open_payments": [{"payment": pay(o["payment"]), "days": o["days"], "note": o["note"]} for o in result["open"]],
        "skipped_bank_lines": [line(b) for b in result["skipped"]],
    }


def needs_person(result):
    return bool(result["review"]) or any(o["note"] for o in result["open"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Match bank deposits to recorded payments, to the cent. Read-only.")
    ap.add_argument("--payments", required=True, help="payments export (CSV)")
    ap.add_argument("--bank", required=True, help="bank export (CSV)")
    ap.add_argument("--config", required=True, help="column names, sources and fee rates (JSON)")
    ap.add_argument("--out", required=True, help="folder for report.md and report.json")
    ap.add_argument("--as-of", help="count days outstanding up to this date (YYYY-MM-DD); default is the last bank date")
    args = ap.parse_args(argv)
    try:
        try:
            config = json.loads(Path(args.config).read_text())
        except (OSError, ValueError) as e:
            raise InputError(f"can't read the config: {e}") from None
        as_of = to_date(args.as_of, "%Y-%m-%d", "--as-of") if args.as_of else None
        result = reconcile(load_payments(args.payments, config), load_bank(args.bank, config), config, as_of)
    except InputError as e:
        print(f"Stopped, nothing written: {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"Stopped, nothing written: the config is missing {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render(result))
    (out / "report.json").write_text(json.dumps(as_json(result), indent=1))
    t = result["totals"]
    print(f"{len(result['matched'])} of {result['deposits']} deposits matched ({money(t['matched'])}). "
          f"{len(result['review'])} need a person ({money(t['needs_person'])}). Report: {out / 'report.md'}")
    return 1 if needs_person(result) else 0


if __name__ == "__main__":
    sys.exit(main())
