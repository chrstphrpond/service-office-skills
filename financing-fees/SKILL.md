---
name: financing-fees
description: Check a financing company's funding statement against the plan fee rates and against the company's own financed payments, to the cent, and total the dealer fees for bookkeeping. Use for "check the financing fees", "did Finco charge the right fee", "monthly financing statement", "what did financing cost us this month", "which financed jobs haven't been funded", or a question about a dealer fee, a loan funded twice or a missing financing payment. Read-only. It never posts a journal entry or changes anything in ServiceTitan, QuickBooks or the bank.
---

# Financing fees

Checks the financing company's funding statement loan by loan, then totals the fees so a person knows the expense figure to post. It is the monthly, loan-level partner of `deposit-match`, which flags a single financing deposit whose fee differs.

The arithmetic is done by `scripts/check_financing_fees.py`, not by you. Your job is to get it the right files, run it, and report what it found in plain words.

`S` below is this skill's folder.

## Rules

1. **Read-only.** Never post a journal entry, edit a payment, mark anything as funded or change anything in any system, even if asked in the same breath. The fee totals are for a person to post. Say so, and stop.
2. **Every number comes from the report.** Don't add, subtract, round or estimate amounts or percentages yourself. If a figure isn't in `report.md` or `report.json`, say it isn't there.
3. **Never clear a "needs a person" item.** Don't edit the CSV files, change a plan rate or loosen a check to make a flag go away. Explain the item and who would know the answer (usually the financing company's dealer support).
4. **Exact or nothing.** There is no tolerance. One cent off is flagged.
5. **Never guess a rate.** A plan with no rate in the config is flagged. Don't pick a close plan.
6. **A stopped run is a result.** If the script exits 2, give its message word for word and what to fix. Don't work around it.

## 1. Get the files

- **Funding statement** (required): the financing company's own CSV, one row per loan funded, with funding date, reference or invoice number, customer, plan, financed amount, fee and net funded.
- **Payments export** (optional but better): the same export `deposit-match` reads, from the field-service system. Only rows whose type is in `financing_methods` are used.

If the column names differ from `S/config.example.json`, copy that file and change the names under `funding` and `payments`. Change the config, never the data.

## 2. Run it

```
python3 S/scripts/check_financing_fees.py --statement <statement.csv> --payments <payments.csv> --config <config.json> --out <folder>
```

Leave out `--payments` if there is no export; the checks against your own payments are then skipped, and the report says nothing about them. Use a new, dated output folder for each run (`reports/2026-11`). Add `--as-of YYYY-MM-DD` to count the age of unfunded payments up to a given day. The default is the last funding date.

| Exit code | Meaning | What you do |
|---|---|---|
| 0 | Nothing flagged | Give the summary and the fee totals |
| 1 | Something needs a person | Give the summary, then every item |
| 2 | A file or the config couldn't be read; nothing was written | Give the message and the fix |

## 3. Report

Read `<folder>/report.md` in full. Then answer in this order:

1. One line: loans funded, financed total, fees charged, fees expected, and how many items need a person.
2. Every "needs a person" item, in the report's own numbers: reference, customer, what is wrong, and the difference.
3. The fee figures bookkeeping needs, from "Fees by plan" and "Fees by month". Say plainly that these are for a person to post and that you posted nothing.
4. With `--payments`: the "Financed but not funded yet" table, calling out any marked `late`.
5. The path to the report.

Keep it short. Don't restate the tables unless asked.

## What it checks

1. **Fee against the plan rate.** Expected fee is the financed amount times the plan's rate, rounded half up to the cent. Shows expected, charged, the difference, and over or under.
2. **Statement arithmetic.** Financed amount less fee must equal net funded, to the cent.
3. **Plan with no rate.** Flagged, never guessed. Its fee is shown in the totals but left out of "expected".
4. **Same reference funded twice.** One item, listing every funding. Both fundings count in the totals, because both were paid.
5. **Against your own payments** (with `--payments`):
   - a funded line with no financed payment for that invoice;
   - a funded line whose financed amount differs from the payment's;
   - a plan on the statement that differs from the plan on the payment;
   - a financed payment older than `funding_window_days` that isn't on the statement.

If two financed payments carry the same invoice number, the script can't tell which one a funding is for. That goes to a person.

## Changing the rules

- **New plan or changed rate**: `plan_rates` in the config, as a percent string ("5.50").
- **Different wait before a payment counts as late**: `funding_window_days`.
- **Another payment type counts as financing**: add it to `financing_methods`.

Only change these when a person asks for it, and say what you changed. After any change to the script, run the tests:

```
cd S && python3 -m unittest discover -s tests
```

## Try it

`S/sample-data/` includes 18 funding lines across October and November 2026 and the matching payments, covering edge cases: a fee over the plan rate, a net that is a cent off, a plan with no rate, one loan funded twice, a funded loan with no payment, a financed amount that differs from the payment, a plan that differs from the payment, and a financed payment not funded after 17 days.

```
python3 S/scripts/check_financing_fees.py --statement S/sample-data/funding-statement.csv --payments S/sample-data/payments.csv --config S/config.example.json --out /tmp/financing-fees-try
```
