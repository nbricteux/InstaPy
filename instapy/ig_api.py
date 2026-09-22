"""
Instagram web private-API helpers.

Instagram retired the legacy ``/graphql/query/?query_hash=...`` endpoints that
exposed ``edge_followed_by`` / ``edge_follow`` pagination: those requests now
answer 200 with an empty payload, which is why the old code silently collected
zero users. The endpoints the web app itself uses today are:

    GET /api/v1/friendships/{user_id}/followers/?count=50&max_id=<cursor>
    GET /api/v1/friendships/{user_id}/following/?count=50&max_id=<cursor>

They are called from the logged-in browser session (same-origin XHR, so the
session cookies ride along) and return::

    {"users": [{"pk": ..., "username": ...}, ...], "next_max_id": "...",
     "status": "ok"}
"""

import json
import random
import re
from urllib.parse import quote

from .time_util import sleep
from .util import web_address_navigator

IG_APP_ID = "936619743392459"
IG_ASBD_ID = "129477"

# Synchronous same-origin XHR that returns the raw status alongside the body so
# the caller can tell "throttled" and "logged out" apart from "bad payload".
_XHR_SCRIPT = """
var url = arguments[0];
try {{
    var csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
    var xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
    xhr.setRequestHeader('X-IG-App-ID', '{app_id}');
    xhr.setRequestHeader('X-ASBD-ID', '{asbd_id}');
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    if (csrf) {{ xhr.setRequestHeader('X-CSRFToken', csrf); }}
    xhr.send();
    return JSON.stringify({{status: xhr.status, body: xhr.responseText}});
}} catch (e) {{
    return JSON.stringify({{status: -1, body: String(e)}});
}}
""".format(app_id=IG_APP_ID, asbd_id=IG_ASBD_ID)


def ensure_instagram_origin(browser, username=None):
    """Relative XHRs only work from an instagram.com page - make sure we're on one."""
    try:
        current_url = browser.current_url or ""
    except Exception:
        current_url = ""

    if "instagram.com" in current_url:
        return

    target = (
        "https://www.instagram.com/{}/".format(username)
        if username
        else "https://www.instagram.com/"
    )
    web_address_navigator(browser, target)
    sleep(2)


def api_get(browser, path):
    """Perform a same-origin GET against Instagram's private API.

    Returns a ``(status, payload)`` tuple where ``payload`` is the decoded JSON
    body, or ``None`` when the body was not JSON.
    """
    try:
        raw = browser.execute_script(_XHR_SCRIPT, path)
    except Exception as exc:
        return -1, {"error": str(exc)}

    if not raw:
        return -1, None

    try:
        envelope = json.loads(raw)
    except ValueError:
        return -1, None

    status = envelope.get("status", -1)
    body = envelope.get("body") or ""

    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def get_user_id(browser, username, logger):
    """Resolve a username to its numeric Instagram user ID."""
    ensure_instagram_origin(browser, username)

    status, payload = api_get(
        browser, "/api/v1/users/web_profile_info/?username={}".format(username)
    )
    if payload:
        user_id = (payload.get("data") or {}).get("user", {}).get("id")
        if user_id:
            logger.info("- Got user ID from web_profile_info API: {}".format(user_id))
            return str(user_id)

    if status == 401:
        logger.warning("- web_profile_info returned 401 (session not authenticated?)")
    elif status == 429:
        logger.warning("- web_profile_info returned 429 (rate limited)")

    # Fallback: dig the ID out of the profile page markup.
    web_address_navigator(browser, "https://www.instagram.com/{}/".format(username))
    sleep(3)

    try:
        page_source = browser.page_source
    except Exception:
        page_source = ""

    for pattern, label in (
        (r'"profilePage_(\d+)"', "profilePage"),
        (r'"user_id"\s*:\s*"(\d+)"', "user_id"),
        (
            r'"id"\s*:\s*"(\d+)"[^}]*?"username"\s*:\s*"' + re.escape(username) + '"',
            "id+username",
        ),
    ):
        match = re.search(pattern, page_source)
        if match:
            user_id = match.group(1)
            logger.info(
                "- Got user ID from page source ({}): {}".format(label, user_id)
            )
            return user_id

    logger.warning("- Could not determine user ID for '{}'".format(username))
    return None


def fetch_relationship_list(
    browser,
    user_id,
    rel_type,
    logger,
    max_amount=None,
    batch_size=50,
    progress_every=5,
):
    """Page through the friendships API and return a list of usernames.

    Args:
        browser: logged-in Selenium WebDriver
        user_id: numeric Instagram user ID
        rel_type: "followers" or "following"
        logger: logger instance
        max_amount: stop once this many usernames are collected (None = all)
        batch_size: users requested per page (Instagram caps this around 50)
        progress_every: log a progress line every N requests

    Returns:
        list: usernames, in the order Instagram returned them
    """
    if rel_type not in ("followers", "following"):
        raise ValueError("rel_type must be 'followers' or 'following'")

    ensure_instagram_origin(browser)

    collected = []
    seen = set()
    max_id = None
    request_count = 0
    throttle_retries = 0
    stale_pages = 0
    empty_pages = 0
    # Safety net only. Instagram often returns 10-25 users per page whatever
    # `count` asks for, so 2000 pages still covers ~20k users.
    max_requests = 2000

    logger.info(
        "- Fetching {} via the friendships API (user_id: {})...".format(
            rel_type, user_id
        )
    )

    while request_count < max_requests:
        request_count += 1

        path = "/api/v1/friendships/{}/{}/?count={}".format(
            user_id, rel_type, batch_size
        )
        if rel_type == "followers":
            path += "&search_surface=follow_list_page"
        if max_id:
            # Follower cursors are base64-like ("QVFE...+/=="); unencoded, the
            # '+' turns into a space and Instagram silently restarts the list.
            path += "&max_id={}".format(quote(str(max_id), safe=""))

        status, payload = api_get(browser, path)

        if status == 429 or (status == 400 and payload is None):
            if throttle_retries < 3:
                throttle_retries += 1
                backoff = 60 * throttle_retries
                logger.warning(
                    "- Rate limited (HTTP {}), backing off {}s (retry {}/3)...".format(
                        status, backoff, throttle_retries
                    )
                )
                sleep(backoff, custom_percentage=1)
                request_count -= 1
                continue
            logger.warning("- Still rate limited after 3 retries, stopping early")
            break

        if status in (401, 403):
            logger.error(
                "- Instagram refused the {} request (HTTP {}). The session is not "
                "authenticated or the profile is not visible to you.".format(
                    rel_type, status
                )
            )
            break

        if payload is None:
            logger.warning(
                "- {} request returned a non-JSON response (HTTP {})".format(
                    rel_type, status
                )
            )
            break

        users = payload.get("users")
        if users is None:
            logger.warning(
                "- Unexpected API response for {}: {}".format(
                    rel_type, str(payload)[:200]
                )
            )
            break

        before = len(collected)
        for user in users:
            username = user.get("username")
            if username and username not in seen:
                seen.add(username)
                collected.append(username)
        new_users = len(collected) - before

        throttle_retries = 0
        max_id = payload.get("next_max_id")

        (logger.info if request_count <= 3 else logger.debug)(
            "- {} page {}: {} returned, {} new, next_max_id={!r}".format(
                rel_type, request_count, len(users), new_users, max_id
            )
        )

        if max_amount and len(collected) >= max_amount:
            collected = collected[:max_amount]
            break

        if not max_id:
            logger.info(
                "- No next_max_id after page {} ({} returned), end of list. "
                "Response fields: {}".format(
                    request_count,
                    len(users),
                    {k: v for k, v in payload.items() if k != "users"},
                )
            )
            break

        if not users:
            # Instagram filters hidden/restricted accounts out of each page after
            # building it, so a page can come back empty while the cursor still
            # points further into the list. Only the cursor means "the end".
            empty_pages += 1
            if empty_pages >= 30:
                logger.warning(
                    "- {} empty pages in a row with a cursor still present, "
                    "stopping. Response fields: {}".format(
                        empty_pages, {k: v for k, v in payload.items() if k != "users"}
                    )
                )
                break
        elif new_users == 0:
            empty_pages = 0
            stale_pages += 1
            if stale_pages >= 3:
                logger.warning(
                    "- {} pages in a row returned only already-seen {}; Instagram "
                    "is repeating the list, stopping".format(stale_pages, rel_type)
                )
                break
        else:
            empty_pages = 0
            stale_pages = 0

        if request_count % progress_every == 0:
            logger.info("- Fetched {} {} so far...".format(len(collected), rel_type))

        # Short pause between pages; a 429 is handled by the back-off above.
        sleep(random.uniform(0.4, 0.9), custom_percentage=1)

    logger.info(
        "- API collected {} {} in {} requests".format(
            len(collected), rel_type, request_count
        )
    )
    return collected


def is_list_complete(collected, expected_count, tolerance=0.9):
    """True when ``collected`` plausibly covers the profile's reported count.

    Instagram's counters include deactivated/restricted accounts the list
    endpoint never returns, so a few percent short is normal. An unknown
    expected count is treated as complete.
    """
    if not expected_count:
        return True
    return len(collected) >= expected_count * tolerance
