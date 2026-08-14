"""Render the cached publication JSON into a single self-contained HTML page.

    python3 build_site.py --title "MCB Recent Publications"

The output has no external dependencies, so you can open it locally, email it,
or drop it on any web host.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN = os.path.join(HERE, "data", "publications.json")
DEFAULT_OUT = os.path.join(HERE, "site", "index.html")

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #ffffff;
    --panel: #f6f7f9;
    --border: #e2e5ea;
    --text: #14181f;
    --muted: #5c6673;
    --accent: #0b5fa5;
    --badge-bg: #eef2f7;
    --badge-text: #435063;
    --preprint-bg: #fdf0e3;
    --preprint-text: #8a5117;
    --oa-bg: #e6f4ea;
    --oa-text: #1c6b34;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14171c;
      --panel: #1c2027;
      --border: #2c323b;
      --text: #e8eaee;
      --muted: #9aa4b1;
      --accent: #6cb6ff;
      --badge-bg: #262c35;
      --badge-text: #b3bdc9;
      --preprint-bg: #3a2c19;
      --preprint-text: #f0bb7a;
      --oa-bg: #1b3524;
      --oa-text: #85d3a1;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 32px 20px 80px; }
  header h1 { font-size: 1.9rem; margin: 0 0 6px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 0.92rem; margin: 0 0 24px; }
  .controls {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px; margin-bottom: 8px;
    position: sticky; top: 0; z-index: 5;
  }
  input[type="search"], select {
    font: inherit; color: var(--text); background: var(--bg);
    border: 1px solid var(--border); border-radius: 7px; padding: 8px 10px;
  }
  input[type="search"] { flex: 1 1 260px; min-width: 0; }
  select { flex: 0 1 auto; max-width: 100%; }
  label.check { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 0.9rem; }
  #count { color: var(--muted); font-size: 0.9rem; margin: 0 0 20px; }
  h2.year {
    font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.09em;
    color: var(--muted); border-bottom: 1px solid var(--border);
    padding-bottom: 6px; margin: 30px 0 4px;
  }
  ol.pubs { list-style: none; padding: 0; margin: 0; }
  li.pub { padding: 15px 0; border-bottom: 1px solid var(--border); }
  .title { font-size: 1.05rem; font-weight: 600; margin: 0 0 5px; }
  .title a { color: var(--accent); text-decoration: none; }
  .title a:hover { text-decoration: underline; }
  .authors { font-size: 0.92rem; color: var(--muted); margin: 0 0 5px; }
  .authors .fac { color: var(--text); font-weight: 700; }
  .meta { font-size: 0.88rem; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .meta .journal { font-style: italic; }
  .badge {
    font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px;
    background: var(--badge-bg); color: var(--badge-text);
  }
  .badge.preprint { background: var(--preprint-bg); color: var(--preprint-text); }
  .badge.oa { background: var(--oa-bg); color: var(--oa-text); }
  .empty { color: var(--muted); padding: 40px 0; text-align: center; }
  .warn {
    background: var(--preprint-bg); color: var(--preprint-text);
    border-radius: 8px; padding: 10px 12px; font-size: 0.88rem; margin-bottom: 16px;
  }
  footer { margin-top: 40px; color: var(--muted); font-size: 0.82rem; }
  footer a { color: var(--accent); }
  @media print {
    .controls { display: none; }
    body { font-size: 11pt; }
    li.pub { break-inside: avoid; }
    .title a { color: inherit; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>__TITLE__</h1>
    <p class="sub">__SUBTITLE__</p>
  </header>

  __WARNING__

  <div class="controls">
    <input type="search" id="q" placeholder="Search title, author, or journal…" autocomplete="off">
    <select id="person"><option value="">All faculty</option></select>
    <label class="check"><input type="checkbox" id="hidePre"> Hide preprints</label>
  </div>
  <p id="count"></p>

  <div id="list"></div>

  <footer>
    Publication data from <a href="https://openalex.org">OpenAlex</a>.
    Generated __GENERATED__.
  </footer>
</div>

<script id="payload" type="application/json">__DATA__</script>
<script>
(function () {
  var DATA = JSON.parse(document.getElementById("payload").textContent);
  var PUBS = DATA.publications || [];
  var FACULTY_IDS = {};
  Object.keys(DATA.roster || {}).forEach(function (name) {
    (DATA.roster[name] || []).forEach(function (id) { FACULTY_IDS[id] = true; });
  });

  // Precompute one lowercase haystack per publication so filtering stays
  // instant even with a few thousand papers.
  PUBS.forEach(function (p) {
    p._hay = [p.title, p.journal, p.faculty.join(" "),
              p.authors.map(function (a) { return a.name; }).join(" ")]
             .join(" ").toLowerCase();
  });

  var qEl = document.getElementById("q");
  var personEl = document.getElementById("person");
  var hidePreEl = document.getElementById("hidePre");
  var listEl = document.getElementById("list");
  var countEl = document.getElementById("count");

  (DATA.faculty || []).forEach(function (name) {
    var opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    personEl.appendChild(opt);
  });

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function authorHtml(p) {
    return p.authors.map(function (a) {
      var name = esc(a.name);
      return FACULTY_IDS[a.id] ? '<span class="fac">' + name + "</span>" : name;
    }).join(", ") || "<em>authors unlisted</em>";
  }

  function prettyDate(iso) {
    var parts = String(iso).split("-");
    if (parts.length < 3) return iso;
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var m = months[parseInt(parts[1], 10) - 1] || "";
    return m + " " + parseInt(parts[2], 10) + ", " + parts[0];
  }

  function pubHtml(p) {
    var bits = [];
    if (p.journal) bits.push('<span class="journal">' + esc(p.journal) + "</span>");
    if (p.volume) bits.push(esc(p.volume) + (p.issue ? "(" + esc(p.issue) + ")" : ""));
    if (p.pages) bits.push(esc(p.pages));
    var cite = bits.join(", ");

    var badges = "";
    if (p.type === "preprint") badges += '<span class="badge preprint">preprint</span>';
    else if (p.has_preprint) badges += '<span class="badge">preprint posted earlier</span>';
    if (p.is_oa) badges += '<span class="badge oa">open access</span>';
    if (p.citations > 0) {
      badges += '<span class="badge">' + p.citations +
                (p.citations === 1 ? " citation" : " citations") + "</span>";
    }

    var link = p.url || ("https://openalex.org/" + p.id);
    return '<li class="pub">' +
      '<p class="title"><a href="' + esc(link) + '" target="_blank" rel="noopener">' +
        esc(p.title) + "</a></p>" +
      '<p class="authors">' + authorHtml(p) + "</p>" +
      '<p class="meta">' + (cite ? cite + " " : "") +
        "<span>" + esc(prettyDate(p.date)) + "</span>" + badges + "</p>" +
    "</li>";
  }

  function render() {
    var q = qEl.value.trim().toLowerCase();
    var person = personEl.value;
    var hidePre = hidePreEl.checked;

    var shown = PUBS.filter(function (p) {
      if (hidePre && p.type === "preprint") return false;
      if (person && p.faculty.indexOf(person) === -1) return false;
      if (q && p._hay.indexOf(q) === -1) return false;
      return true;
    });

    countEl.textContent = shown.length + (shown.length === 1 ? " publication" : " publications") +
      (shown.length !== PUBS.length ? " of " + PUBS.length : "");

    if (!shown.length) {
      listEl.innerHTML = '<p class="empty">No publications match those filters.</p>';
      return;
    }

    // Group under year headings; PUBS is already sorted newest-first.
    var html = "";
    var currentYear = null;
    var open = false;
    shown.forEach(function (p) {
      var year = p.date.slice(0, 4);
      if (year !== currentYear) {
        if (open) html += "</ol>";
        html += '<h2 class="year">' + esc(year) + "</h2><ol class=\"pubs\">";
        currentYear = year;
        open = true;
      }
      html += pubHtml(p);
    });
    if (open) html += "</ol>";
    listEl.innerHTML = html;
  }

  qEl.addEventListener("input", render);
  personEl.addEventListener("change", render);
  hidePreEl.addEventListener("change", render);
  render();
})();
</script>
</body>
</html>
"""


def build(payload, title):
    generated = payload.get("generated", "")
    try:
        generated = dt.datetime.fromisoformat(generated).strftime("%B %-d, %Y at %-I:%M %p")
    except (ValueError, TypeError):
        pass

    pubs = payload.get("publications", [])
    n_faculty = len(payload.get("faculty", []))
    since = payload.get("since")
    window = "published since {}".format(since) if since else "all years"
    subtitle = "{} publications from {} faculty, {}. Newest first.".format(len(pubs), n_faculty, window)

    warning = ""
    errors = payload.get("errors") or []
    if errors:
        warning = '<div class="warn"><strong>{} lookup(s) failed</strong> — this list may be incomplete: {}</div>'.format(
            len(errors), "; ".join(str(e) for e in errors[:5])
        )

    # </script> inside the JSON would close the tag early; escape the slash.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    html = TEMPLATE
    for token, value in (
        ("__TITLE__", title),
        ("__SUBTITLE__", subtitle),
        ("__GENERATED__", generated),
        ("__WARNING__", warning),
        ("__DATA__", data),
    ):
        html = html.replace(token, value)
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default="Recent Publications", help="heading shown at the top of the page")
    parser.add_argument("--in", dest="infile", default=DEFAULT_IN, help="publications JSON from fetch_pubs.py")
    parser.add_argument("--out", default=DEFAULT_OUT, help="HTML file to write")
    args = parser.parse_args(argv)

    if not os.path.exists(args.infile):
        print("No data at {}. Run fetch_pubs.py first.".format(args.infile), file=sys.stderr)
        return 1

    with open(args.infile, encoding="utf-8") as handle:
        payload = json.load(handle)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(build(payload, args.title))

    print("Wrote {} ({:.1f} KB)".format(args.out, os.path.getsize(args.out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
