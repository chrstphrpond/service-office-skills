"""Run from the skill folder: python3 -m unittest discover -s tests"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_routines as cr  # noqa: E402

NOW = "2026-11-20T09:00:00-06:00"   # a Friday, 09:00 in Chicago
PATTERNS = json.loads((ROOT / "routines.example.json").read_text())["failure_patterns"]


def routine(name="Deposit match", days=("Mon", "Tue", "Wed", "Thu", "Fri"), due_by="07:30", max_minutes=20, expect_records=True, owner="Pat"):
    return {"name": name, "owner": owner, "days": list(days), "due_by": due_by, "max_minutes": max_minutes, "expect_records": expect_records}


def line(status, started, finished=None, name="Deposit match", records=None, message=None, host="pc-1"):
    row = {"routine": name, "status": status, "started": started}
    if finished:
        row["finished"] = finished
    if records is not None:
        row["records"] = records
    if message:
        row["message"] = message
    if host:
        row["host"] = host
    return row


def ok(day, start, end, records=5, name="Deposit match"):
    return line("ok", f"2026-11-{day:02d}T{start}:00-06:00", f"2026-11-{day:02d}T{end}:00-06:00", name, records)


def write(tmp, name, content):
    path = Path(tmp) / name
    path.write_text(content)
    return path


def schedule_file(tmp, routines=None, timezone="America/Chicago", patterns=None):
    data = {"timezone": timezone, "failure_patterns": PATTERNS if patterns is None else patterns, "routines": routines if routines is not None else [routine()]}
    return write(tmp, "routines.json", json.dumps(data))


def log_file(tmp, rows):
    return write(tmp, "runs.jsonl", "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))


def check(rows, routines=None, now=NOW, timezone="America/Chicago"):
    tmp = tempfile.mkdtemp()
    schedule = cr.load_schedule(schedule_file(tmp, routines, timezone))
    return cr.check(schedule, cr.load_log(log_file(tmp, rows)), cr.parse_time(now, "--now", "time"))


def status(result, name="Deposit match"):
    return next(r["status"] for r in result["results"] if r["name"] == name)


def cli(rows, routines=None, now=NOW, timezone="America/Chicago", raw_schedule=None):
    tmp = tempfile.mkdtemp()
    sched = write(tmp, "routines.json", raw_schedule) if raw_schedule is not None else schedule_file(tmp, routines, timezone)
    log = log_file(tmp, rows)
    code = cr.main(["--log", str(log), "--routines", str(sched), "--out", str(Path(tmp) / "out"), "--now", now])
    return code, Path(tmp) / "out", log


class EachStatus(unittest.TestCase):
    def test_ok_shows_finish_duration_and_records(self):
        r = check([ok(20, "07:05", "07:12", 14)])
        self.assertEqual(status(r), "OK")
        shown = r["results"][0]["shown"]
        self.assertEqual((shown["finished"] - shown["started"]).total_seconds(), 7 * 60)
        self.assertEqual(shown["records"], 14)

    def test_failed_gives_the_cause_message_and_host(self):
        r = check([line("failed", "2026-11-20T06:40:00-06:00", "2026-11-20T06:41:00-06:00", message="Session expired at ServiceTitan", host="dispatch-pc")])
        res = r["results"][0]
        self.assertEqual((res["status"], res["cause"]), ("Failed", "Signed out of the website"))
        self.assertEqual((res["shown"]["message"], res["shown"]["host"]), ("Session expired at ServiceTitan", "dispatch-pc"))

    def test_failed_with_an_unmatched_message_says_unknown(self):
        r = check([line("failed", "2026-11-20T06:40:00-06:00", "2026-11-20T06:41:00-06:00", message="Disk full")])
        self.assertEqual(r["results"][0]["cause"], "Unknown, see message")

    def test_failure_patterns_are_tried_in_order_and_ignore_case(self):
        r = check([line("failed", "2026-11-20T06:40:00-06:00", "2026-11-20T06:41:00-06:00", message="HTTP 401 and it timed out")])
        self.assertEqual(r["results"][0]["cause"], "Signed out of the website")
        r = check([line("failed", "2026-11-20T06:40:00-06:00", "2026-11-20T06:41:00-06:00", message="Host Unreachable")])
        self.assertEqual(r["results"][0]["cause"], "Computer was off or offline")

    def test_stuck_when_started_and_past_max_minutes(self):
        self.assertEqual(status(check([line("started", "2026-11-20T07:45:00-06:00")])), "Stuck")

    def test_running_within_max_minutes(self):
        self.assertEqual(status(check([line("started", "2026-11-20T08:45:00-06:00")])), "Running")

    def test_exactly_max_minutes_is_still_running(self):
        self.assertEqual(status(check([line("started", "2026-11-20T08:40:00-06:00")])), "Running")
        self.assertEqual(status(check([line("started", "2026-11-20T08:39:00-06:00")])), "Stuck")

    def test_missed_when_due_has_passed_and_nothing_ran(self):
        self.assertEqual(status(check([ok(19, "07:05", "07:12")])), "Missed")

    def test_missed_with_an_empty_log(self):
        self.assertEqual(status(check([])), "Missed")

    def test_not_due_yet_before_the_due_time(self):
        self.assertEqual(status(check([], now="2026-11-20T07:00:00-06:00")), "Not due yet")

    def test_exactly_at_the_due_time_is_not_yet_missed(self):
        self.assertEqual(status(check([], now="2026-11-20T07:30:00-06:00")), "Not due yet")
        self.assertEqual(status(check([], now="2026-11-20T07:31:00-06:00")), "Missed")

    def test_did_nothing_when_ok_with_zero_records_and_records_expected(self):
        self.assertEqual(status(check([ok(20, "07:05", "07:07", 0)])), "Did nothing")

    def test_zero_records_is_fine_when_not_expected(self):
        r = check([ok(20, "07:05", "07:07", 0)], routines=[routine(expect_records=False)])
        self.assertEqual(status(r), "OK")

    def test_an_ok_run_that_did_not_report_records_is_not_flagged(self):
        self.assertEqual(status(check([ok(20, "07:05", "07:07", None)])), "OK")

    def test_not_scheduled_today_is_listed_and_never_flagged(self):
        r = check([], routines=[routine(days=("Mon",))])
        self.assertEqual(status(r), "Not scheduled today")
        self.assertEqual(r["scheduled"], 0)
        self.assertFalse(cr.needs_person(r))

    def test_a_failure_then_a_good_retry_is_ok(self):
        rows = [line("failed", "2026-11-20T07:00:00-06:00", "2026-11-20T07:01:00-06:00", message="login"), ok(20, "07:10", "07:15")]
        self.assertEqual(status(check(rows)), "OK")

    def test_a_good_run_then_a_failed_retry_is_failed(self):
        rows = [ok(20, "07:00", "07:05"), line("failed", "2026-11-20T07:10:00-06:00", "2026-11-20T07:11:00-06:00", message="login")]
        self.assertEqual(status(check(rows)), "Failed")


class PriorityOrder(unittest.TestCase):
    def test_failed_beats_stuck_and_missed(self):
        rows = [line("started", "2026-11-20T06:00:00-06:00"), line("failed", "2026-11-20T07:00:00-06:00", "2026-11-20T07:01:00-06:00", message="offline")]
        self.assertEqual(status(check(rows)), "Failed")

    def test_stuck_beats_missed(self):
        self.assertEqual(status(check([line("started", "2026-11-20T06:00:00-06:00")])), "Stuck")

    def test_did_nothing_beats_running(self):
        rows = [ok(20, "07:00", "07:02", 0), line("started", "2026-11-20T08:55:00-06:00")]
        self.assertEqual(status(check(rows)), "Did nothing")

    def test_a_run_still_in_progress_after_the_due_time_is_running_not_missed(self):
        rows = [line("started", "2026-11-20T08:50:00-06:00")]
        self.assertEqual(status(check(rows, routines=[routine(due_by="08:00", max_minutes=30)])), "Running")

    def test_an_old_open_run_replaced_by_a_later_good_run_is_not_stuck(self):
        rows = [line("started", "2026-11-20T06:00:00-06:00"), ok(20, "07:00", "07:05")]
        self.assertEqual(status(check(rows)), "OK")

    def test_a_finish_record_closes_its_start_record(self):
        s = "2026-11-20T07:00:00-06:00"
        rows = [line("started", s), line("ok", s, "2026-11-20T07:05:00-06:00", records=3)]
        r = check(rows)
        self.assertEqual(status(r), "OK")
        self.assertEqual(r["notes"], [])

    def test_exactly_one_status_each_and_counts_add_up(self):
        routines = [routine("A"), routine("B"), routine("C", days=("Mon",)), routine("D", due_by="23:00")]
        r = check([ok(20, "07:00", "07:05", name="A")], routines=routines)
        self.assertEqual([x["status"] for x in r["results"]], ["OK", "Missed", "Not scheduled today", "Not due yet"])
        self.assertEqual(sum(r["counts"].values()), r["scheduled"])
        self.assertEqual(r["scheduled"], 3)


class TimeZonesAndMidnight(unittest.TestCase):
    def test_a_run_started_before_midnight_counts_for_the_day_it_started(self):
        rows = [line("ok", "2026-11-19T23:50:00-06:00", "2026-11-20T00:10:00-06:00", records=4)]
        self.assertEqual(status(check(rows)), "Missed")   # yesterday's run does not satisfy today

    def test_a_run_just_after_midnight_counts_for_the_new_day(self):
        rows = [line("ok", "2026-11-20T00:05:00-06:00", "2026-11-20T00:10:00-06:00", records=4)]
        self.assertEqual(status(check(rows)), "OK")

    def test_times_in_other_offsets_are_compared_as_real_moments(self):
        rows = [line("ok", "2026-11-20T13:05:00+00:00", "2026-11-20T13:12:00+00:00", records=4)]   # 07:05 in Chicago
        self.assertEqual(status(check(rows)), "OK")

    def test_today_is_the_local_date_not_the_utc_date(self):
        # 21:00 in Chicago is already Nov 21 in UTC. A run at 07:05 Chicago on Nov 20 is still today.
        rows = [ok(20, "07:05", "07:12")]
        self.assertEqual(status(check(rows, now="2026-11-21T03:00:00+00:00")), "OK")
        r = check(rows, now="2026-11-21T03:00:00+00:00")
        self.assertEqual(r["today"].isoformat(), "2026-11-20")

    def test_the_weekday_follows_the_local_date(self):
        # Sat Nov 21 03:00 UTC is still Friday evening in Chicago, so a Friday routine is scheduled.
        self.assertEqual(status(check([], now="2026-11-21T03:00:00+00:00")), "Missed")
        self.assertEqual(status(check([], now="2026-11-21T07:00:00+00:00", routines=[routine(days=("Fri",))])), "Not scheduled today")

    def test_an_offset_without_a_colon_is_read(self):
        rows = [line("ok", "2026-11-20T07:05:00-0600", "2026-11-20T07:12:00-0600", records=4)]
        self.assertEqual(status(check(rows)), "OK")

    def test_a_run_after_now_is_ignored(self):
        rows = [ok(20, "09:30", "09:40")]
        self.assertEqual(status(check(rows)), "Missed")

    def test_a_run_that_finishes_after_now_is_still_open_at_now(self):
        rows = [ok(20, "08:50", "09:10")]
        self.assertEqual(status(check(rows, routines=[routine(max_minutes=30)])), "Running")


EVERY_DAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class DaylightSaving(unittest.TestCase):
    def test_duration_across_spring_forward_is_real_elapsed_time(self):
        rows = [line("ok", "2026-03-08T01:50:00-06:00", "2026-03-08T03:10:00-05:00", records=2)]
        r = check(rows, now="2026-03-08T04:00:00-05:00", routines=[routine(days=EVERY_DAY, due_by="01:00")])
        shown = r["results"][0]["shown"]
        self.assertEqual((shown["finished"] - shown["started"]).total_seconds(), 20 * 60)

    def test_stuck_uses_real_minutes_over_the_spring_forward_gap(self):
        rows = [line("started", "2026-03-08T01:50:00-06:00")]
        routines = [routine(days=EVERY_DAY, due_by="01:00", max_minutes=30)]
        self.assertEqual(status(check(rows, now="2026-03-08T03:05:00-05:00", routines=routines)), "Running")   # 15 real minutes
        self.assertEqual(status(check(rows, now="2026-03-08T03:30:00-05:00", routines=routines)), "Stuck")     # 40 real minutes

    def test_stuck_uses_real_minutes_over_the_fall_back_repeat(self):
        rows = [line("started", "2026-11-01T01:50:00-05:00")]
        now = "2026-11-01T01:20:00-06:00"   # 30 real minutes later, though the clock reads earlier
        self.assertEqual(status(check(rows, now=now, routines=[routine(days=EVERY_DAY, due_by="01:00", max_minutes=45)])), "Running")
        self.assertEqual(status(check(rows, now=now, routines=[routine(days=EVERY_DAY, due_by="01:00", max_minutes=20)])), "Stuck")

    def test_a_due_time_that_does_not_exist_on_the_clock_still_works(self):
        r = check([], now="2026-03-08T09:00:00-05:00", routines=[routine(days=EVERY_DAY, due_by="02:30")])   # 02:30 is skipped that day
        self.assertEqual(status(r), "Missed")
        r = check([], now="2026-03-08T01:00:00-06:00", routines=[routine(days=EVERY_DAY, due_by="02:30")])
        self.assertEqual(status(r), "Not due yet")

    def test_the_due_time_follows_local_time_after_the_clocks_change(self):
        # Nov 2: CST. 07:30 local is 13:30 UTC.
        self.assertEqual(status(check([], now="2026-11-02T13:29:00+00:00")), "Not due yet")
        self.assertEqual(status(check([], now="2026-11-02T13:31:00+00:00")), "Missed")


class RepeatedTrouble(unittest.TestCase):
    def week(self, outcomes):
        """Mon Nov 16 .. Thu Nov 19 from a list of 'ok', 'failed', 'missed'."""
        rows = []
        for day, outcome in zip((16, 17, 18, 19), outcomes):
            if outcome == "ok":
                rows.append(ok(day, "07:00", "07:05"))
            elif outcome == "failed":
                rows.append(line("failed", f"2026-11-{day}T07:00:00-06:00", f"2026-11-{day}T07:01:00-06:00", message="offline"))
        return rows

    def trouble(self, rows, **kw):
        return check(rows, **kw)["results"][0]["trouble"]

    def test_two_bad_days_in_the_window_is_repeated_trouble(self):
        rows = self.week(["ok", "failed", "missed", "ok"]) + [ok(20, "07:00", "07:05")]
        t = self.trouble(rows)
        self.assertEqual([(d.isoformat(), o) for d, o in t["bad"]], [("2026-11-17", "failed"), ("2026-11-18", "missed")])

    def test_one_bad_day_is_not(self):
        self.assertIsNone(self.trouble(self.week(["ok", "failed", "ok", "ok"]) + [ok(20, "07:00", "07:05")]))

    def test_today_counts_when_it_is_failed_or_missed(self):
        t = self.trouble(self.week(["ok", "ok", "failed", "ok"]))   # nothing today, due has passed
        self.assertEqual([o for _, o in t["bad"]], ["failed", "missed"])
        self.assertEqual(t["bad"][-1][0].isoformat(), "2026-11-20")

    def test_today_not_due_yet_is_not_counted(self):
        rows = self.week(["ok", "failed", "failed", "ok"])
        r = check(rows, now="2026-11-20T06:00:00-06:00")["results"][0]
        self.assertEqual(r["status"], "Not due yet")
        self.assertEqual(r["trouble"]["days_checked"], 4)

    def test_only_the_last_five_scheduled_days_count(self):
        rows = [line("failed", "2026-11-09T07:00:00-06:00", "2026-11-09T07:01:00-06:00", message="x"),
                line("failed", "2026-11-10T07:00:00-06:00", "2026-11-10T07:01:00-06:00", message="x")]
        rows += self.week(["ok", "ok", "ok", "ok"]) + [ok(20, "07:00", "07:05")]
        self.assertIsNone(self.trouble(rows))   # Nov 16..20 are the five days

    def test_days_before_the_log_began_are_not_counted_as_missed(self):
        t = check([ok(19, "07:00", "07:05"), ok(20, "07:00", "07:05")])["results"][0]["trouble"]
        self.assertIsNone(t)

    def test_weekends_are_not_scheduled_days(self):
        rows = [ok(13, "07:00", "07:05"), ok(16, "07:00", "07:05")]
        r = check(rows, now="2026-11-16T09:00:00-06:00")["results"][0]
        self.assertIsNone(r["trouble"])

    def test_a_routine_not_scheduled_today_can_still_have_trouble(self):
        rows = [line("failed", "2026-11-16T07:00:00-06:00", "2026-11-16T07:01:00-06:00", message="x"),
                line("failed", "2026-11-17T07:00:00-06:00", "2026-11-17T07:01:00-06:00", message="x"),
                ok(18, "07:00", "07:05"), ok(19, "07:00", "07:05"), ok(20, "07:00", "07:05")]
        r = check(rows, now="2026-11-21T09:00:00-06:00")["results"][0]   # a Saturday
        self.assertEqual(r["status"], "Not scheduled today")
        self.assertEqual(len(r["trouble"]["bad"]), 2)


class Unknowns(unittest.TestCase):
    def test_runs_for_unlisted_routines_are_reported(self):
        r = check([ok(20, "07:00", "07:05"), ok(19, "16:00", "16:05", name="Old export"), ok(20, "08:00", "08:05", name="Old export")])
        self.assertEqual([(u["routine"], u["runs"]) for u in r["unknown"]], [("Old export", 2)])
        self.assertEqual(status(r), "OK")

    def test_an_unfinished_run_from_an_earlier_day_is_mentioned(self):
        r = check([line("started", "2026-11-18T07:00:00-06:00")])
        self.assertTrue(any("no finish record" in n for n in r["notes"]))

    def test_two_finish_records_for_one_run_are_noted(self):
        s = "2026-11-20T07:00:00-06:00"
        r = check([line("ok", s, "2026-11-20T07:05:00-06:00", records=1), line("ok", s, "2026-11-20T07:06:00-06:00", records=2)])
        self.assertEqual(len(r["notes"]), 1)


class BadInput(unittest.TestCase):
    def stops(self, rows=None, raw_schedule=None, routines=None, timezone="America/Chicago", now=NOW):
        code, out, _ = cli(rows if rows is not None else [ok(20, "07:00", "07:05")], routines, now, timezone, raw_schedule)
        self.assertEqual(code, 2)
        self.assertFalse(out.exists(), "a stopped run must write nothing")

    def message(self, rows):
        tmp = tempfile.mkdtemp()
        with self.assertRaises(cr.InputError) as ctx:
            cr.load_log(log_file(tmp, rows))
        return str(ctx.exception)

    def test_a_line_that_is_not_json_names_its_line_number(self):
        self.assertIn("line 2", self.message([ok(20, "07:00", "07:05"), "{not json"]))
        self.stops(rows=[ok(20, "07:00", "07:05"), "{not json"])

    def test_a_time_with_no_offset_stops_the_run(self):
        bad = line("ok", "2026-11-20T07:00:00", "2026-11-20T07:05:00-06:00")
        self.assertIn("line 1", self.message([bad]))
        self.stops(rows=[bad])

    def test_finish_before_start_stops_the_run(self):
        bad = line("ok", "2026-11-20T07:05:00-06:00", "2026-11-20T07:00:00-06:00")
        self.assertIn("before started", self.message([bad]))
        self.stops(rows=[bad])

    def test_an_unknown_status_stops_the_run(self):
        self.stops(rows=[line("done", "2026-11-20T07:00:00-06:00", "2026-11-20T07:05:00-06:00")])

    def test_ok_without_a_finish_time_stops_the_run(self):
        self.stops(rows=[line("ok", "2026-11-20T07:00:00-06:00")])

    def test_a_started_line_with_a_finish_time_stops_the_run(self):
        self.stops(rows=[line("started", "2026-11-20T07:00:00-06:00", "2026-11-20T07:05:00-06:00")])

    def test_bad_records_and_unknown_fields_stop_the_run(self):
        base = ok(20, "07:00", "07:05")
        self.stops(rows=[{**base, "records": -1}])
        self.stops(rows=[{**base, "records": "5"}])
        self.stops(rows=[{**base, "recrods": 5}])

    def test_a_missing_log_stops_the_run(self):
        tmp = tempfile.mkdtemp()
        sched = schedule_file(tmp)
        self.assertEqual(cr.main(["--log", str(Path(tmp) / "none.jsonl"), "--routines", str(sched), "--out", str(Path(tmp) / "o"), "--now", NOW]), 2)

    def test_now_without_an_offset_stops_the_run(self):
        self.stops(now="2026-11-20T09:00:00")

    def test_an_unknown_timezone_stops_the_run(self):
        self.stops(timezone="Mars/Olympus")

    def test_duplicate_routine_names_stop_the_run(self):
        self.stops(routines=[routine("A"), routine("a")])

    def test_a_bad_schedule_stops_the_run(self):
        self.stops(raw_schedule="{broken")
        self.stops(routines=[routine(days=("Funday",))])
        self.stops(routines=[routine(due_by="7:30")])
        self.stops(routines=[routine(max_minutes=0)])
        self.stops(routines=[{k: v for k, v in routine().items() if k != "owner"}])
        self.stops(routines=[])


class FilesAndExitCodes(unittest.TestCase):
    def test_a_clean_day_exits_0(self):
        code, out, _ = cli([ok(20, "07:00", "07:05")])
        self.assertEqual(code, 0)
        self.assertIn("Nothing.", (out / "report.md").read_text())

    def test_only_not_due_yet_and_running_exit_0(self):
        routines = [routine("A", due_by="23:00"), routine("B", max_minutes=60)]
        code, _, _ = cli([line("started", "2026-11-20T08:30:00-06:00", name="B")], routines)
        self.assertEqual(code, 0)

    def test_a_problem_exits_1_and_writes_both_files(self):
        code, out, _ = cli([])
        self.assertEqual(code, 1)
        self.assertTrue((out / "report.md").exists() and (out / "report.json").exists())

    def test_the_log_is_never_changed(self):
        tmp = tempfile.mkdtemp()
        log = log_file(tmp, [ok(20, "07:00", "07:05")])
        before = hashlib.sha256(log.read_bytes()).hexdigest()
        cr.main(["--log", str(log), "--routines", str(schedule_file(tmp)), "--out", str(Path(tmp) / "o"), "--now", NOW])
        self.assertEqual(hashlib.sha256(log.read_bytes()).hexdigest(), before)

    def test_the_default_now_is_the_current_time(self):
        tmp = tempfile.mkdtemp()
        code = cr.main(["--log", str(log_file(tmp, [])), "--routines", str(schedule_file(tmp)), "--out", str(Path(tmp) / "o")])
        self.assertIn(code, (0, 1))

    def test_json_carries_the_same_counts_as_the_report(self):
        code, out, _ = cli([ok(20, "07:05", "07:12", 14)], [routine("A"), routine("B"), routine("C", days=("Mon",))])
        data = json.loads((out / "report.json").read_text())
        self.assertEqual((data["scheduled_today"], data["counts"]["OK"], data["counts"]["Missed"], data["not_scheduled_today"]), (2, 0, 2, 1))
        self.assertEqual(data["needs_person_count"], 2)
        md = (out / "report.md").read_text()
        self.assertIn("2 routines scheduled today: 0 OK, 2 need a person.", md)


class SampleData(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.code = cr.main(["--log", str(ROOT / "sample-data/runs.jsonl"), "--routines", str(ROOT / "routines.example.json"),
                             "--out", str(self.tmp), "--now", "2026-11-20T09:00:00-06:00"])
        self.data = json.loads((self.tmp / "report.json").read_text())

    def test_the_planted_problems_are_caught(self):
        by = {r["name"]: r for r in self.data["routines"]}
        self.assertEqual(self.code, 1)
        self.assertEqual((by["Yesterday's jobs review"]["status"], by["Yesterday's jobs review"]["cause"]), ("Failed", "Signed out of the website"))
        self.assertEqual(by["Financing fee check"]["status"], "Missed")
        self.assertEqual(by["Parts order status"]["status"], "Stuck")
        self.assertEqual(by["Unpaid invoice list"]["status"], "Did nothing")
        self.assertEqual(by["Deposit match"]["status"], "OK")
        self.assertEqual(by["Maintenance agreement renewals"]["status"], "Not scheduled today")
        self.assertEqual([r["name"] for r in self.data["routines"] if r["repeated_trouble"]], ["Financing fee check"])
        self.assertEqual([u["routine"] for u in self.data["not_in_schedule"]], ["Old payroll export"])

    def test_the_saved_sample_report_is_what_the_script_writes(self):
        for name in ("report.md", "report.json"):
            self.assertEqual((self.tmp / name).read_text(), (ROOT / "sample-data/report" / name).read_text(), name)


if __name__ == "__main__":
    unittest.main()
