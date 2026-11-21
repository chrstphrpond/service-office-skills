"""Run from the skill folder: python3 -m unittest discover -s tests"""
import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import review_jobs as rj  # noqa: E402

CONFIG = json.loads((ROOT / "config.example.json").read_text())
HEADER = ["Job #", "Completed", "Customer", "Technician", "Job Type", "Status", "Invoice #", "Invoice Total",
          "Balance Due", "Payment Type", "Summary", "Recall Of", "Discount"]
GOOD_NOTES = "Replaced the part and tested the unit."


def row(job="1", date="11/20/2026", customer="Test Customer", tech="Ann Tester", job_type="Service call", status="Completed",
        invoice=None, total="150.00", balance="0.00", pay="Visa", notes=GOOD_NOTES, recall="", discount="0.00"):
    invoice = f"INV{job}" if invoice is None else invoice
    return [job, date, customer, tech, job_type, status, invoice, total, balance, pay, notes, recall, discount]


def write_csv(folder, rows, header=HEADER):
    path = Path(folder) / "jobs.csv"
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows([header] + rows)
    return path


def run(rows, config=CONFIG, header=HEADER):
    with tempfile.TemporaryDirectory() as d:
        jobs, absent = rj.load_jobs(write_csv(d, rows, header), config)
    return rj.review(jobs, config, absent)


def checks_for(result, job):
    return [k for k, items in result["by_check"].items() for i in items if i["job"] == job]


def cli(rows=None, config=CONFIG, header=HEADER, raw=None):
    """Run main() in a temp folder. Returns (exit code, stderr, out folder exists, out folder path)."""
    d = tempfile.mkdtemp()
    jobs = Path(d) / "jobs.csv"
    if raw is not None:
        jobs.write_bytes(raw)
    else:
        write_csv(d, rows, header)
    cfg = Path(d) / "config.json"
    cfg.write_text(json.dumps(config))
    out = Path(d) / "out"
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = rj.main(["--jobs", str(jobs), "--config", str(cfg), "--out", str(out)])
    return code, err.getvalue(), out


class Amounts(unittest.TestCase):
    def test_reads_dollars_commas_and_negatives(self):
        self.assertEqual(rj.to_cents("$1,245.50", "x"), 124550)
        self.assertEqual(rj.to_cents("(50.00)", "x"), -5000)
        self.assertEqual(rj.to_cents("-50", "x"), -5000)
        self.assertEqual(rj.to_cents("0.07", "x"), 7)

    def test_rejects_what_is_not_dollars_and_cents(self):
        for bad in ["", "12.345", "abc", "NaN", "12..5"]:
            with self.assertRaises(rj.InputError, msg=bad):
                rj.to_cents(bad, "x")

    def test_money_format(self):
        self.assertEqual(rj.money(-5), "-$0.05")
        self.assertEqual(rj.money(123456789), "$1,234,567.89")


class EachCheck(unittest.TestCase):
    def test_clean_job_has_no_flags(self):
        r = run([row("1")])
        self.assertEqual(r["counts"]["flags"], 0)

    def test_completed_job_without_invoice(self):
        r = run([row("1", invoice="", total="")])
        self.assertEqual(checks_for(r, "1"), ["no_invoice"])

    def test_no_invoice_is_not_also_a_zero_dollar_flag(self):
        r = run([row("1", invoice="", total="0.00")])
        self.assertEqual(checks_for(r, "1"), ["no_invoice"])

    def test_zero_dollar_invoice_on_a_service_call(self):
        r = run([row("1", total="0.00")])
        self.assertEqual(checks_for(r, "1"), ["zero_dollar"])

    def test_zero_dollar_is_fine_for_warranty_and_recall(self):
        r = run([row("1", job_type="Warranty", total="0.00"), row("2", job_type="recall", total="0.00")])
        self.assertEqual(r["counts"]["by_check"]["zero_dollar"], 0)

    def test_balance_due_with_a_card_is_flagged(self):
        r = run([row("1", total="200.00", balance="200.00", pay="Visa")])
        self.assertEqual(checks_for(r, "1"), ["balance_due"])
        self.assertIn("$200.00", r["by_check"]["balance_due"][0]["reason"])

    def test_balance_due_is_fine_when_billed_later(self):
        r = run([row("1", balance="150.00", pay="On account"), row("2", balance="150.00", pay="financing")])
        self.assertEqual(r["counts"]["by_check"]["balance_due"], 0)

    def test_balance_due_with_blank_payment_type_is_flagged(self):
        r = run([row("1", balance="50.00", pay="")])
        self.assertEqual(checks_for(r, "1"), ["balance_due"])

    def test_missing_notes_and_short_notes(self):
        r = run([row("1", notes=""), row("2", notes="done"), row("3", notes="x" * 25), row("4", notes="x" * 24)])
        self.assertEqual(checks_for(r, "1") + checks_for(r, "2") + checks_for(r, "4"), ["short_notes"] * 3)
        self.assertEqual(checks_for(r, "3"), [])

    def test_callback_with_the_original_in_the_file(self):
        r = run([row("10", tech="Ann Tester", date="11/19/2026"), row("11", tech="Bo Other", job_type="Recall", total="0.00", recall="10")])
        item = r["by_check"]["callback"][0]
        self.assertEqual((item["job"], item["original_job"], item["original_technician"], item["original_date"]), ("11", "10", "Ann Tester", "2026-11-19"))
        self.assertIn("Ann Tester", item["reason"])

    def test_callback_with_the_original_not_in_the_file(self):
        r = run([row("11", job_type="Recall", total="0.00", recall="999")])
        item = r["by_check"]["callback"][0]
        self.assertEqual(item["original_job"], "999")
        self.assertIsNone(item["original_technician"])
        self.assertIn("not in this file", item["reason"])

    def test_callback_finds_the_original_even_if_it_was_not_completed(self):
        r = run([row("10", status="Canceled"), row("11", job_type="Recall", total="0.00", recall="10")])
        self.assertEqual(r["by_check"]["callback"][0]["original_job"], "10")

    def test_total_below_the_minimum_for_the_job_type(self):
        r = run([row("1", total="59.00"), row("2", job_type="Maintenance", total="128.99")])
        self.assertEqual(checks_for(r, "1"), ["below_minimum"])
        self.assertEqual(checks_for(r, "2"), ["below_minimum"])

    def test_total_at_the_minimum_is_fine(self):
        r = run([row("1", total="89.00"), row("2", job_type="Install", total="10.00")])
        self.assertEqual(r["counts"]["by_check"]["below_minimum"], 0)

    def test_discount_over_the_limit(self):
        r = run([row("1", total="850.00", discount="150.01")])  # 150.01 of 1000.01 is just over 15%
        self.assertEqual(checks_for(r, "1"), ["big_discount"])

    def test_discount_exactly_at_the_limit_is_fine(self):
        r = run([row("1", total="850.00", discount="150.00")])
        self.assertEqual(r["counts"]["by_check"]["big_discount"], 0)

    def test_duplicate_invoice_numbers_flag_every_job_that_shares_one(self):
        r = run([row("1", invoice="A100"), row("2", invoice="a100"), row("3", invoice="A101")])
        self.assertEqual(checks_for(r, "1"), ["duplicate_invoice"])
        self.assertEqual(checks_for(r, "2"), ["duplicate_invoice"])
        self.assertEqual(checks_for(r, "3"), [])
        self.assertEqual(r["by_check"]["duplicate_invoice"][0]["other_jobs"], ["2"])

    def test_one_job_can_have_several_flags(self):
        r = run([row("1", total="50.00", balance="50.00", notes="ok")])
        self.assertEqual(checks_for(r, "1"), ["balance_due", "short_notes", "below_minimum"])
        self.assertEqual(r["counts"]["flagged_jobs"], 1)
        self.assertEqual(r["counts"]["flags"], 3)


class Skipping(unittest.TestCase):
    def test_other_statuses_are_counted_and_not_checked(self):
        r = run([row("1"), row("2", status="Canceled", invoice="", notes=""), row("3", status="In progress")])
        self.assertEqual((r["counts"]["reviewed"], r["counts"]["skipped"], r["counts"]["rows"]), (1, 2, 3))
        self.assertEqual(r["counts"]["flags"], 0)

    def test_status_match_ignores_case_and_spaces(self):
        r = run([row("1", status=" completed "), row("2", status="DONE")])
        self.assertEqual(r["counts"]["reviewed"], 2)

    def test_skipped_jobs_do_not_count_toward_totals(self):
        r = run([row("1", total="100.00"), row("2", total="900.00", status="Canceled")])
        self.assertEqual(r["totals"]["invoiced"], 10000)


class Totals(unittest.TestCase):
    def test_technician_totals_add_up_to_the_overall_totals(self):
        r = run([row("1", tech="Ann", total="100.00", balance="10.00", pay="On account"),
                 row("2", tech="Bo", total="250.50", balance="0.00"),
                 row("3", tech="ann", total="49.50", balance="0.00")])
        self.assertEqual(r["totals"], {"invoiced": 40000, "balance_due": 1000})
        self.assertEqual([(t["technician"], t["jobs"], t["invoiced"]) for t in r["by_technician"]], [("Ann", 2, 14950), ("Bo", 1, 25050)])

    def test_blank_technician_is_grouped_not_dropped(self):
        r = run([row("1", tech="")])
        self.assertEqual(r["by_technician"][0]["technician"], "(no technician)")

    def test_blank_amounts_count_as_zero(self):
        r = run([row("1", total="", balance="", invoice="")])
        self.assertEqual(r["totals"], {"invoiced": 0, "balance_due": 0})


class OptionalColumns(unittest.TestCase):
    def test_a_file_without_optional_columns_still_runs_and_says_what_was_skipped(self):
        keep = [i for i, h in enumerate(HEADER) if h not in ("Payment Type", "Summary", "Recall Of", "Discount")]
        header = [HEADER[i] for i in keep]
        rows = [[row("1", total="50.00", balance="50.00", notes="", recall="9", discount="40.00")[i] for i in keep]]
        r = run(rows, header=header)
        self.assertEqual(checks_for(r, "1"), ["below_minimum"])
        self.assertEqual(sorted(n["missing_column"] for n in r["not_run"]), ["discount", "payment_type", "recall_of", "summary"])
        self.assertIn("Not run:", rj.render(r))

    def test_all_columns_present_means_nothing_was_skipped(self):
        self.assertEqual(run([row("1")])["not_run"], [])


class BadInput(unittest.TestCase):
    def assert_stops(self, **kw):
        code, err, out = cli(**kw)
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("Stopped, nothing written"))
        self.assertFalse(out.exists(), "nothing may be written")
        return err

    def test_duplicate_job_numbers_stop_the_run(self):
        err = self.assert_stops(rows=[row("1"), row("1", invoice="OTHER")])
        self.assertIn("job number 1 appears twice", err)

    def test_missing_required_column_stops_the_run(self):
        header = [h for h in HEADER if h != "Technician"]
        err = self.assert_stops(rows=[[v for h, v in zip(HEADER, row("1")) if h != "Technician"]], header=header)
        self.assertIn("Technician", err)

    def test_bad_amount_stops_the_run(self):
        self.assert_stops(rows=[row("1", total="12.345")])

    def test_bad_balance_stops_the_run(self):
        self.assert_stops(rows=[row("1", balance="abc")])

    def test_bad_date_stops_the_run(self):
        self.assert_stops(rows=[row("1", date="2026-11-20")])

    def test_a_bad_date_on_a_skipped_job_still_stops_the_run(self):
        self.assert_stops(rows=[row("1"), row("2", status="Canceled", date="soon")])

    def test_blank_job_number_stops_the_run(self):
        self.assert_stops(rows=[row("")])

    def test_negative_total_stops_the_run(self):
        self.assert_stops(rows=[row("1", total="-20.00")])

    def test_negative_discount_stops_the_run(self):
        self.assert_stops(rows=[row("1", discount="-5.00")])

    def test_unreadable_file_stops_the_run(self):
        code, err, out = cli(raw=b"\xff\xfe\x00bad")
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_missing_file_stops_the_run(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = rj.main(["--jobs", "/no/such/file.csv", "--config", str(ROOT / "config.example.json"), "--out", "/no/such/out"])
        self.assertEqual(code, 2)

    def test_config_with_a_bad_rule_stops_the_run(self):
        for key, value in [("max_discount_percent", "lots"), ("minimum_invoice", {"Service call": "cheap"}), ("min_summary_length", "long")]:
            self.assert_stops(rows=[row("1")], config={**CONFIG, key: value})

    def test_config_missing_a_key_stops_the_run(self):
        self.assert_stops(rows=[row("1")], config={k: v for k, v in CONFIG.items() if k != "completed_statuses"})


class ExitCodesAndReports(unittest.TestCase):
    def test_exit_0_when_nothing_is_flagged(self):
        code, err, out = cli(rows=[row("1"), row("2")])
        self.assertEqual(code, 0)
        self.assertIn("Nothing.", (out / "report.md").read_text())

    def test_exit_1_when_something_is_flagged_and_both_files_are_written(self):
        code, err, out = cli(rows=[row("1", total="0.00")])
        self.assertEqual(code, 1)
        self.assertTrue((out / "report.md").exists() and (out / "report.json").exists())

    def test_an_empty_export_is_fine(self):
        code, err, out = cli(rows=[])
        self.assertEqual(code, 0)

    def test_json_has_the_same_numbers_as_the_report(self):
        code, err, out = cli(rows=[row("1", tech="Ann", total="0.00"), row("2", tech="Bo", total="300.00", balance="300.00")])
        data = json.loads((out / "report.json").read_text())
        self.assertEqual(data["counts"]["flagged_jobs"], 2)
        self.assertEqual(data["totals_cents"], {"invoiced": 30000, "balance_due": 30000})
        self.assertEqual([t["technician"] for t in data["by_technician"]], ["Ann", "Bo"])
        self.assertEqual(data["by_technician"][1]["flagged_jobs"][0]["flags"][0]["check"], "balance_due")
        self.assertIn("$300.00", (out / "report.md").read_text())

    def test_report_ends_with_the_read_only_line(self):
        code, err, out = cli(rows=[row("1")])
        self.assertIn("read-only", (out / "report.md").read_text().strip().splitlines()[-1])

    def test_a_date_range_shows_in_the_title(self):
        r = run([row("1", date="11/19/2026"), row("2", date="11/20/2026")])
        self.assertIn("Nov 19, 2026 to Nov 20, 2026", rj.render(r))

    def test_the_sample_data_gives_the_planted_problems(self):
        with tempfile.TemporaryDirectory() as d:
            code = None
            with contextlib.redirect_stdout(io.StringIO()):
                code = rj.main(["--jobs", str(ROOT / "sample-data/jobs.csv"), "--config", str(ROOT / "config.example.json"), "--out", d])
            data = json.loads((Path(d) / "report.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual({k: v["count"] for k, v in data["by_check"].items()},
                         {"no_invoice": 1, "zero_dollar": 1, "balance_due": 1, "short_notes": 1, "callback": 2,
                          "below_minimum": 1, "big_discount": 1, "duplicate_invoice": 2})
        self.assertEqual((data["counts"]["reviewed"], data["counts"]["skipped"]), (24, 2))


if __name__ == "__main__":
    unittest.main()
