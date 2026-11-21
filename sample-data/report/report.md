# Deposit match, as of Nov 20, 2026

| | Count | Amount |
|---|---:|---:|
| Bank deposits | 14 | $36,269.44 |
| Matched | 10 | $22,707.74 |
| **Needs a person** | **4** | **$13,561.70** |
| Payments not in the bank yet | 7 | $20,596.30 |

Check: $22,707.74 matched + $13,561.70 needs a person = $36,269.44 in bank deposits.
Fees taken out of matched deposits: $464.75.
Bank lines that are not deposits (skipped): 2.

## Needs a person

- **$692.30**, Nov 18, "BANKCARD MTOT DEP" (bank row 6)
  - No batch adds up to exactly this amount.
  - Closest batch: 2026-11-17, 3 payment(s) adding up to $881.30. The bank shows $189.00 less.
- **$12,096.00**, Nov 20, "FINCO FUNDING INV 48216" (bank row 12)
  - The fee differs. Plan '60 months 9.99%' should deduct $378.00 (3.00%) from $12,600.00; the bank shows $504.00 deducted.
  - Payment P-1016 Nell Hargrove, invoice 48216. Expected deposit $12,222.00.
- **$455.00**, Nov 20, "BANKCARD MTOT DEP" (bank row 15)
  - This bank line repeats an earlier one: same date, amount, description and reference.
  - Check with the bank before counting it twice.
- **$318.40**, Nov 20, "WIRE IN TX COMPTROLLER REFUND" (bank row 16)
  - No rule covers this kind of bank line.
  - If it is a customer payment, add its wording to a source in the config.

## Matched

| Bank date | Bank line | Deposit | Payments | Gross | Fee |
|---|---|---:|---|---:|---:|
| Nov 17 | BANKCARD MTOT DEP | $1,634.50 | P-1001 (48201), P-1002 (48202) | $1,634.50 |  |
| Nov 17 | COUNTER DEPOSIT | $1,795.00 | P-1003 (48203), P-1009 (48209), P-1010 (48210) | $1,795.00 |  |
| Nov 18 | AMEX SETTLEMENT | $612.75 | P-1004 (48204) | $612.75 |  |
| Nov 18 | ACH CREDIT HOLLIS FARMS LLC | $3,120.00 | P-1006 (48206) | $3,120.00 |  |
| Nov 18 | FINCO FUNDING INV 48205 | $7,985.25 | P-1005 (48205) | $8,450.00 | $464.75 |
| Nov 19 | ACH CREDIT JUNIPER DENTAL GROUP | $3,120.00 | P-1012 (48212) | $3,120.00 |  |
| Nov 19 | BANKCARD MTOT DEP | $2,474.99 | P-1013 (48213), P-1014 (48214) | $2,474.99 |  |
| Nov 20 | COUNTER DEPOSIT | $530.25 | P-1015 (48215), P-1018 (48218) | $530.25 |  |
| Nov 20 | BANKCARD MTOT DEP | $455.00 | P-1017 (48217) | $455.00 |  |
| Nov 20 | AMEX SETTLEMENT | $980.00 | P-1020 (48220) | $980.00 |  |

## Payments not in the bank yet

| Payment | Date | Customer | Invoice | Type | Amount | Days | |
|---|---|---|---|---|---:|---:|---|
| P-1007 | Nov 17 | Priya Nandakumar | 48207 | Visa | $189.00 | 3 |  |
| P-1008 | Nov 17 | Owen Stricklin | 48208 | Visa | $742.30 | 3 |  |
| P-1011 | Nov 17 | Dana Whitfield | 48201 | Visa | -$50.00 | 3 |  |
| P-1016 | Nov 18 | Nell Hargrove | 48216 | Financing | $12,600.00 | 2 |  |
| P-1019 | Nov 19 | Rosa Delgado | 48219 | Financing | $6,200.00 | 1 |  |
| P-1021 | Nov 20 | Mina Sato | 48221 | Visa | $275.00 | 0 |  |
| P-1022 | Nov 20 | Paul Everhart | 48222 | Check | $640.00 | 0 |  |

This report is read-only. Nothing was posted or changed in the payments system, the books or the bank.
