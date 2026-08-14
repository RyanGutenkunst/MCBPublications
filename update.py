"""Fetch fresh data and rebuild the site in one step.

    python3 update.py                       # last 2 years
    python3 update.py --since 2026-01-01    # since a given date
    python3 update.py --serve               # rebuild, then serve on :8000

Run this before each faculty meeting.
"""

from __future__ import annotations

import argparse
import os
import sys

import build_site
import fetch_pubs

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default=None, help="earliest publication date, YYYY-MM-DD")
    parser.add_argument("--all", action="store_true", help="no date limit")
    parser.add_argument("--no-preprints", action="store_true", help="exclude preprints")
    parser.add_argument("--keep-abstracts", action="store_true", help="include conference/meeting abstracts")
    parser.add_argument("--no-collapse", action="store_true", help="list preprint and published versions separately")
    parser.add_argument("--title", default="MCB Recent Publications", help="page heading")
    parser.add_argument("--api-key", default=os.environ.get("OPENALEX_API_KEY", ""),
                        help="OpenAlex API key; defaults to $OPENALEX_API_KEY")
    parser.add_argument("--mailto", default=os.environ.get("OPENALEX_MAILTO", ""), help="your email")
    parser.add_argument("--serve", action="store_true", help="serve the site locally when done")
    parser.add_argument("--port", type=int, default=8000, help="port for --serve")
    args = parser.parse_args(argv)

    fetch_argv = []
    if args.all:
        fetch_argv.append("--all")
    elif args.since:
        fetch_argv += ["--since", args.since]
    if args.no_preprints:
        fetch_argv.append("--no-preprints")
    if args.keep_abstracts:
        fetch_argv.append("--keep-abstracts")
    if args.no_collapse:
        fetch_argv.append("--no-collapse")
    if args.api_key:
        fetch_argv += ["--api-key", args.api_key]
    if args.mailto:
        fetch_argv += ["--mailto", args.mailto]

    rc = fetch_pubs.main(fetch_argv)
    if rc != 0:
        return rc

    rc = build_site.main(["--title", args.title])
    if rc != 0:
        return rc

    if args.serve:
        import functools
        import http.server
        import socketserver

        root = os.path.join(HERE, "site")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", args.port), handler) as httpd:
            print("\nServing at http://localhost:{}/  (Ctrl-C to stop)".format(args.port))
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
