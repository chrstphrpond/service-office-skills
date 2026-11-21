"""Run from the skill folder: python3 -m unittest discover -s tests"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import match_deposits as md  # noqa: E402

CONFIG = json.loads((ROOT / "config.example.json").read_text())


def day(n):
    return dt.date(2026, 11, n)


def pay(pid, d, method, amount, invoice="", customer="", batch="", plan=""):
    return {"id": pid, "date": day(d), "invoice": invoice, "customer": customer, "method": method,
            "amount": md.to_cents(amount, pid), "batch": batch, "plan": plan}


def dep(row, d, description, amount, reference=""):
    return {"row": row, "date": day(d), "description": description, "amount": md.to_cents(amount, "bank"), "reference": reference}


def run(payments, bank):
    return md.reconcile(payments, bank, CONFIG)


class Amounts(unittest.TestCase):
    def test_reads_dollars_commas_and_negatives(self):
        self.assertEqual(md.to_cents("$1,245.50", "x"), 124550)
        self.assertEqual(md.to_cents("(50.00)", "x"), -5000)
        self.assertEqual(md.to_cents("-50", "x"), -5000)
        self.assertEqual(md.to_cents("0.07", "x"), 7)

    def test_rejects_what_is_not_dollars_and_cents(self):
        for bad in ["", "12.345", "abc", "1e3.5", "NaN", "12..5"]:
            with self.assertRaises(md.InputError, msg=bad):
                md.to_cents(bad, "x")

    def test_fee_rounds_half_up_to_the_cent(self):
        self.assertEqual(md.fee_cents(845000, "5.50"), 46475)
        self.assertEqual(md.fee_cents(1010, "2.50"), 25)   # 25.25 cents -> 25
        self.assertEqual(md.fee_cents(1020, "2.50"), 26)   # 25.5 cents -> 26
        self.assertEqual(md.money(-5), "-$0.05")
        self.assertEqual(md.money(123456789), "$1,234,567.89")


class Cards(unittest.TestCase):
    def test_a_day_of_card_payments_matches_one_deposit(self):
        r = run([pay("A", 16, "Visa", "389.00"), pay("B", 16, "Mastercard", "1245.50")], [dep(2, 17, "BANKCARD MTOT DEP", "1634.50")])
        self.assertEqual([p["id"] for p in r["matched"][0]["payments"]], ["A", "B"])
        self.assertEqual(r["review"], [])

    def test_one_cent_off_is_not_a_match(self):
        r = run([pay("A", 16, "Visa", "389.00")], [dep(2, 17, "BANKCARD MTOT DEP", "388.99")])
        self.assertEqual(r["matched"], [])
        self.assertIn("$0.01 less", r["review"][0]["hint"])
        self.assertEqual(len(r["open"]), 1)

    def test_a_refund_in_the_batch_lowers_the_deposit(self):
        r = run([pay("A", 17, "Visa", "742.30"), pay("B", 17, "Visa", "-50.00")], [dep(2, 18, "BANKCARD MTOT DEP", "692.30")])
        self.assertEqual(len(r["matched"]), 1)

    def test_amex_and_other_cards_are_separate_batches(self):
        r = run([pay("A", 16, "Visa", "100.00"), pay("B", 16, "Amex", "200.00")],
                [dep(2, 17, "BANKCARD MTOT DEP", "100.00"), dep(3, 18, "AMEX SETTLEMENT", "200.00")])
        self.assertEqual(len(r["matched"]), 2)

    def test_a_batch_column_wins_over_the_date(self):
        r = run([pay("A", 16, "Visa", "100.00", batch="B1"), pay("B", 17, "Visa", "60.00", batch="B1"), pay("C", 17, "Visa", "40.00", batch="B2")],
                [dep(2, 18, "BANKCARD MTOT DEP", "160.00")])
        self.assertEqual([p["id"] for p in r["matched"][0]["payments"]], ["A", "B"])

    def test_two_batches_with_the_same_total_go_to_a_person(self):
        r = run([pay("A", 16, "Visa", "100.00"), pay("B", 17, "Visa", "100.00")], [dep(2, 18, "BANKCARD MTOT DEP", "100.00")])
        self.assertEqual(r["matched"], [])
        self.assertIn("2 batches", r["review"][0]["reason"])

    def test_a_deposit_dated_before_the_payment_never_matches(self):
        r = run([pay("A", 18, "Visa", "100.00")], [dep(2, 17, "BANKCARD MTOT DEP", "100.00")])
        self.assertEqual(r["matched"], [])

    def test_a_payment_outside_the_window_never_matches(self):
        r = run([pay("A", 2, "Visa", "100.00")], [dep(2, 17, "BANKCARD MTOT DEP", "100.00")])
        self.assertEqual(r["matched"], [])
        self.assertEqual(r["open"][0]["note"], "late")


class Financing(unittest.TestCase):
    def test_deposit_net_of_the_plan_fee_matches_and_records_the_fee(self):
        r = run([pay("A", 16, "Financing", "8450.00", "48205", plan="12 months no interest")], [dep(2, 18, "FINCO FUNDING INV 48205", "7985.25")])
        self.assertEqual(r["matched"][0]["fee"], 46475)
        self.assertEqual(r["totals"]["fees"], 46475)

    def test_a_different_fee_is_flagged_with_both_numbers(self):
        r = run([pay("A", 18, "Financing", "12600.00", "48216", plan="60 months 9.99%")], [dep(2, 20, "FINCO FUNDING INV 48216", "12096.00")])
        self.assertEqual(r["matched"], [])
        self.assertIn("should deduct $378.00", r["review"][0]["reason"])
        self.assertIn("$504.00 deducted", r["review"][0]["reason"])

    def test_the_gross_amount_is_not_accepted_as_a_match(self):
        r = run([pay("A", 16, "Financing", "8450.00", "48205", plan="12 months no interest")], [dep(2, 18, "FINCO FUNDING", "8450.00")])
        self.assertEqual(r["matched"], [])

    def test_a_plan_with_no_rate_is_never_guessed(self):
        r = run([pay("A", 16, "Financing", "1000.00", plan="24 months mystery")], [dep(2, 18, "FINCO FUNDING", "950.00")])
        self.assertEqual(r["matched"], [])
        self.assertIn("24 months mystery", r["review"][0]["hint"])

    def test_two_loans_funding_the_same_amount_need_the_invoice_on_the_bank_line(self):
        pays = [pay("A", 16, "Financing", "1000.00", "500", plan="60 months 9.99%"), pay("B", 16, "Financing", "1000.00", "501", plan="60 months 9.99%")]
        self.assertEqual(run(pays, [dep(2, 18, "FINCO FUNDING", "970.00")])["matched"], [])
        named = run(pays, [dep(2, 18, "FINCO FUNDING INV 501", "970.00")])
        self.assertEqual(named["matched"][0]["payments"][0]["id"], "B")


class ChecksAndCash(unittest.TestCase):
    def test_the_only_set_that_adds_up_is_matched(self):
        r = run([pay("A", 16, "Check", "220.00"), pay("B", 17, "Cash", "95.00"), pay("C", 17, "Check", "1480.00"), pay("D", 17, "Check", "640.00")],
                [dep(2, 17, "COUNTER DEPOSIT", "1795.00")])
        self.assertEqual([p["id"] for p in r["matched"][0]["payments"]], ["A", "B", "C"])
        self.assertEqual([o["payment"]["id"] for o in r["open"]], ["D"])

    def test_two_sets_that_add_up_go_to_a_person(self):
        r = run([pay("A", 16, "Check", "220.00"), pay("B", 16, "Check", "220.00"), pay("C", 16, "Check", "100.00")], [dep(2, 17, "COUNTER DEPOSIT", "320.00")])
        self.assertEqual(r["matched"], [])
        self.assertIn("More than one set", r["review"][0]["reason"])

    def test_one_match_can_make_the_next_one_unique(self):
        # Alone, the 300.00 deposit could be C or A+B. The 1,100.00 deposit can only be A+F,
        # and once A is used, C is the only way left to make 300.00.
        pays = [pay("A", 16, "Check", "100.00"), pay("B", 16, "Check", "200.00"), pay("C", 16, "Check", "300.00"), pay("F", 16, "Check", "1000.00")]
        r = run(pays, [dep(2, 17, "COUNTER DEPOSIT", "300.00"), dep(3, 17, "COUNTER DEPOSIT", "1100.00")])
        by_row = {m["bank"]["row"]: [p["id"] for p in m["payments"]] for m in r["matched"]}
        self.assertEqual(by_row, {3: ["A", "F"], 2: ["C"]})
        self.assertEqual([o["payment"]["id"] for o in r["open"]], ["B"])

    def test_no_set_adds_up(self):
        r = run([pay("A", 16, "Check", "220.00")], [dep(2, 17, "COUNTER DEPOSIT", "219.99")])
        self.assertIn("No set", r["review"][0]["reason"])

    def test_too_many_open_checks_goes_to_a_person(self):
        pays = [pay(f"P{i}", 16, "Check", f"{100 + i}.00") for i in range(md.SUBSET_LIMIT + 1)]
        r = run(pays, [dep(2, 17, "COUNTER DEPOSIT", "100.00")])
        self.assertIn("too many", r["review"][0]["reason"])


class Ach(unittest.TestCase):
    def test_two_payments_of_the_same_amount_are_told_apart_by_the_bank_line(self):
        pays = [pay("A", 17, "ACH", "3120.00", "48206", "Hollis Farms LLC"), pay("B", 17, "ACH", "3120.00", "48212", "Juniper Dental Group")]
        r = run(pays, [dep(2, 18, "ACH CREDIT JUNIPER DENTAL GROUP", "3120.00")])
        self.assertEqual(r["matched"][0]["payments"][0]["id"], "B")

    def test_same_amount_and_no_name_goes_to_a_person(self):
        pays = [pay("A", 17, "ACH", "3120.00", "48206", "Hollis Farms LLC"), pay("B", 17, "ACH", "3120.00", "48212", "Juniper Dental Group")]
        r = run(pays, [dep(2, 18, "ACH CREDIT", "3120.00")])
        self.assertEqual(r["matched"], [])
        self.assertIn("A, B", r["review"][0]["hint"])

    def test_an_invoice_number_inside_a_longer_number_does_not_count(self):
        self.assertFalse(md.names_payment("ACH CREDIT REF 148206", pay("A", 17, "ACH", "1.00", "48206")))
        self.assertTrue(md.names_payment("ACH CREDIT INV 48206", pay("A", 17, "ACH", "1.00", "48206")))


class Safety(unittest.TestCase):
    def test_a_payment_is_never_used_twice(self):
        r = run([pay("A", 16, "Visa", "455.00")], [dep(2, 17, "BANKCARD MTOT DEP", "455.00", "X1"), dep(3, 18, "BANKCARD MTOT DEP", "455.00", "X2")])
        self.assertEqual(len(r["matched"]), 1)
        self.assertEqual(len(r["review"]), 1)

    def test_a_repeated_bank_line_is_flagged_not_matched(self):
        r = run([pay("A", 16, "Visa", "455.00"), pay("B", 17, "Visa", "455.00")],
                [dep(2, 18, "BANKCARD MTOT DEP", "455.00", "BC1"), dep(3, 18, "BANKCARD MTOT DEP", "455.00", "BC1")])
        self.assertIn("repeats", r["review"][-1]["reason"])

    def test_a_bank_line_no_rule_covers_is_flagged(self):
        r = run([], [dep(2, 18, "WIRE IN REFUND", "318.40")])
        self.assertIn("No rule", r["review"][0]["reason"])

    def test_a_payment_type_no_rule_covers_is_flagged(self):
        r = run([pay("A", 16, "Gift card", "120.00")], [])
        self.assertEqual(r["open"][0]["note"], "no rule covers this payment type")
        self.assertTrue(md.needs_person(r))

    def test_withdrawals_are_skipped_not_matched(self):
        r = run([], [dep(2, 18, "MONTHLY SERVICE FEE", "-35.00")])
        self.assertEqual((r["deposits"], len(r["skipped"])), (0, 1))

    def test_every_bank_dollar_and_payment_dollar_is_accounted_for(self):
        r = run([pay("A", 16, "Visa", "389.00"), pay("B", 16, "Financing", "8450.00", "48205", plan="12 months no interest"), pay("C", 17, "Check", "75.10")],
                [dep(2, 17, "BANKCARD MTOT DEP", "389.00"), dep(3, 18, "FINCO FUNDING INV 48205", "7985.25"), dep(4, 18, "WIRE IN", "12.00")])
        t = r["totals"]
        self.assertEqual(t["bank_deposits"], t["matched"] + t["needs_person"])
        self.assertEqual(t["payments"], t["payments_matched"] + t["payments_open"])
        self.assertEqual(t["payments_matched"] - t["fees"], t["matched"])


class Files(unittest.TestCase):
    def cli(self, payments, bank, config=None):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "p.csv").write_text(payments)
        (tmp / "b.csv").write_text(bank)
        (tmp / "c.json").write_text(json.dumps(config or CONFIG))
        code = md.main(["--payments", str(tmp / "p.csv"), "--bank", str(tmp / "b.csv"), "--config", str(tmp / "c.json"), "--out", str(tmp / "out")])
        return code, tmp / "out"

    HEAD_P = "Payment ID,Payment Date,Invoice #,Customer,Type,Amount,Batch,Financing Plan\n"
    HEAD_B = "Date,Description,Amount,Reference\n"

    def test_a_clean_run_exits_0(self):
        code, out = self.cli(self.HEAD_P + "P1,11/16/2026,1,A,Visa,10.00,,\n", self.HEAD_B + "11/17/2026,BANKCARD MTOT DEP,10.00,R1\n")
        self.assertEqual(code, 0)
        self.assertIn("Nothing.", (out / "report.md").read_text())

    def test_a_duplicate_payment_id_stops_the_run_and_writes_nothing(self):
        code, out = self.cli(self.HEAD_P + "P1,11/16/2026,1,A,Visa,10.00,,\nP1,11/16/2026,2,B,Visa,20.00,,\n", self.HEAD_B)
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_a_bad_amount_stops_the_run(self):
        code, out = self.cli(self.HEAD_P + "P1,11/16/2026,1,A,Visa,ten,,\n", self.HEAD_B)
        self.assertEqual((code, out.exists()), (2, False))

    def test_a_missing_column_stops_the_run(self):
        code, _ = self.cli("Payment ID,Payment Date,Type\nP1,11/16/2026,Visa\n", self.HEAD_B)
        self.assertEqual(code, 2)

    def test_a_bad_date_stops_the_run(self):
        code, _ = self.cli(self.HEAD_P + "P1,2026-11-16,1,A,Visa,10.00,,\n", self.HEAD_B)
        self.assertEqual(code, 2)

    def test_the_sample_week(self):
        code, out = self.cli((ROOT / "sample-data/payments.csv").read_text(), (ROOT / "sample-data/bank.csv").read_text())
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual((len(report["matched"]), len(report["needs_person"]), len(report["open_payments"])), (10, 4, 7))
        self.assertEqual(report["totals_cents"]["bank_deposits"], 3626944)
        self.assertEqual(report["totals_cents"]["fees"], 46475)


if __name__ == "__main__":
    unittest.main()
