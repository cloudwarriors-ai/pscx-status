# PSCX status page

The public status page for PSCX. It is a static page served by GitHub Pages and
rebuilt by a scheduled GitHub Actions workflow. There is no server and no
third-party service.

## How it works

1. Every 10 minutes, the `check` workflow (`.github/workflows/check.yml`) runs
   on GitHub's servers. You can also start it by hand from the Actions tab
   ("Run workflow").
2. `scripts/build.py --check` sends one HTTPS GET to each site in `sites.json`
   with curl (20-second timeout, follows redirects). A site is **up** when the
   final response has a 2xx or 3xx status code. A timeout or connection error
   is recorded as status `0` (down).
3. The result is appended to `history/<slug>.csv` with the columns
   `utc_time,up,status,ms`. Rows older than 90 days are removed.
4. The script regenerates `docs/index.html` (the page) and `docs/status.json`
   (the same data as JSON). The workflow commits the changes with the message
   `status: <time> [skip ci]`.
5. A second job publishes `docs/` to GitHub Pages.

The page shows, for each site: the current state, uptime for the last 24 hours,
7 days and 90 days (share of checks that were up), a bar of daily uptime for
the last 90 days (green = 100%, amber = 95% or more, red = below 95%, grey = no
data), and the time of the last check in UTC.

The workflow uses only official `actions/*` actions pinned to full commit SHAs,
and only the built-in `GITHUB_TOKEN` with the smallest permissions each job
needs. Runs never overlap (concurrency group `status-check`).

## Add a site

Add an entry to `sites.json` and push to `main`:

```json
[
  {"name": "PSCX website", "url": "https://pscx.ai"},
  {"name": "New site", "url": "https://example.pscx.ai"}
]
```

- `url` must start with `https://` and be a public address.
- The history file name comes from the name (`New site` becomes
  `history/new-site.csv`). Renaming a site starts a new history file.

The site appears on the page after the next scheduled run, or run the workflow
by hand.

## Run locally

```sh
python3 -m unittest discover -s scripts   # tests
python3 scripts/build.py                  # rebuild docs/ from history, no checks
```

`python3 scripts/build.py --check` also runs real checks, but results from your
own machine should not be committed; let the workflow record them.

## Notes

- GitHub can delay scheduled runs by several minutes when it is busy, so the
  interval between checks is not exact.
- GitHub disables scheduled workflows in public repositories after 60 days
  without repository activity. If the last check time on the page stops
  moving, re-enable the workflow in the Actions tab.
