"""Unit tests for build.py. Run: python3 -m unittest discover -s scripts"""

import os
import sys
import tempfile
import subprocess
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


def row(minutes_ago, up=True, status=200, ms=100):
    return {"time": NOW - timedelta(minutes=minutes_ago), "up": up,
            "status": status, "ms": ms}


class UptimeTests(unittest.TestCase):
    def test_counts_only_checks_inside_window(self):
        rows = [row(60 * 25, up=False), row(30, up=True), row(20, up=False),
                row(10, up=True), row(0, up=True)]
        self.assertEqual(build.uptime(rows, NOW, timedelta(hours=24)), 75.0)
        self.assertEqual(build.uptime(rows, NOW, timedelta(days=7)), 60.0)

    def test_no_data_is_none(self):
        self.assertIsNone(build.uptime([], NOW, timedelta(hours=24)))
        self.assertEqual(build.format_percent(None), "No data")

    def test_up_means_2xx_or_3xx(self):
        self.assertTrue(build.is_up(200))
        self.assertTrue(build.is_up(301))
        self.assertFalse(build.is_up(404))
        self.assertFalse(build.is_up(500))
        self.assertFalse(build.is_up(0))

    def test_daily_uptime_buckets_by_utc_day(self):
        rows = [row(60 * 24 + 1, up=False), row(60 * 24 + 2, up=True), row(5, up=True)]
        daily = build.daily_uptime(rows, NOW)
        self.assertEqual(len(daily), 90)
        self.assertEqual(daily[-1], (NOW.date(), 100.0))
        self.assertEqual(daily[-2], (NOW.date() - timedelta(days=1), 50.0))
        self.assertEqual(daily[0], (NOW.date() - timedelta(days=89), None))

    def test_format_percent(self):
        self.assertEqual(build.format_percent(100.0), "100%")
        self.assertEqual(build.format_percent(99.31), "99.31%")


class CheckSiteTests(unittest.TestCase):
    def run_curl(self, returncode, stdout):
        result = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with mock.patch.object(build.subprocess, "run", return_value=result):
            return build.check_site("https://example.com")

    def test_success(self):
        self.assertEqual(self.run_curl(0, "200 0.250"), (200, 250))

    def test_curl_error_is_down_even_with_status(self):
        self.assertEqual(self.run_curl(47, "301 0.100")[0], 0)  # too many redirects
        self.assertEqual(self.run_curl(28, "200 20.000")[0], 0)  # timed out mid-body


class BannerTests(unittest.TestCase):
    def page(self, histories):
        sites = [build.summarize({"name": "s%d" % i, "url": "https://e.com"}, rows, NOW)
                 for i, rows in enumerate(histories)]
        return build.render_page(sites, NOW)

    def test_banner_states(self):
        self.assertIn("All systems operational", self.page([[row(0)], [row(0)]]))
        self.assertIn("Some systems are down", self.page([[row(0)], [row(0, up=False)]]))
        self.assertIn("no data yet", self.page([[row(0)], []]))


class TrimTests(unittest.TestCase):
    def test_keeps_only_last_90_days(self):
        rows = [row(60 * 24 * 91), row(60 * 24 * 90), row(60 * 24 * 89), row(0)]
        kept = build.trim(rows, NOW)
        self.assertEqual([r["time"] for r in kept], [rows[2]["time"], rows[3]["time"]])

    def test_history_round_trip_after_trim(self):
        rows = [row(60 * 24 * 100, up=False, status=503), row(10, up=False, status=0, ms=20000),
                row(0, status=200, ms=321)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "site.csv")
            build.write_history(path, build.trim(rows, NOW))
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            self.assertEqual(lines[0], "utc_time,up,status,ms")
            self.assertEqual(lines[1:], ["2026-11-20T11:50:00Z,0,0,20000",
                                         "2026-11-20T12:00:00Z,1,200,321"])
            self.assertEqual(build.read_history(path), rows[1:])


class EscapingTests(unittest.TestCase):
    def test_site_name_and_url_are_escaped(self):
        site = {"name": '<script>alert("x")</script> & Co', "url": 'https://example.com/"onmouseover="x'}
        summary = build.summarize(site, [row(0)], NOW)
        page = build.render_page([summary], NOW)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; Co", page)
        self.assertNotIn('"onmouseover="', page)

    def test_page_has_footer_and_no_scripts(self):
        page = build.render_page([build.summarize(
            {"name": "PSCX website", "url": "https://pscx.ai"}, [row(0)], NOW)], NOW)
        self.assertIn("Checks run every 10 minutes from GitHub&#x27;s servers.", page)
        self.assertIn("security@pscx.ai", page)
        self.assertIn("All systems operational", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("http-equiv=\"refresh\"", page)

    def test_slugify(self):
        self.assertEqual(build.slugify("PSCX website"), "pscx-website")
        self.assertEqual(build.slugify("../../etc"), "etc")


if __name__ == "__main__":
    unittest.main()
