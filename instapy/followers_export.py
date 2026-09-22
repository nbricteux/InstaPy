"""
Followers / Following Export Module

Exports the followers and following lists using Instagram's current internal
friendships API via the authenticated browser session (see ``ig_api``).
Saves to a JSON file and prints the result for use in the 'friends' list.
"""

import json
import os
import time

from .ig_api import fetch_relationship_list, get_user_id, is_list_complete
from .util import get_relationship_counts


def _save_and_report(kind, username, usernames, output_path, logger):
    """Persist the exported list to JSON and print it for copy-paste."""
    if output_path is None:
        output_path = "{}_{}.json".format(kind, username)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "username": username,
                    "count": len(usernames),
                    "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    kind: usernames,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        logger.info(
            "- {} saved to: {}".format(kind.capitalize(), os.path.abspath(output_path))
        )
    except Exception as e:
        logger.warning("- Failed to save {} JSON: {}".format(kind, str(e)))

    print("\n" + "=" * 70)
    print(
        "{} LIST FOR '{}' ({} total)".format(kind.upper(), username, len(usernames))
    )
    print("=" * 70)
    print("\nCopy-paste for 'friends' variable:")
    formatted = "[{}]".format(",".join("'{}'".format(f) for f in usernames))
    print("friends = {}".format(formatted))
    print("\n" + "=" * 70)


def _export(browser, kind, username, logger, output_path, max_amount):
    logger.info("Starting {} export for '{}'...".format(kind, username))

    user_id = get_user_id(browser, username, logger)
    if not user_id:
        logger.error(
            "Could not get user ID for '{}' - cannot retrieve {}".format(username, kind)
        )
        return []

    usernames = sorted(
        fetch_relationship_list(browser, user_id, kind, logger, max_amount=max_amount)
    )
    logger.info("- Total {} collected: {}".format(kind, len(usernames)))

    # Never replace a previous export with a truncated one (not applicable when
    # the caller deliberately capped the amount).
    if not max_amount:
        followers_count, following_count = get_relationship_counts(
            browser, username, logger
        )
        expected = followers_count if kind == "followers" else following_count
        if not is_list_complete(usernames, expected):
            logger.error(
                "- Only got {} of {} {} - export is incomplete, NOT saving it "
                "(existing file left untouched)".format(len(usernames), expected, kind)
            )
            return usernames

    _save_and_report(kind, username, usernames, output_path, logger)
    return usernames


def export_followers(browser, username, logger, output_path=None, max_followers=None):
    """
    Export followers list using Instagram's internal API via the browser session.

    Args:
        browser: Selenium WebDriver instance (must be logged in)
        username: Instagram username whose followers to export
        logger: Logger instance
        output_path: Path to save JSON file (default: ./followers_{username}.json)
        max_followers: Maximum number of followers to collect (None = all)

    Returns:
        list: List of follower usernames
    """
    return _export(browser, "followers", username, logger, output_path, max_followers)


def export_following(browser, username, logger, output_path=None, max_following=None):
    """
    Export following list (people you follow) using Instagram's internal API.

    Args:
        browser: Selenium WebDriver instance (must be logged in)
        username: Instagram username whose following to export
        logger: Logger instance
        output_path: Path to save JSON file (default: ./following_{username}.json)
        max_following: Maximum number to collect (None = all)

    Returns:
        list: List of usernames you follow
    """
    return _export(browser, "following", username, logger, output_path, max_following)
