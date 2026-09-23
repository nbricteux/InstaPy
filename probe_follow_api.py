"""
One-off measurement tool: which request shape returns the most followers per
request?

Instagram decides the page size server-side and ignores `count` when it feels
like it (the live runs average ~14 users per request while asking for 50), and
no public documentation says what actually raises it. So measure it: each
variant below is asked for 3 consecutive pages and the users-per-request is
reported. Roughly 20 requests in total.

Usage (PowerShell):
    $env:INSTA_USERNAME="nbricteux"; $env:INSTA_PASSWORD="..."
    python probe_follow_api.py

Nothing here writes to Instagram - it only reads follower pages.
"""

import json
import os
import sys
import time
from urllib.parse import quote

from instapy import InstaPy, smart_run
from instapy.ig_api import api_get, get_user_id

PAGES_PER_VARIANT = 3

VARIANTS = [
    ("current: count=50 + search_surface", "count=50&search_surface=follow_list_page"),
    ("count=50, no search_surface", "count=50"),
    ("count=100", "count=100"),
    ("count=200", "count=200"),
    ("count=200 + search_surface", "count=200&search_surface=follow_list_page"),
    ("count=12 (what the web UI sends)", "count=12"),
]


def probe_rest(browser, user_id, label, query, logger):
    """Walk PAGES_PER_VARIANT pages of one request shape and report page sizes."""
    sizes = []
    statuses = []
    max_id = None

    for _ in range(PAGES_PER_VARIANT):
        path = "/api/v1/friendships/{}/followers/?{}".format(user_id, query)
        if max_id:
            path += "&max_id={}".format(quote(str(max_id), safe=""))

        status, payload = api_get(browser, path)
        statuses.append(status)

        if not payload or payload.get("users") is None:
            sizes.append(0)
            break

        sizes.append(len(payload["users"]))
        max_id = payload.get("next_max_id")
        if not max_id:
            break
        time.sleep(1)

    total = sum(sizes)
    per_request = total / float(len(sizes) or 1)
    print(
        "{:<42} statuses={} pages={} -> {:.1f} users/request".format(
            label, statuses, sizes, per_request
        )
    )
    return per_request


def probe_graphql(browser, user_id, logger):
    """Experimental: the private GraphQL follow-list surface instagrapi moved to.

    Needs the page's fb_dtsg token. Prints whatever comes back so the response
    shape (or the error) can be read directly.
    """
    token = browser.execute_script(
        """
        var m = document.documentElement.innerHTML.match(/"dtsg":\\s*\\{"token":"([^"]+)"/)
             || document.documentElement.innerHTML.match(/"DTSGInitialData",\\[\\],\\{"token":"([^"]+)"/);
        return m ? m[1] : null;
        """
    )
    if not token:
        print("graphql: no fb_dtsg token found in the page, skipped")
        return

    for doc_id, variables in (
        (
            "284797047911918316998205836755",  # FollowersList (instagrapi PR #2801)
            {"id": str(user_id), "first": 50},
        ),
        (
            "284797047911918316998205836755",
            {"data": {"count": 50, "search_surface": "follow_list_page"},
             "userID": str(user_id)},
        ),
    ):
        result = browser.execute_script(
            """
            var docId = arguments[0], vars = arguments[1], token = arguments[2];
            var body = 'doc_id=' + encodeURIComponent(docId)
                     + '&variables=' + encodeURIComponent(vars)
                     + '&fb_dtsg=' + encodeURIComponent(token)
                     + '&server_timestamps=true';
            var xhr = new XMLHttpRequest();
            xhr.open('POST', '/graphql/query', false);
            xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
            xhr.setRequestHeader('X-IG-App-ID', '936619743392459');
            xhr.send(body);
            return JSON.stringify({status: xhr.status, body: xhr.responseText.substring(0, 400)});
            """,
            doc_id,
            json.dumps(variables),
            token,
        )
        print("graphql doc_id={} vars={}".format(doc_id, json.dumps(variables)[:60]))
        print("   ->", result)
        time.sleep(1)


def main():
    username = os.environ.get("INSTA_USERNAME")
    password = os.environ.get("INSTA_PASSWORD")
    if not username or not password:
        sys.exit("Set INSTA_USERNAME and INSTA_PASSWORD first.")

    session = InstaPy(
        username=username,
        password=password,
        headless_browser=False,
        want_check_browser=False,
        browser_choice="chrome",
    )

    with smart_run(session):
        browser, logger = session.browser, session.logger
        user_id = get_user_id(browser, username, logger)
        if not user_id:
            sys.exit("Could not resolve the user ID - is the session logged in?")

        print("\nuser_id: {}\n{}".format(user_id, "-" * 78))
        results = {}
        for label, query in VARIANTS:
            try:
                results[label] = probe_rest(browser, user_id, label, query, logger)
            except Exception as exc:
                print("{:<42} FAILED: {}".format(label, exc))
            time.sleep(2)

        print("-" * 78)
        probe_graphql(browser, user_id, logger)

        if results:
            best = max(results, key=results.get)
            print("\nBest: {} at {:.1f} users/request".format(best, results[best]))
            print("Current code averages ~14 users/request in real runs.")


if __name__ == "__main__":
    main()
