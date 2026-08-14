"""Fetch publications for everyone in faculty.csv and cache them as JSON.

    python3 fetch_pubs.py --since 2025-01-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys

import openalex
from roster import RosterError, load_roster

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "data", "publications.json")

# OpenAlex indexes a lot that nobody wants on a faculty-meeting slide:
# peer review reports, grant records, errata, front matter, blog posts.
NOISE_TYPES = {
    "peer-review",
    "grant",
    "erratum",
    "paratext",
    "editorial",
    "reference-entry",
    "other",
    "supplementary-materials",
}

# Conference/meeting abstracts are real records but usually clutter a
# department publication list, so they're dropped unless --keep-abstracts.
ABSTRACT_TYPES = {"conference-abstract", "abstract"}


_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw):
    """Strip the inline markup OpenAlex carries in titles and source names.

    Publisher metadata leaks tags like <tt>, <i>, <sub> and &amp; entities into
    the title field. Left alone they show up verbatim on the page and stop the
    preprint and published versions of a paper from matching each other.
    """
    text = html.unescape(raw or "")
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _journal(work):
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return clean_text(source.get("display_name") or location.get("raw_source_name"))


def _source_type(work):
    """'journal', 'repository', 'conference', ... Used to pick the best version."""
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return source.get("type") or ""


def _best_url(work):
    if work.get("doi"):
        return work["doi"]
    location = work.get("primary_location") or {}
    return location.get("landing_page_url") or work.get("id") or ""


def _authors(work):
    out = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        out.append(
            {
                "id": openalex.short_id(author.get("id")),
                "name": author.get("display_name") or authorship.get("raw_author_name") or "",
            }
        )
    return out


def _sort_date(work):
    """Date used for ordering; falls back to Jan 1 of the year if missing."""
    date = work.get("publication_date")
    if date:
        return date
    year = work.get("publication_year")
    return "{}-01-01".format(year) if year else "0000-01-01"


def normalize(work):
    biblio = work.get("biblio") or {}
    oa = work.get("open_access") or {}
    return {
        "id": openalex.short_id(work.get("id")),
        "title": clean_text(work.get("title")) or "Untitled",
        "date": _sort_date(work),
        "year": work.get("publication_year"),
        "type": work.get("type") or "",
        "journal": _journal(work),
        "source_type": _source_type(work),
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": (
            "{}-{}".format(biblio["first_page"], biblio["last_page"])
            if biblio.get("first_page") and biblio.get("last_page")
            else (biblio.get("first_page") or "")
        ),
        "doi": work.get("doi") or "",
        "url": _best_url(work),
        "is_oa": bool(oa.get("is_oa")),
        "oa_url": oa.get("oa_url") or "",
        "citations": work.get("cited_by_count") or 0,
        "authors": _authors(work),
        # Filled in by collect(): which roster members this work belongs to.
        "faculty": [],
        # Set by collapse_versions() when a preprint of this paper also exists.
        "has_preprint": False,
    }


def _title_key(title):
    """Normalized title for matching preprint and published versions."""
    return "".join(ch for ch in title.lower() if ch.isalnum())


def _version_rank(pub):
    """Sort key picking the most citable version of a paper (lower is better).

    Prefer a published version over a preprint, a real journal over an
    institutional repository copy, and then whichever is better cited.
    """
    return (
        1 if pub["type"] == "preprint" else 0,
        0 if pub["source_type"] == "journal" else 1,
        -pub["citations"],
    )


def collapse_versions(publications):
    """Merge preprint / repository / published records of the same paper.

    OpenAlex indexes each version as its own work, so without this a single
    paper can appear three times. Two records merge only if they have the same
    normalized title *and* share at least one author, which keeps generically
    titled items (e.g. two different "Editorial" entries) apart.
    """
    groups = {}
    order = []
    for pub in publications:
        key = _title_key(pub["title"])
        authors = {a["id"] for a in pub["authors"] if a["id"]}
        # Short titles are too weak a signal to merge on.
        placed = False
        if len(key) >= 20:
            for group in groups.get(key, []):
                if authors & group["authors"]:
                    group["members"].append(pub)
                    group["authors"] |= authors
                    placed = True
                    break
        if not placed:
            group = {"members": [pub], "authors": authors}
            groups.setdefault(key, []).append(group)
            order.append(group)

    merged = []
    for group in order:
        members = group["members"]
        best = min(members, key=_version_rank)
        if len(members) > 1:
            best = dict(best)
            best["faculty"] = sorted({name for m in members for name in m["faculty"]})
            best["citations"] = max(m["citations"] for m in members)
            best["is_oa"] = any(m["is_oa"] for m in members)
            best["has_preprint"] = any(m["type"] == "preprint" for m in members)
        merged.append(best)

    return sorted(merged, key=lambda w: (w["date"], w["title"]), reverse=True)


def collect(
    people,
    mailto,
    since=None,
    keep_preprints=True,
    keep_abstracts=False,
    collapse=True,
    verbose=True,
):
    """Fetch every roster member's works and merge them into one deduped list.

    A paper co-authored by three people in the department appears once, tagged
    with all three names.
    """
    # One flat list of every roster ID, plus a reverse map for attribution.
    owner = {}
    all_ids = []
    for person in people:
        for author_id in person["ids"]:
            owner[author_id] = person["name"]
            all_ids.append(author_id)

    by_work = {}
    per_person_counts = {name: 0 for name in (p["name"] for p in people)}
    errors = []
    requests = [0]

    def count_request():
        requests[0] += 1

    try:
        for work in openalex.works_by_authors(
            all_ids, mailto, from_date=since, on_request=count_request
        ):
            work_type = work.get("type")
            if work_type in NOISE_TYPES:
                continue
            if not keep_abstracts and work_type in ABSTRACT_TYPES:
                continue
            if not keep_preprints and work_type == "preprint":
                continue

            work_id = openalex.short_id(work.get("id"))
            record = by_work.get(work_id)
            if record is None:
                record = normalize(work)
                by_work[work_id] = record

            # A batched query returns the union, so work out who it belongs to
            # by looking for roster IDs among the authors.
            for authorship in work.get("authorships") or []:
                author_id = openalex.short_id((authorship.get("author") or {}).get("id"))
                name = owner.get(author_id)
                if name and name not in record["faculty"]:
                    record["faculty"].append(name)
    except openalex.RateLimitError:
        # Partial results are worse than none here: the page would silently
        # lose whoever came last, so stop and let the caller report it.
        # (Caught before OpenAlexError below, which it subclasses.)
        raise
    except openalex.OpenAlexError as exc:
        errors.append(str(exc))
        if verbose:
            print("  ! {}".format(exc), file=sys.stderr)

    for record in by_work.values():
        for name in record["faculty"]:
            per_person_counts[name] += 1

    # Zero works is ambiguous: a wrong ID and a quiet window look the same in a
    # works query. One bulk author lookup tells them apart, so only pay for it
    # when somebody actually came back empty.
    quiet = [p for p in people if per_person_counts[p["name"]] == 0]
    known = {}
    if quiet:
        try:
            known = openalex.authors_by_ids(
                [i for p in quiet for i in p["ids"]], mailto
            )
            requests[0] += 1
        except openalex.OpenAlexError:
            known = {}

    for person in quiet:
        resolved = [known[i] for i in person["ids"] if i in known]
        if known and not resolved:
            person["_note"] = "no such author ID - check it"
        elif resolved:
            total = max(r["works_count"] for r in resolved)
            person["_note"] = (
                "ID is valid ({} works overall), nothing in this window".format(total)
                if total
                else "ID is valid but has no works at all"
            )

    if verbose:
        print("  {} request(s) to OpenAlex".format(requests[0]))
        for person in people:
            name = person["name"]
            found = per_person_counts[name]
            note = "  <- {}".format(person["_note"]) if person.get("_note") else ""
            print("  {:<24} {:>4} works{}".format(name[:24], found, note))

    publications = sorted(by_work.values(), key=lambda w: (w["date"], w["title"]), reverse=True)
    for pub in publications:
        pub["faculty"].sort()

    if collapse:
        before = len(publications)
        publications = collapse_versions(publications)
        if verbose and before != len(publications):
            print("  merged {} duplicate version(s) of the same paper".format(before - len(publications)))

    return publications, per_person_counts, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--since",
        default=None,
        help="only fetch works published on or after this date (YYYY-MM-DD). "
        "Default: 2 years ago. Use --all for no limit.",
    )
    parser.add_argument("--all", action="store_true", help="fetch every publication, no date limit")
    parser.add_argument("--no-preprints", action="store_true", help="exclude preprints (bioRxiv etc.)")
    parser.add_argument("--keep-abstracts", action="store_true", help="include conference/meeting abstracts")
    parser.add_argument(
        "--no-collapse",
        action="store_true",
        help="list preprint and published versions of a paper separately",
    )
    parser.add_argument(
        "--mailto",
        default=os.environ.get("OPENALEX_MAILTO", ""),
        help="your email; puts you in OpenAlex's faster 'polite pool'",
    )
    parser.add_argument("--roster", default=None, help="path to faculty.csv")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the JSON cache")
    args = parser.parse_args(argv)

    if args.all:
        since = None
    elif args.since:
        since = args.since
    else:
        since = (dt.date.today() - dt.timedelta(days=730)).isoformat()

    try:
        people = load_roster(args.roster) if args.roster else load_roster()
    except RosterError as exc:
        print("Roster problem: {}".format(exc), file=sys.stderr)
        return 1

    print("Fetching {} faculty from OpenAlex{}...".format(
        len(people), " (since {})".format(since) if since else ""
    ))
    try:
        publications, counts, errors = collect(
            people,
            args.mailto,
            since=since,
            keep_preprints=not args.no_preprints,
            keep_abstracts=args.keep_abstracts,
            collapse=not args.no_collapse,
        )
    except openalex.RateLimitError as exc:
        print("\n{}".format(exc), file=sys.stderr)
        print(
            "Nothing was written, so the existing site is untouched. "
            "Re-run after the reset, or see README 'If you hit the rate limit'.",
            file=sys.stderr,
        )
        return 2

    payload = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "since": since,
        "faculty": [p["name"] for p in people],
        # name -> author IDs, so the site can bold departmental authors reliably
        # (by ID, not by fuzzy name matching).
        "roster": {p["name"]: p["ids"] for p in people},
        "counts": counts,
        "errors": errors,
        "publications": publications,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)

    print("\n{} unique publications -> {}".format(len(publications), args.out))
    if openalex.budget.get("remaining") is not None:
        print("OpenAlex budget remaining today: {} of {} requests".format(
            openalex.budget["remaining"], openalex.budget.get("limit") or "?"
        ))
    if errors:
        print("{} lookup(s) failed; see 'errors' in the JSON.".format(len(errors)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
