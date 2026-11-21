"""Run from the skill folder: python3 -m unittest discover -s tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import record_run as rr  # noqa: E402

START = "2026-11-20T07:30:00-06:00"
END = "2026-11-20T07:38:00-06:00"


def call(log, *extra, status="ok", routine="Deposit match", started=START, finished=END):
    args = ["--log", str(log), "--routine", routine, "--status", status, "--started", started]
    if finished is not None:
        args += ["--finished", finished]
    return rr.main(args + list(extra))


class RecordRun(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "runs.jsonl"

    def lines(self):
        return self.log.read_text().splitlines()

    def test_appends_exactly_one_json_line(self):
        self.assertEqual(call(self.log, "--records", "14", "--message", "all fine", "--host", "office-pc-1"), 0)
        rows = [json.loads(x) for x in self.lines()]
        self.assertEqual(rows, [{"routine": "Deposit match", "status": "ok", "started": START, "finished": END,
                                 "records": 14, "message": "all fine", "host": "office-pc-1"}])
        self.assertTrue(self.log.read_text().endswith("\n"))

    def test_a_started_record_has_no_finish(self):
        self.assertEqual(call(self.log, status="started", finished=None), 0)
        self.assertEqual(json.loads(self.lines()[0]), {"routine": "Deposit match", "status": "started", "started": START})

    def test_a_second_call_adds_a_line_and_leaves_the_first_alone(self):
        call(self.log, "--records", "1")
        before = self.log.read_bytes()
        call(self.log, "--records", "2", routine="Parts order status")
        after = self.log.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(self.lines()), 2)

    def test_the_log_is_created_when_missing(self):
        self.assertFalse(self.log.exists())
        self.assertEqual(call(self.log), 0)
        self.assertTrue(self.log.exists())

    def test_a_time_without_an_offset_is_refused_and_nothing_is_written(self):
        self.assertEqual(call(self.log, started="2026-11-20T07:30:00"), 2)
        self.assertFalse(self.log.exists())

    def test_a_date_only_time_is_refused(self):
        self.assertEqual(call(self.log, finished="2026-11-20"), 2)

    def test_finish_before_start_is_refused(self):
        self.assertEqual(call(self.log, started=END, finished=START), 2)
        self.assertFalse(self.log.exists())

    def test_an_unknown_status_is_refused(self):
        self.assertEqual(call(self.log, status="done"), 2)

    def test_ok_and_failed_need_a_finish_time(self):
        self.assertEqual(call(self.log, finished=None), 2)
        self.assertEqual(call(self.log, status="failed", finished=None), 2)

    def test_started_with_a_finish_or_records_is_refused(self):
        self.assertEqual(call(self.log, status="started"), 2)
        self.assertEqual(call(self.log, "--records", "3", status="started", finished=None), 2)

    def test_negative_records_are_refused(self):
        self.assertEqual(call(self.log, "--records", "-1"), 2)

    def test_an_empty_routine_name_is_refused(self):
        self.assertEqual(call(self.log, routine="  "), 2)

    def test_a_log_that_cannot_be_written_exits_1(self):
        self.assertEqual(call(self.dir), 1)   # a folder, not a file

    def test_a_cut_off_last_line_is_kept_and_ours_starts_on_a_new_line(self):
        self.log.write_text('{"routine": "x", "sta')
        self.assertEqual(call(self.log), 0)
        text = self.log.read_text()
        self.assertTrue(text.startswith('{"routine": "x", "sta\n'))
        self.assertEqual(len(text.splitlines()), 2)

    def test_an_offset_without_a_colon_is_accepted(self):
        self.assertEqual(call(self.log, started="2026-11-20T07:30:00-0600", finished="2026-11-20T07:38:00-0600"), 0)
        self.assertEqual(json.loads(self.lines()[0])["started"], START)

    def test_z_offset_and_untidy_messages_are_handled(self):
        self.assertEqual(call(self.log, "--message", "line one\n  line two", started="2026-11-20T13:30:00Z", finished="2026-11-20T13:40:00Z"), 0)
        row = json.loads(self.lines()[0])
        self.assertEqual(row["message"], "line one line two")


if __name__ == "__main__":
    unittest.main()
