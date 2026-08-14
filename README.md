# Faculty Publication Search

Builds a browsable, sorted list of recent publications for the faculty in a
department, using [OpenAlex](https://openalex.org) author IDs. The output is a
single self-contained HTML file you can project at a faculty meeting, host
anywhere, or print.

Python 3.8+ standard library only, no dependencies.

You need a **free OpenAlex API key**: sign up at [openalex.org](https://openalex.org)
and copy it from [openalex.org/settings/api](https://openalex.org/settings/api)
(about 30 seconds). Since February 2026 OpenAlex expects a key for anything
beyond demo use. Set it as `OPENALEX_API_KEY`, or pass `--api-key`.

## Setup

**1. Fill in the roster.** `faculty.csv` has one row per person:

```csv
name,openalex_ids
Ryan N. Gutenkunst,A5056381387;A5138474539
Jane Doe,A5012345678
```

OpenAlex frequently splits one researcher across several author records
(different name spellings, ORCID vs. non-ORCID). List every ID for a person in
one cell, separated by semicolons, and their works get merged.

**2. Find the IDs.** Search a name at [openalex.org](https://openalex.org) and
open the author's page; the ID is the last part of the URL
(`openalex.org/works?filter=author.id:A5056381387`). Or query the API directly
and read off the candidates:

```bash
curl -s "https://api.openalex.org/authors?search=Jane%20Doe" \
  | python3 -m json.tool | grep -E '"id"|"display_name"|"works_count"'
```

Check the works count and institution before pasting — name search is fuzzy, and
common names return many matches. If a person looks split across two records
with substantial counts in each, put both IDs in their cell.

A wrong ID is caught on the next run: anyone returning zero publications is
re-checked against OpenAlex, and the run reports whether the ID is invalid or
the person simply published nothing in the window.

## Use

```bash
python3 update.py --mailto you@example.edu
```

That fetches everything published in the last year and writes
`site/index.html`. To change that window permanently, edit
`DEFAULT_WINDOW_DAYS` at the top of `fetch_pubs.py` — it is a rolling window
measured from the day of the run, so the scheduled job keeps moving forward.
Per-run options:

| Option | Effect |
|---|---|
| `--since 2026-01-01` | only works published on or after this date |
| `--all` | no date limit (full career output) |
| `--no-preprints` | drop bioRxiv/arXiv preprints |
| `--keep-abstracts` | include conference/meeting abstracts (off by default) |
| `--no-collapse` | list preprint and published versions as separate entries |
| `--title "MCB Publications"` | page heading |
| `--serve` | rebuild, then serve at `http://localhost:8000` |

Passing `--mailto` puts you in OpenAlex's "polite pool", which is faster and
more reliable. Both it and `--api-key` fall back to the `OPENALEX_MAILTO` and
`OPENALEX_API_KEY` environment variables, so you can export them once instead of
passing flags every time.

The page itself has a search box (title, author, journal), a per-faculty
filter, and a preprint toggle. Papers are grouped by year, newest first, with
departmental authors in bold. Co-authored papers appear once, tagged with
everyone involved.

## If you hit the rate limit

OpenAlex bills API calls against a daily budget that resets at midnight UTC.
What budget you get depends entirely on whether you send a key:

| | Daily budget | Tied to |
|---|---|---|
| No API key | ~$0.10, demo use only | **your IP address** |
| Free API key | $1 (~10,000 list calls) | **your key** |

The distinction matters more than the size. Without a key the budget belongs to
the IP address, so on any shared host — a CI runner, a campus NAT — somebody
else may already have spent it before you start. This is exactly why the GitHub
Actions job needs a key: runners use shared cloud IPs whose keyless budget is
routinely gone.

A full run of a ~30-person roster costs about 3 calls, because author IDs are
OR-batched 25 at a time rather than queried one per person. Against a free key's
10,000 that is unlimited for practical purposes.

If it happens, `fetch_pubs.py` stops immediately (exit code 2) rather than
retrying, since every retry spends another credit for nothing. It reports when
the budget resets and leaves the existing `site/index.html` untouched, so the
page you already published stays live. Just re-run after the reset.

Each successful run prints the remaining budget. To reduce usage further, fetch
a narrower window (`--since`), and rebuild the page with `build_site.py` alone,
which re-renders from the cached JSON and makes no API calls at all.

## Publishing

`site/index.html` is fully self-contained, so any static host works — GitHub
Pages, a departmental web server, or `scp` to a public_html directory. To
refresh monthly from your own machine, add a cron entry:

```
0 6 1 * * cd /path/to/faculty_pub_search && /usr/bin/python3 update.py --mailto you@example.edu
```

## Hosting on GitHub Pages, refreshed automatically

One file does all of it:
[`.github/workflows/refresh-publications.yml`](.github/workflows/refresh-publications.yml).
It fetches from OpenAlex, rebuilds the page, and publishes it to GitHub Pages.
No separate host is needed — Pages serves the built HTML directly, and the whole
thing is free on a public repository.

Setup, once:

1. Push this repo to GitHub. Make it **public** unless you have a paid plan;
   Pages from a private repo requires one. (A faculty publication list is public
   information anyway.)
2. **Settings > Pages > Build and deployment > Source: GitHub Actions.** This is
   the step people forget — without it, the deploy step fails.
3. **Settings > Secrets and variables > Actions > New repository secret**. Add
   `OPENALEX_API_KEY` (required — see the rate limit section above for why), and
   `OPENALEX_MAILTO` set to your email.
4. **Actions > Refresh publications > Run workflow** to publish immediately
   rather than waiting for the first scheduled run.

The site lands at `https://<user>.github.io/<repo>/`. Point a departmental
subdomain at it with a `CNAME` file if you want a nicer URL.

It then refreshes nightly at 11:17 UTC (4:17 AM Arizona). For weekly, change the
cron to `"17 11 * * 1"` (Mondays). The **Run workflow** button refreshes on
demand before a meeting.

### Things worth knowing about the scheduled run

- **Scheduled runs drift.** GitHub delays `schedule` events under load and can
  skip them entirely; runs are not guaranteed to be punctual. The cron is set to
  `:17` rather than `:00` because the top of the hour is the worst congestion.
  Harmless here — a publication list does not care about a 20-minute delay — but
  do not rely on it firing at an exact time.
- **A 60-day quiet repo disables the schedule.** GitHub automatically disables
  scheduled workflows in public repositories after 60 days with no activity. The
  job commits `data/publications.json` whenever the list changes, which normally
  keeps the repo active, but if the department publishes nothing for two months
  check that the workflow is still enabled.
- **Failures are safe.** If OpenAlex is rate limited, `update.py` exits non-zero
  and the job stops *before* the deploy step, so the previously published page
  stays live. You get a stale page and a failed-run email, never a broken one.
- **Costs nothing.** Actions minutes are unlimited on public repositories, and
  this job runs for well under a minute. Pages allows 1 GB published and 100 GB
  of bandwidth per month; this page is under 100 KB.
- **The bot commit will not retrigger the workflow.** Pushes made with the
  built-in `GITHUB_TOKEN` do not start new workflow runs, so there is no risk of
  a refresh loop.

## Layout

Everything tracked in git, and why:

| File | Purpose |
|---|---|
| `faculty.csv` | the roster you maintain — the only file you edit routinely |
| `openalex.py` | API client (OR-batching, paging, retries, rate-limit handling) |
| `roster.py` | roster loading and validation |
| `fetch_pubs.py` | fetch + dedupe + filter → `data/publications.json` |
| `build_site.py` | JSON → `site/index.html` |
| `update.py` | both steps; this is what the workflow runs |
| `.github/workflows/refresh-publications.yml` | the scheduled fetch + Pages deploy |
| `data/publications.json` | last fetch. Committed for history and to keep the schedule alive |
| `README.md`, `.gitignore` | this file, and the exclusions below |

Those are enough on their own: a clone with nothing else builds the whole site,
creating `data/` and `site/` as it goes.

Deliberately **not** in the repo:

| Excluded | Why |
|---|---|
| `site/` | build output — Pages serves it as a workflow artifact, so committing it would only create churn |
| `__pycache__/`, `*.pyc` | bytecode |
| `.DS_Store` | macOS clutter |
| `.claude/` | local assistant/editor settings, specific to one machine |

There are no secrets in the repo. The API key and your email reach the workflow
through the `OPENALEX_API_KEY` and `OPENALEX_MAILTO` GitHub secrets. The key is
sent as a query parameter and is redacted from every error message, so it cannot
leak into a workflow log or into the published page.

The fetch and build steps are separate so you can rebuild the page (retitle it,
tweak the styling) without re-querying the API.

## Notes on the data

- OpenAlex is comprehensive but imperfect. If someone's list looks short, they
  probably have a second author record — search their name on openalex.org
  and add the extra ID.
- Anyone who returns zero works gets checked against the authors endpoint, so
  the run tells you whether the ID is wrong or the person simply published
  nothing in the window. That check costs one extra request, and only runs when
  somebody came back empty.
- OpenAlex indexes each *version* of a paper separately, so one paper can show
  up as a bioRxiv preprint, an institutional repository copy, and the journal
  article. These are merged by title plus shared authorship, keeping the
  journal version and noting that a preprint exists. `--no-collapse` turns
  this off.
- Records that are clearly not publications (peer review reports, grants,
  errata, front matter) are filtered out; see `NOISE_TYPES` in `fetch_pubs.py`.
- Very recent papers can take a few weeks to appear in OpenAlex.
- `publication_date` is OpenAlex's best guess and sometimes reflects the online
  date rather than the issue date.
