---
name: deposit-match
description: Match bank deposits to the payments recorded in the field-service system, to the cent, and list what a person has to look at. Use for "match deposits", "reconcile the bank", "did yesterday's deposits come in", "which payments haven't hit the bank", a daily deposit check, or a question about a financing fee or a short card batch. Read-only. It never posts, edits or marks anything in ServiceTitan, QuickBooks or the bank.
---

# Deposit match

Compares two exports, the payments taken and the bank's deposits, and writes a report: what matched, what needs a person, and which payments haven't reached the bank.

The arithmetic is done by `scripts/match_deposits.py`, not by you. Your job is to get it the right files, run it, and report what it found in plain words.

`S` below is this skill's folder.

## Rules

1. **Read-only.** Never post, edit, mark as deposited or delete anything in any system, even if asked in the same breath. Say what a person should do and stop.
2. **Every number comes from the report.** Don't add, subtract, round or estimate amounts yourself. If a figure isn't in `report.md` or `report.json`, say it isn't there.
3. **Never clear a "needs a person" item.** Don't edit the CSV files, loosen a rule or change a fee rate to make something match. Explain the item and who would know the answer.
4. **Exact or nothing.** There is no tolerance. One cent off is not a match.
5. **A stopped run is a result.** If the script exits 2, give its message word for word and what to fix. Don't work around it.

## 1. Get the files

You need two CSV exports covering the same days:

- **Payments**: one row per payment, with an ID, date, type (Visa, Check, Financing…) and amount. Invoice number, customer, batch and financing plan make matching better.
- **Bank**: one row per bank line, with date, description and amount. Deposits are positive.

Give the bank export a few more days than the payments export: cards land one to three days later, financing later still.

If the column names differ from `S/config.example.json`, copy that file and change the names under `columns`. Change the config, never the data.

## 2. Run it

```
python3 S/scripts/match_deposits.py --payments <payments.csv> --bank <bank.csv> --config <config.json> --out <folder>
```

Use a new, dated output folder for each run (`reports/2026-11-20`). Add `--as-of YYYY-MM-DD` to count days outstanding up to a given day.

| Exit code | Meaning | What you do |
|---|---|---|
| 0 | Every deposit matched | Give the summary |
| 1 | Something needs a person | Give the summary, then every item |
| 2 | A file or the config couldn't be read; nothing was written | Give the message and the fix |

## 3. Report

Read `<folder>/report.md` in full. Then answer in this order:

1. One line: deposits matched out of the total, and the dollar amount that needs a person.
2. Every "needs a person" item: the amount, the date, the reason, and the hint, in the report's own numbers.
3. Payments not in the bank yet, calling out any marked `late` or `no rule covers this payment type`.
4. The path to the report.

Keep it short. Don't restate the matched table unless asked.

## How it matches

Each bank line is sent to a source by the words in its description (`bank_patterns` in the config). Each source has one mode:

| Mode | Used for | A match is |
|---|---|---|
| `batch` | Card settlements | One batch of payments (by batch ID, or by day) adding up to the deposit |
| `net_of_fee` | Financing | One financed payment, less its plan's fee, equal to the deposit |
| `subset` | Check and cash deposits | The only set of open payments adding up to the deposit |
| `one_to_one` | ACH | One payment of the same amount |

All of them require the deposit to be dated on or after the payment and within the source's `window_days`. A payment is used once. If two answers are possible, the bank line has to name the invoice or customer, or the item goes to a person.

## What gets flagged

- A deposit nothing adds up to, with the closest candidate and the difference (a short card batch usually means a chargeback or a refund).
- A financing deposit where the fee deducted isn't the plan's fee, with both numbers.
- Two or more possible matches for one deposit.
- A bank line that repeats an earlier one (same date, amount, description and reference).
- A bank line or a payment type no rule covers.
- Payments still not in the bank after their window (`late`).

## Changing the rules

- **New financing plan or changed fee**: `financing_plans` in the config, as a percent ("5.50"). A plan with no rate is never matched.
- **New kind of bank line**: add its wording to a source's `bank_patterns`.
- **Processor deposits card batches net of fees**: add `"fee_percent"` to that source.

Only change these when a person asks for it, and say what you changed. After any change to the script, run the tests:

```
cd S && python3 -m unittest discover -s tests
```

## Try it

`S/sample-data/` has a made-up week (22 payments, 16 bank lines) with four problems planted in it: a card batch $189.00 short, a financing fee that is higher than the plan's, a repeated bank line and a wire no rule covers.

```
python3 S/scripts/match_deposits.py --payments S/sample-data/payments.csv --bank S/sample-data/bank.csv --config S/config.example.json --out /tmp/deposit-match-try
```
