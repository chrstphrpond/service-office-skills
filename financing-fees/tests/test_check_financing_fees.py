"""Run from the skill folder: python3 -m unittest discover -s tests"""
import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_financing_fees as cf  # noqa: E402

CONFIG = json.loads((ROOT / "config.example.json").read_text())
P12, P18, P60 = "12 months no interest", "18 months no interest", "60 months 9.99%"


def day(m, n):
    return dt.date(2026, m, n)


def line(ref, financed, fee=None, net=None, plan=P12, d=day(11, 10), row=2, customer="Test Customer"):
    f = cf.to_cents(financed, ref)
    rate = {P12.lower(): "5.50", P18.lower(): "7.25", P60.lower(): "3.00"}.get(plan.lower(), "0")
    fee_c = cf.to_cents(fee, ref) if fee is not None else cf.fee_cents(f, rate)
    net_c = cf.to_cents(net, ref) if net is not None else f - fee_c
    return {"row": row, "date": d, "reference": ref, "customer": customer, "plan": plan,
            "financed": f, "fee": fee_c, "net": net_c}


def pay(pid, invoice, amount, plan=P12, d=day(11, 7), method="Financing"):
    return {"id": pid, "date": d, "invoice": invoice, "customer": "Test Customer", "method": method,
            "amount": cf.to_cents(amount, pid), "plan": plan}


def checks(result):
    return [i["check"] for i in result["items"]]


def analyze(lines, payments=None, config=CONFIG, as_of=None):
    return cf.analyze(lines, config, payments, as_of)


class Amounts(unittest.TestCase):
    def test_reads_dollars_commas_and_negatives(self):
        self.assertEqual(cf.to_cents("$1,245.50", "x"), 124550)
        self.assertEqual(cf.to_cents("(50.00)", "x"), -5000)
        self.assertEqual(cf.to_cents("0.07", "x"), 7)

    def test_rejects_what_is_not_dollars_and_cents(self):
        for bad in ["", "12.345", "abc", "NaN", "12..5"]:
            with self.assertRaises(cf.InputError, msg=bad):
                cf.to_cents(bad, "x")

    def test_fee_rounds_half_up_at_the_half_cent(self):
        self.assertEqual(cf.fee_cents(500100, "5.50"), 27506)   # 275.055 -> 275.06
        self.assertEqual(cf.fee_cents(500000, "5.50"), 27500)
        self.assertEqual(cf.fee_cents(1010, "2.50"), 25)        # 25.25 cents -> 25
        self.assertEqual(cf.fee_cents(1020, "2.50"), 26)        # 25.5 cents -> 26

    def test_money_and_percent_text(self):
        self.assertEqual(cf.money(-5), "-$0.05")
        self.assertEqual(cf.money(123456789), "$1,234,567.89")
        self.assertEqual(cf.percent_text(27506, 500100), "5.50%")
        self.assertEqual(cf.percent_text(1, 3), "33.33%")
        self.assertEqual(cf.percent_text(1, 8), "12.50%")
        self.assertEqual(cf.percent_text(1, 0), "")


class FeeCheck(unittest.TestCase):
    def test_a_correct_fee_is_not_flagged(self):
        self.assertEqual(analyze([line("1", "5000.00")])["items"], [])

    def test_the_half_cent_fee_is_accepted_rounded_up(self):
        self.assertEqual(analyze([line("1", "5001.00", fee="275.06")])["items"], [])

    def test_the_half_cent_fee_rounded_down_is_flagged(self):
        r = analyze([line("1", "5001.00", fee="275.05")])
        self.assertEqual(checks(r), ["fee_differs"])
        self.assertEqual(r["items"][0]["numbers"]["difference"], -1)
        self.assertIn("$0.01 under", r["items"][0]["message"])

    def test_fee_over_shows_expected_charged_and_difference(self):
        r = analyze([line("1", "18900.00", fee="661.50", plan=P60)])
        n = r["items"][0]["numbers"]
        self.assertEqual((n["expected"], n["charged"], n["difference"]), (56700, 66150, 9450))
        self.assertIn("$94.50 over", r["items"][0]["message"])

    def test_one_cent_off_is_flagged(self):
        self.assertEqual(checks(analyze([line("1", "6850.00", fee="376.76")])), ["fee_differs"])

    def test_plan_names_match_in_any_case(self):
        self.assertEqual(analyze([line("1", "5000.00", plan=P12.upper())])["items"], [])


class Arithmetic(unittest.TestCase):
    def test_net_one_cent_high_is_flagged(self):
        r = analyze([line("1", "8640.00", plan=P18, net="8013.61")])
        self.assertEqual(checks(r), ["arithmetic"])
        self.assertEqual(r["items"][0]["numbers"]["difference"], 1)
        self.assertIn("$0.01 more", r["items"][0]["message"])

    def test_net_short_is_flagged_as_less(self):
        r = analyze([line("1", "5000.00", net="4700.00")])
        self.assertEqual(checks(r), ["arithmetic"])
        self.assertIn("less", r["items"][0]["message"])

    def test_a_wrong_fee_with_a_consistent_net_only_flags_the_fee(self):
        self.assertEqual(checks(analyze([line("1", "5000.00", fee="300.00")])), ["fee_differs"])


class NoRate(unittest.TestCase):
    def test_unknown_plan_is_flagged_not_guessed(self):
        r = analyze([line("1", "7800.00", fee="624.00", plan="24 months no interest")])
        self.assertEqual(checks(r), ["no_rate"])
        self.assertIsNone(r["lines"][0]["expected"])
        self.assertEqual(r["totals"]["expected"], 0)

    def test_blank_plan_is_flagged(self):
        self.assertEqual(checks(analyze([line("1", "5000.00", fee="100.00", plan="")])), ["no_rate"])

    def test_unrated_fees_stay_out_of_the_difference(self):
        r = analyze([line("1", "7800.00", fee="624.00", plan="Other"), line("2", "5000.00")])
        self.assertEqual(r["totals"]["difference"], 0)
        self.assertEqual(r["totals"]["charged"], 62400 + 27500)
        self.assertEqual(r["totals"]["unrated"], 1)


class Duplicates(unittest.TestCase):
    def test_same_reference_twice_is_one_item_and_both_count_in_totals(self):
        r = analyze([line("9", "1000.00", row=2), line("9", "1000.00", row=3, d=day(11, 12))])
        self.assertEqual(checks(r), ["funded_twice"])
        self.assertEqual(r["totals"]["count"], 2)
        self.assertEqual(r["totals"]["financed"], 200000)

    def test_a_repeat_is_not_also_flagged_against_payments(self):
        r = analyze([line("9", "1000.00", row=2), line("9", "1000.00", row=3)], [pay("P1", "9", "1100.00")])
        self.assertEqual(checks(r).count("amount_differs"), 1)

    def test_different_references_are_fine(self):
        self.assertEqual(analyze([line("1", "1000.00"), line("2", "1000.00")])["items"], [])


class Payments(unittest.TestCase):
    def test_matching_payment_is_clean(self):
        self.assertEqual(analyze([line("1", "5000.00")], [pay("P1", "1", "5000.00")])["items"], [])

    def test_funded_with_no_payment(self):
        r = analyze([line("1", "5000.00")], [pay("P1", "2", "5000.00", d=day(11, 9))])
        self.assertEqual(checks(r), ["no_payment"])

    def test_amount_differs_shows_both_amounts(self):
        r = analyze([line("1", "10250.00", plan=P18)], [pay("P1", "1", "10520.00", plan=P18)])
        self.assertEqual(checks(r), ["amount_differs"])
        n = r["items"][0]["numbers"]
        self.assertEqual((n["financed"], n["payment"], n["difference"]), (1025000, 1052000, -27000))

    def test_amount_one_cent_apart_is_flagged(self):
        self.assertEqual(checks(analyze([line("1", "5000.00")], [pay("P1", "1", "5000.01")])), ["amount_differs"])

    def test_plan_differs(self):
        r = analyze([line("1", "5000.00")], [pay("P1", "1", "5000.00", plan=P18)])
        self.assertEqual(checks(r), ["plan_differs"])

    def test_blank_plan_on_the_payment_is_a_difference(self):
        self.assertEqual(checks(analyze([line("1", "5000.00")], [pay("P1", "1", "5000.00", plan="")])), ["plan_differs"])

    def test_only_financing_methods_are_used(self):
        r = analyze([line("1", "5000.00")], [pay("P1", "1", "5000.00"), pay("P2", "77", "300.00", method="Visa")])
        self.assertEqual(r["items"], [])
        self.assertEqual(r["unfunded"], [])

    def test_two_payments_for_one_invoice_go_to_a_person(self):
        r = analyze([line("1", "5000.00")], [pay("P1", "1", "2500.00"), pay("P2", "1", "2500.00")])
        self.assertEqual(checks(r), ["several_payments"])

    def test_old_unfunded_payment_is_flagged_late(self):
        r = analyze([line("1", "5000.00", d=day(11, 20))],
                    [pay("P1", "1", "5000.00"), pay("P2", "2", "900.00", d=day(11, 3))])
        self.assertEqual(checks(r), ["not_funded"])
        self.assertEqual(r["items"][0]["numbers"]["days"], 17)

    def test_unfunded_payment_inside_the_window_is_listed_but_not_flagged(self):
        r = analyze([line("1", "5000.00", d=day(11, 20))],
                    [pay("P1", "1", "5000.00"), pay("P2", "2", "900.00", d=day(11, 6))])
        self.assertEqual(r["items"], [])
        self.assertEqual([u["days"] for u in r["unfunded"]], [14])

    def test_one_day_past_the_window_is_late(self):
        r = analyze([line("1", "5000.00", d=day(11, 20))],
                    [pay("P1", "1", "5000.00"), pay("P2", "2", "900.00", d=day(11, 5))])
        self.assertEqual(checks(r), ["not_funded"])

    def test_as_of_changes_the_age(self):
        lines, pays = [line("1", "5000.00", d=day(11, 20))], [pay("P1", "1", "5000.00"), pay("P2", "2", "900.00", d=day(11, 10))]
        self.assertEqual(analyze(lines, pays)["items"], [])
        self.assertEqual(checks(analyze([dict(lines[0])], pays, as_of=day(12, 1))), ["not_funded"])

    def test_without_payments_none_of_these_checks_run(self):
        r = analyze([line("1", "5000.00")])
        self.assertIsNone(r["unfunded"])
        self.assertEqual(r["items"], [])


class Totals(unittest.TestCase):
    def test_plan_and_month_rows_add_up_to_the_total(self):
        lines = [line("1", "5000.00", d=day(10, 5)), line("2", "9400.00", plan=P18, d=day(10, 8)),
                 line("3", "7000.00", plan="Other", fee="500.00", d=day(11, 2)), line("4", "4000.00", plan=P60, d=day(11, 3))]
        r = analyze(lines)
        for rows in (r["by_plan"], r["by_month"]):
            self.assertEqual(sum(x["financed"] for x in rows), r["totals"]["financed"])
            self.assertEqual(sum(x["charged"] for x in rows), r["totals"]["charged"])
            self.assertEqual(sum(x["count"] for x in rows), 4)
        self.assertEqual([m["label"] for m in r["by_month"]], ["Oct 2026", "Nov 2026"])

    def test_effective_rate_is_a_two_decimal_percent(self):
        r = analyze([line("1", "18900.00", fee="661.50", plan=P60), line("2", "5000.00", plan=P60)])
        self.assertEqual(r["by_plan"][0]["effective_rate"], "3.40%")   # (661.50 + 150.00) / 23,900.00 = 3.3954

    def test_net_funded_is_the_statements_figure(self):
        r = analyze([line("1", "8640.00", plan=P18, net="8013.61")])
        self.assertEqual(r["totals"]["net"], 801361)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        p = self.dir / name
        p.write_text(text)
        return str(p)

    def config(self, **changes):
        c = json.loads(json.dumps(CONFIG))
        c.update(changes)
        return self.write("config.json", json.dumps(c))

    def run_cli(self, statement, payments=None, config=None, extra=()):
        argv = ["--statement", statement, "--config", config or self.config(), "--out", str(self.dir / "out")]
        if payments:
            argv += ["--payments", payments]
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cf.main(argv + list(extra))
        return code, err.getvalue()

    HEAD = "Funding Date,Reference,Customer,Plan,Financed Amount,Dealer Fee,Net Funded\n"

    def test_clean_statement_exits_0_and_writes_both_files(self):
        s = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,\"5,000.00\",275.00,\"4,725.00\"\n")
        code, _ = self.run_cli(s)
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "out" / "report.md").exists())
        data = json.loads((self.dir / "out" / "report.json").read_text())
        self.assertEqual(data["totals_cents"]["financed"], 500000)
        self.assertEqual(data["totals_cents"]["items_for_a_person"], 0)

    def test_flagged_statement_exits_1(self):
        s = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,300.00,4700.00\n")
        self.assertEqual(self.run_cli(s)[0], 1)

    def test_late_unfunded_payment_alone_exits_1(self):
        s = self.write("s.csv", self.HEAD + "11/20/2026,1,Ann,12 months no interest,5000.00,275.00,4725.00\n")
        p = self.write("p.csv", "Payment ID,Payment Date,Invoice #,Customer,Type,Amount,Batch,Financing Plan\n"
                       "P1,11/10/2026,1,Ann,Financing,5000.00,,12 months no interest\n"
                       "P2,11/01/2026,2,Bo,Financing,900.00,,12 months no interest\n")
        self.assertEqual(self.run_cli(s, p)[0], 1)

    def stops(self, statement, payments=None, config=None, extra=()):
        code, err = self.run_cli(statement, payments, config, extra)
        self.assertEqual(code, 2, err)
        self.assertFalse((self.dir / "out").exists(), "a stopped run must write nothing")
        self.assertIn("nothing written", err)

    def test_missing_file_stops(self):
        self.stops(str(self.dir / "nope.csv"))

    def test_missing_column_stops(self):
        self.stops(self.write("s.csv", "Funding Date,Reference\n11/10/2026,1\n"))

    def test_bad_amount_stops(self):
        self.stops(self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,27.5.00,4700.00\n"))

    def test_bad_date_stops(self):
        self.stops(self.write("s.csv", self.HEAD + "2026-11-10,1,Ann,12 months no interest,5000.00,275.00,4725.00\n"))

    def test_blank_reference_stops(self):
        self.stops(self.write("s.csv", self.HEAD + "11/10/2026,,Ann,12 months no interest,5000.00,275.00,4725.00\n"))

    def test_zero_financed_amount_stops(self):
        self.stops(self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,0.00,0.00,0.00\n"))

    def test_empty_statement_stops(self):
        self.stops(self.write("s.csv", self.HEAD))

    def test_bad_rate_in_config_stops(self):
        good = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,275.00,4725.00\n")
        for bad in ["abc", "-1", "100", 5.5, ""]:
            self.stops(good, config=self.config(plan_rates={P12: bad}))

    def test_unreadable_config_stops(self):
        good = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,275.00,4725.00\n")
        self.stops(good, config=self.write("bad.json", "{not json"))

    def test_bad_as_of_stops(self):
        good = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,275.00,4725.00\n")
        self.stops(good, extra=["--as-of", "11/20/2026"])

    def test_duplicate_payment_id_stops(self):
        s = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,275.00,4725.00\n")
        p = self.write("p.csv", "Payment ID,Payment Date,Invoice #,Customer,Type,Amount,Batch,Financing Plan\n"
                       "P1,11/05/2026,1,Ann,Financing,5000.00,,x\nP1,11/05/2026,2,Bo,Financing,5.00,,x\n")
        self.stops(s, p)

    def test_payments_with_a_bad_amount_stops_even_for_a_non_financing_row(self):
        s = self.write("s.csv", self.HEAD + "11/10/2026,1,Ann,12 months no interest,5000.00,275.00,4725.00\n")
        p = self.write("p.csv", "Payment ID,Payment Date,Invoice #,Customer,Type,Amount,Batch,Financing Plan\n"
                       "P1,11/05/2026,9,Ann,Visa,twelve,,\n")
        self.stops(s, p)


class SampleData(unittest.TestCase):
    def test_sample_run_flags_exactly_the_planted_problems(self):
        with tempfile.TemporaryDirectory() as out:
            with contextlib.redirect_stdout(io.StringIO()):
                code = cf.main(["--statement", str(ROOT / "sample-data/funding-statement.csv"),
                                "--payments", str(ROOT / "sample-data/payments.csv"),
                                "--config", str(ROOT / "config.example.json"), "--out", out])
            data = json.loads((Path(out) / "report.json").read_text())
        self.assertEqual(code, 1)
        found = sorted((i["check"], i["reference"]) for i in data["needs_person"])
        self.assertEqual(found, sorted([
            ("fee_differs", "47352"), ("arithmetic", "47365"), ("no_payment", "47371"), ("no_rate", "48414"),
            ("amount_differs", "48420"), ("plan_differs", "48426"), ("funded_twice", "48431"), ("not_funded", "48395")]))

    def test_committed_report_is_what_the_script_writes_now(self):
        with tempfile.TemporaryDirectory() as out:
            with contextlib.redirect_stdout(io.StringIO()):
                cf.main(["--statement", str(ROOT / "sample-data/funding-statement.csv"),
                         "--payments", str(ROOT / "sample-data/payments.csv"),
                         "--config", str(ROOT / "config.example.json"), "--out", out])
            for name in ("report.md", "report.json"):
                self.assertEqual((Path(out) / name).read_text(), (ROOT / "sample-data/report" / name).read_text(), name)


if __name__ == "__main__":
    unittest.main()
