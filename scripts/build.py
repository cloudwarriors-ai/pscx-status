#!/usr/bin/env python3
"""Check each site, keep 90 days of history and build the status page.

Usage:
    python3 scripts/build.py --check   # run checks, append history, rebuild
    python3 scripts/build.py           # rebuild docs/ from existing history

Standard library only. Paths are relative to the repository root.
"""

import argparse
import csv
import html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES_FILE = os.path.join(ROOT, "sites.json")
HISTORY_DIR = os.path.join(ROOT, "history")
DOCS_DIR = os.path.join(ROOT, "docs")

HEADER = ["utc_time", "up", "status", "ms"]
KEEP_DAYS = 90
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CURL_TIMEOUT_SECONDS = 20
FOOTER = ("Checks run every 10 minutes from GitHub's servers. "
          "Report a security issue: ")
SECURITY_EMAIL = "security@pscx.ai"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_time(value):
    return value.strftime(TIME_FORMAT)


def parse_time(text):
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "site"


def load_sites(path=SITES_FILE):
    with open(path, encoding="utf-8") as handle:
        sites = json.load(handle)
    slugs = set()
    for site in sites:
        if not site.get("name") or not str(site.get("url", "")).startswith("https://"):
            raise ValueError("each site needs a name and an https:// url: %r" % (site,))
        slug = slugify(site["name"])
        if slug in slugs:
            raise ValueError("two sites share the file name %s" % slug)
        slugs.add(slug)
    return sites


def is_up(status_code):
    return 200 <= status_code < 400


def check_site(url):
    """Return (status_code, milliseconds). Status 0 means no HTTP response."""
    command = [
        "curl", "--silent", "--output", "/dev/null", "--location",
        "--max-redirs", "5", "--max-time", str(CURL_TIMEOUT_SECONDS),
        "--user-agent", "pscx-status-check",
        "--write-out", "%{http_code} %{time_total}", url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=CURL_TIMEOUT_SECONDS + 10)
        code_text, seconds_text = result.stdout.split()
        return int(code_text), int(round(float(seconds_text) * 1000))
    except (subprocess.TimeoutExpired, ValueError):
        return 0, CURL_TIMEOUT_SECONDS * 1000


def read_history(path):
    """Return rows as dicts with parsed types; ignore malformed lines."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append({
                    "time": parse_time(row["utc_time"]),
                    "up": row["up"] == "1",
                    "status": int(row["status"]),
                    "ms": int(row["ms"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
    rows.sort(key=lambda r: r["time"])
    return rows


def trim(rows, now, keep_days=KEEP_DAYS):
    cutoff = now - timedelta(days=keep_days)
    return [r for r in rows if r["time"] > cutoff]


def write_history(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for r in rows:
            writer.writerow([format_time(r["time"]), "1" if r["up"] else "0",
                             r["status"], r["ms"]])


def uptime(rows, now, window):
    """Percentage of checks that were up within the window, or None."""
    start = now - window
    relevant = [r for r in rows if start < r["time"] <= now]
    if not relevant:
        return None
    return 100.0 * sum(1 for r in relevant if r["up"]) / len(relevant)


def daily_uptime(rows, now, days=KEEP_DAYS):
    """List of (date, percent or None) for the last `days` UTC days, oldest first."""
    today = now.date()
    buckets = {}
    for r in rows:
        buckets.setdefault(r["time"].date(), []).append(r["up"])
    result = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        checks = buckets.get(day)
        percent = 100.0 * sum(checks) / len(checks) if checks else None
        result.append((day, percent))
    return result


def format_percent(value):
    if value is None:
        return "No data"
    if value == 100.0:
        return "100%"
    return "%.2f%%" % value


def summarize(site, rows, now):
    last = rows[-1] if rows else None
    return {
        "name": site["name"],
        "url": site["url"],
        "up": last["up"] if last else None,
        "status": last["status"] if last else None,
        "ms": last["ms"] if last else None,
        "last_check": format_time(last["time"]) if last else None,
        "uptime": {
            "24h": uptime(rows, now, timedelta(hours=24)),
            "7d": uptime(rows, now, timedelta(days=7)),
            "90d": uptime(rows, now, timedelta(days=90)),
        },
        "daily": daily_uptime(rows, now),
    }


def day_class(percent):
    if percent is None:
        return "none"
    if percent == 100.0:
        return "ok"
    if percent >= 95.0:
        return "partial"
    return "down"


def render_site(summary):
    name = html.escape(summary["name"])
    url = html.escape(summary["url"], quote=True)
    if summary["up"] is None:
        state, state_class = "No data yet", "none"
    elif summary["up"]:
        state, state_class = "Operational", "ok"
    else:
        state, state_class = "Down", "down"
    last = summary["last_check"]
    last_text = last.replace("T", " ").replace("Z", " UTC") if last else "Never"
    bad_days = sum(1 for _, p in summary["daily"] if p is not None and p < 100.0)
    bars = "".join(
        '<span class="day %s" title="%s: %s"></span>'
        % (day_class(p), day.isoformat(), html.escape(format_percent(p)))
        for day, p in summary["daily"]
    )
    windows = "".join(
        "<div><dt>%s</dt><dd>%s</dd></div>"
        % (label, html.escape(format_percent(summary["uptime"][key])))
        for key, label in (("24h", "24 hours"), ("7d", "7 days"), ("90d", "90 days"))
    )
    return (
        '<section class="site">\n'
        '<h2><a href="%s">%s</a> <span class="state %s">%s</span></h2>\n'
        '<dl class="uptime">%s</dl>\n'
        '<div class="bar" role="img" aria-label="Daily uptime for the last 90 days: '
        '%d day(s) below 100%%">%s</div>\n'
        '<div class="axis" aria-hidden="true"><span>90 days ago</span><span>Today</span></div>\n'
        '<p class="last">Last check: <time datetime="%s">%s</time></p>\n'
        "</section>"
    ) % (url, name, state_class, state, windows, bad_days, bars,
         html.escape(last or ""), html.escape(last_text))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>PSCX Status</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --card: #f6f8fa; --line: #d1d9e0;
  --ok: #1a7f37; --partial: #9a6700; --down: #cf222e; --none: #d1d9e0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --card: #161b22; --line: #3d444d;
    --ok: #3fb950; --partial: #d29922; --down: #f85149; --none: #3d444d;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px; }
h1 { font-size: 1.6rem; margin: 0 0 16px; }
.banner { padding: 12px 16px; border-radius: 8px; color: #fff; font-weight: 600; margin-bottom: 24px; }
.banner.ok { background: var(--ok); } .banner.down { background: var(--down); }
.banner.none { background: var(--muted); }
@media (prefers-color-scheme: dark) { .banner { color: #0d1117; } }
.site { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px; margin-bottom: 16px; }
.site h2 { font-size: 1.15rem; margin: 0 0 12px; display: flex; flex-wrap: wrap;
  justify-content: space-between; gap: 8px; }
.site h2 a { color: inherit; }
.state { font-size: 0.95rem; }
.state.ok { color: var(--ok); } .state.down { color: var(--down); } .state.none { color: var(--muted); }
.uptime { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 0 0 12px; }
.uptime dt { color: var(--muted); font-size: 0.85rem; }
.uptime dd { margin: 0; font-weight: 600; }
.bar { display: flex; gap: 2px; height: 32px; }
.day { flex: 1 1 0; min-width: 1px; border-radius: 2px; background: var(--none); }
.day.ok { background: var(--ok); } .day.partial { background: var(--partial); }
.day.down { background: var(--down); }
.axis { display: flex; justify-content: space-between; color: var(--muted); font-size: 0.8rem; margin-top: 4px; }
.last { color: var(--muted); font-size: 0.9rem; margin: 8px 0 0; }
footer { color: var(--muted); font-size: 0.9rem; border-top: 1px solid var(--line);
  margin-top: 24px; padding-top: 16px; }
footer a { color: inherit; }
</style>
</head>
<body>
<main>
<h1>PSCX Status</h1>
<p class="banner %(banner_class)s" role="status">%(banner)s</p>
%(sites)s
<footer><p>%(footer)s<a href="mailto:%(email)s">%(email)s</a></p>
<p>Page generated <time datetime="%(generated)s">%(generated_text)s</time>.</p></footer>
</main>
</body>
</html>
"""


def render_page(summaries, now):
    states = [s["up"] for s in summaries]
    if not states or all(state is None for state in states):
        banner, banner_class = "Waiting for the first check", "none"
    elif all(states):
        banner, banner_class = "All systems operational", "ok"
    else:
        banner, banner_class = "Some systems are down", "down"
    generated = format_time(now)
    return PAGE % {
        "banner": banner,
        "banner_class": banner_class,
        "sites": "\n".join(render_site(s) for s in summaries),
        "footer": html.escape(FOOTER),
        "email": SECURITY_EMAIL,
        "generated": generated,
        "generated_text": generated.replace("T", " ").replace("Z", " UTC"),
    }


def status_json(summaries, now):
    sites = []
    for s in summaries:
        item = {k: s[k] for k in ("name", "url", "up", "status", "ms", "last_check")}
        item["uptime"] = {k: (round(v, 3) if v is not None else None)
                          for k, v in s["uptime"].items()}
        sites.append(item)
    return json.dumps({"schema_version": 1, "generated_at": format_time(now),
                       "sites": sites}, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="check every site and append the result first")
    args = parser.parse_args()

    now = utc_now()
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)
    summaries = []
    for site in load_sites():
        path = os.path.join(HISTORY_DIR, slugify(site["name"]) + ".csv")
        rows = read_history(path)
        if args.check:
            status, ms = check_site(site["url"])
            rows.append({"time": now, "up": is_up(status), "status": status, "ms": ms})
            print("%s up=%s status=%d ms=%d" % (site["name"], is_up(status), status, ms))
        rows = trim(rows, now)
        write_history(path, rows)
        summaries.append(summarize(site, rows, now))

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(render_page(summaries, now))
    with open(os.path.join(DOCS_DIR, "status.json"), "w", encoding="utf-8") as handle:
        handle.write(status_json(summaries, now))


if __name__ == "__main__":
    main()
