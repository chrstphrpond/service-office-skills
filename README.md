# Deposit match

A Claude skill that matches bank deposits to recorded payments, to the cent, and lists what a person has to look at. It reads two CSV exports and writes a report. It never posts or changes anything.

Written by Christopher Pond Maquidato in November 2026 for an HVAC and plumbing company's daily deposit check. It runs on made-up sample data: the column names and bank wording in `config.example.json` are placeholders until they are set from real exports.

## What's here

| File | What it is |
|---|---|
| `SKILL.md` | What Claude reads: when to use the skill, the rules, the steps |
| `scripts/match_deposits.py` | The matching. Plain Python, no packages to install |
| `config.example.json` | Column names, deposit sources, financing fee rates |
| `sample-data/` | One made-up week and the report it produces |
| `tests/` | 36 tests |

## Run it

```
python3 scripts/match_deposits.py --payments sample-data/payments.csv --bank sample-data/bank.csv --config config.example.json --out sample-data/report
python3 -m unittest discover -s tests
```

The sample week gives: 10 of 14 deposits matched, 4 for a person. See `sample-data/report/report.md`.

## How it keeps wrong numbers out

- **The model doesn't do the arithmetic.** A script does, in whole cents. Claude runs it and reports.
- **Exact or nothing.** No tolerance and no "close enough". One cent off goes to a person with the difference shown.
- **Never a guess.** Two possible matches, a fee that differs, a plan with no rate: each goes to a person.
- **Every dollar is accounted for.** Matched plus needs-a-person must equal the bank's deposits, or the script refuses to write a report.
- **Bad input stops the run.** A duplicate payment ID, a bad amount or a missing column writes nothing.
- **Read-only.** There is no code path that posts to any system.

## Install as a skill

Copy this folder to `~/.claude/skills/deposit-match/`.

## Not done yet

- Reading ServiceTitan, QuickBooks Online or the bank directly. It works from CSV exports.
- A card batch paid out across two deposits, or two batches in one deposit. Both are flagged for a person today.
- A scheduled daily run with an alert. The exit codes (0, 1, 2) are there for it.
