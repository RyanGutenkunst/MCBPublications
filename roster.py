"""Load the faculty roster from faculty.csv."""

from __future__ import annotations

import csv
import os

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faculty.csv")


class RosterError(RuntimeError):
    pass


def load_roster(path=DEFAULT_PATH):
    """Return [{'name': str, 'ids': [str, ...]}, ...] from the CSV.

    Blank lines and rows whose name starts with '#' are skipped, so you can
    comment out someone on sabbatical without deleting their IDs.
    """
    if not os.path.exists(path):
        raise RosterError("roster not found: {}".format(path))

    people = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "name" not in reader.fieldnames:
            raise RosterError("{} must have a header row with a 'name' column".format(path))
        if "openalex_ids" not in reader.fieldnames:
            raise RosterError("{} must have an 'openalex_ids' column".format(path))

        for lineno, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            if not name or name.startswith("#"):
                continue
            raw_ids = (row.get("openalex_ids") or "").strip()
            # Semicolon-separated because OpenAlex often splits one person
            # across several author records.
            ids = [i.strip().rstrip("/").rsplit("/", 1)[-1] for i in raw_ids.split(";") if i.strip()]
            if not ids:
                raise RosterError("{} line {}: no OpenAlex ID for {!r}".format(path, lineno, name))
            for oid in ids:
                if not (oid.startswith("A") and oid[1:].isdigit()):
                    raise RosterError(
                        "{} line {}: {!r} is not an OpenAlex author ID "
                        "(should look like A5056381387)".format(path, lineno, oid)
                    )
            people.append({"name": name, "ids": ids})

    if not people:
        raise RosterError("no faculty listed in {}".format(path))
    return people
