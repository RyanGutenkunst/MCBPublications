"""Minimal OpenAlex API client (standard library only).

Docs: https://docs.openalex.org/
Sending a mailto puts us in the "polite pool". Since February 2026 OpenAlex
also expects an API key for anything beyond demo use; a free one covers this
tool many times over. See configure() below.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.openalex.org"

# OpenAlex has required an API key for production use since February 2026.
# A free key gives $1 of usage per day (about 10,000 list calls) and is tied to
# the key rather than your IP address, which matters on shared hosts like CI
# runners. Get one in ~30 seconds at https://openalex.org/settings/api
_api_key = ""


def configure(api_key=""):
    """Set the API key sent with every request."""
    global _api_key
    _api_key = (api_key or "").strip()


def has_api_key():
    return bool(_api_key)


def _safe(url):
    """Redact the key so it never reaches a log, an error, or the built page."""
    return re.sub(r"(api_key=)[^&]*", r"\1<redacted>", url)


# Fields we actually render. Requesting only these keeps responses ~10x smaller.
WORK_FIELDS = [
    "id",
    "doi",
    "title",
    "publication_date",
    "publication_year",
    "type",
    "cited_by_count",
    "authorships",
    "primary_location",
    "open_access",
    "biblio",
]


# OpenAlex allows an OR list in a filter. Batching author IDs this way turns
# one request per person into one request per chunk, which matters because the
# free tier has a small daily budget (see RateLimitError).
MAX_IDS_PER_QUERY = 25

# If the API says "come back in more than this many seconds", the daily budget
# is gone and waiting it out in-process is pointless.
MAX_WAIT_SECONDS = 120

# Last seen rate-limit headers, for reporting remaining budget.
budget = {"remaining": None, "limit": None, "remaining_usd": None}


class OpenAlexError(RuntimeError):
    pass


class RateLimitError(OpenAlexError):
    """Daily credit budget exhausted; retrying today will not help."""

    def __init__(self, retry_after, detail=""):
        self.retry_after = retry_after
        minutes = max(1, int(round(retry_after / 60.0)))
        message = (
            "OpenAlex daily request budget is exhausted. It resets at midnight UTC, "
            "in about {} minute{}.".format(minutes, "" if minutes == 1 else "s")
        )
        if not has_api_key():
            # Without a key the budget is tied to the IP address, so on a shared
            # host (a CI runner, a campus NAT) somebody else may have spent it.
            message += (
                " No API key is set. Keyless access is only meant for demos and is "
                "shared with everyone on your IP address. A free key gives $1/day "
                "of its own budget (~10,000 calls): sign up at openalex.org and "
                "copy the key from openalex.org/settings/api, then set "
                "OPENALEX_API_KEY."
            )
        if detail:
            message += " " + detail
        super().__init__(message)


def _record_budget(headers):
    for header, key in (
        ("x-ratelimit-remaining", "remaining"),
        ("x-ratelimit-limit", "limit"),
        ("x-ratelimit-remaining-usd", "remaining_usd"),
    ):
        value = headers.get(header)
        if value is not None:
            budget[key] = value


def _retry_after(exc):
    """Seconds the API wants us to wait, from the header or the JSON body."""
    header = exc.headers.get("retry-after") if exc.headers else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        return float(json.loads(exc.read().decode("utf-8")).get("retryAfter", 0))
    except Exception:
        return 0.0


def _get(path, params, mailto, max_retries=4):
    """GET one page, retrying on transient errors.

    A 429 is *not* treated as transient: OpenAlex's free tier is a daily credit
    budget, and every retry spends another credit for nothing. Once we see one,
    we stop immediately and say when it resets.
    """
    params = dict(params)
    if mailto:
        params["mailto"] = mailto
    if _api_key:
        params["api_key"] = _api_key
    url = "{}/{}?{}".format(API_ROOT, path.lstrip("/"), urllib.parse.urlencode(params))

    delay = 2.0
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url, headers={"User-Agent": "faculty-pub-search (mailto:{})".format(mailto or "unknown")}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                _record_budget(resp.headers)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _record_budget(exc.headers or {})
                wait = _retry_after(exc)
                if wait > MAX_WAIT_SECONDS:
                    raise RateLimitError(wait)
                # A short wait means ordinary throttling, so it is worth pausing.
                if attempt == max_retries:
                    raise RateLimitError(wait or MAX_WAIT_SECONDS)
                time.sleep(max(wait, delay))
                delay *= 2
                continue
            if exc.code not in (500, 502, 503, 504) or attempt == max_retries:
                raise OpenAlexError("HTTP {} for {}".format(exc.code, _safe(url))) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == max_retries:
                raise OpenAlexError("network error for {}: {}".format(_safe(url), exc)) from exc
        time.sleep(delay)
        delay *= 2

    raise OpenAlexError("exhausted retries for {}".format(_safe(url)))


def short_id(openalex_id):
    """'https://openalex.org/A5056381387' -> 'A5056381387'. Idempotent."""
    if not openalex_id:
        return ""
    return openalex_id.rstrip("/").rsplit("/", 1)[-1].strip()


def works_by_authors(author_ids, mailto, from_date=None, pause=0.2, on_request=None):
    """Yield every work by any of author_ids, batching IDs into few requests.

    Asking for 40 authors one at a time costs 40+ credits; OR-ing them into
    chunks costs about one request per chunk per 200 results.
    """
    ids = [short_id(a) for a in author_ids if a]
    for start in range(0, len(ids), MAX_IDS_PER_QUERY):
        chunk = ids[start:start + MAX_IDS_PER_QUERY]
        filters = ["author.id:{}".format("|".join(chunk))]
        if from_date:
            filters.append("from_publication_date:{}".format(from_date))

        cursor = "*"
        while cursor:
            if on_request:
                on_request()
            page = _get(
                "works",
                {
                    "filter": ",".join(filters),
                    "sort": "publication_date:desc",
                    "per-page": 200,
                    "cursor": cursor,
                    "select": ",".join(WORK_FIELDS),
                },
                mailto,
            )
            for work in page.get("results", []):
                yield work
            cursor = page.get("meta", {}).get("next_cursor")
            if cursor:
                time.sleep(pause)


def authors_by_ids(author_ids, mailto):
    """Look up author records in bulk. Returns {id: {...}} for those that exist.

    Used to tell "this ID is wrong" apart from "this person published nothing
    in the requested window", which look identical in a works query.
    """
    found = {}
    ids = [short_id(a) for a in author_ids if a]
    for start in range(0, len(ids), MAX_IDS_PER_QUERY):
        chunk = ids[start:start + MAX_IDS_PER_QUERY]
        page = _get(
            "authors",
            {
                "filter": "openalex_id:{}".format("|".join(chunk)),
                "per-page": MAX_IDS_PER_QUERY,
                "select": "id,display_name,works_count,last_known_institutions",
            },
            mailto,
        )
        for author in page.get("results", []):
            institutions = author.get("last_known_institutions") or []
            found[short_id(author.get("id"))] = {
                "name": author.get("display_name") or "",
                "works_count": author.get("works_count", 0),
                "institution": institutions[0].get("display_name") if institutions else None,
            }
    return found
