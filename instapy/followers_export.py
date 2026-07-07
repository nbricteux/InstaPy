"""
Followers Export Module

Exports followers list using Instagram's internal GraphQL API via the
authenticated browser session. Falls back to dialog scrolling if API fails.
Saves to JSON file and prints them for use in the 'friends' list.
"""

import json
import os
import re
import time

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from .time_util import sleep
from .util import web_address_navigator


def export_followers(browser, username, logger, output_path=None, max_followers=None):
    """
    Export followers list using the internal GraphQL API via the browser session.

    Args:
        browser: Selenium WebDriver instance (must be logged in)
        username: Instagram username whose followers to export
        logger: Logger instance
        output_path: Path to save JSON file (default: ./followers_{username}.json)
        max_followers: Maximum number of followers to collect (None = all)

    Returns:
        list: List of follower usernames
    """
    logger.info("Starting followers export for '{}'...".format(username))

    # Step 1: Get the user ID (needed for GraphQL queries)
    user_id = _get_user_id(browser, username, logger)

    if user_id:
        # Step 2: Use GraphQL API to fetch followers (fast method)
        followers_list = _fetch_followers_via_api(browser, user_id, username, logger, max_followers)
    else:
        logger.warning("- Could not get user ID, falling back to scroll method")
        followers_list = _fetch_followers_via_scroll(browser, username, logger, max_followers)

    # If API method got few results, try scroll method as supplement
    if len(followers_list) < 12 and user_id:
        logger.info("- API returned few results, trying scroll method as supplement...")
        scroll_results = _fetch_followers_via_scroll(browser, username, logger, max_followers)
        # Merge both lists
        all_followers = set(followers_list) | set(scroll_results)
        followers_list = sorted(list(all_followers))

    followers_list = sorted(followers_list)
    logger.info("- Total followers collected: {}".format(len(followers_list)))

    # Save to JSON file
    if output_path is None:
        output_path = "followers_{}.json".format(username)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "username": username,
                "count": len(followers_list),
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "followers": followers_list
            }, f, indent=2, ensure_ascii=False)
        logger.info("- Followers saved to: {}".format(os.path.abspath(output_path)))
    except Exception as e:
        logger.warning("- Failed to save followers JSON: {}".format(str(e)))

    # Print followers for easy copy-paste into friends list
    print("\n" + "=" * 70)
    print("FOLLOWERS LIST FOR '{}' ({} total)".format(username, len(followers_list)))
    print("=" * 70)
    print("\nCopy-paste for 'friends' variable:")
    formatted = "[{}]".format(",".join("'{}'".format(f) for f in followers_list))
    print("friends = {}".format(formatted))
    print("\n" + "=" * 70)

    return followers_list


def _get_user_id(browser, username, logger):
    """Get Instagram user ID from the profile page."""
    user_id = None

    # Navigate to the profile page
    profile_url = "https://www.instagram.com/{}/".format(username)
    web_address_navigator(browser, profile_url)
    sleep(3)

    # Strategy 1: Extract from page source via script
    try:
        user_id = browser.execute_script("""
            // Try multiple locations where IG stores user ID
            try {
                var data = window._sharedData;
                if (data && data.entry_data && data.entry_data.ProfilePage) {
                    return data.entry_data.ProfilePage[0].graphql.user.id;
                }
            } catch(e) {}
            
            try {
                var keys = Object.keys(window.__additionalData || {});
                if (keys.length > 0) {
                    return window.__additionalData[keys[0]].data.user.id;
                }
            } catch(e) {}
            
            return null;
        """)
        if user_id:
            logger.info("- Got user ID from page data: {}".format(user_id))
            return str(user_id)
    except Exception:
        pass

    # Strategy 2: Find user ID in page source HTML
    try:
        page_source = browser.page_source
        # Look for "profilePage_" followed by user ID
        match = re.search(r'"profilePage_(\d+)"', page_source)
        if match:
            user_id = match.group(1)
            logger.info("- Got user ID from page source (profilePage): {}".format(user_id))
            return user_id

        # Look for "user_id":"12345" or "id":"12345"
        match = re.search(r'"user_id"\s*:\s*"(\d+)"', page_source)
        if match:
            user_id = match.group(1)
            logger.info("- Got user ID from page source (user_id): {}".format(user_id))
            return user_id

        match = re.search(r'"id"\s*:\s*"(\d+)".*?"username"\s*:\s*"{}"'.format(re.escape(username)), page_source)
        if match:
            user_id = match.group(1)
            logger.info("- Got user ID from page source (id+username): {}".format(user_id))
            return user_id
    except Exception:
        pass

    # Strategy 3: Use the web profile info API
    try:
        user_id = browser.execute_script("""
            var response = await fetch('/api/v1/users/web_profile_info/?username=""" + username + """', {
                headers: {
                    'x-ig-app-id': '936619743392459'
                }
            });
            var data = await response.json();
            return data.data.user.id;
        """)
        if user_id:
            logger.info("- Got user ID from web_profile_info API: {}".format(user_id))
            return str(user_id)
    except Exception:
        pass

    logger.warning("- Could not determine user ID for '{}'".format(username))
    return None


def _fetch_followers_via_api(browser, user_id, username, logger, max_followers):
    """Fetch followers using Instagram's internal GraphQL API via the browser."""
    followers = []
    has_next = True
    end_cursor = ""
    batch_size = 50
    request_count = 0
    max_requests = 200  # Safety limit

    logger.info("- Fetching followers via GraphQL API (user_id: {})...".format(user_id))

    while has_next and request_count < max_requests:
        request_count += 1

        # Build the GraphQL query - use the followers edge query
        try:
            result = browser.execute_script("""
                var userId = arguments[0];
                var after = arguments[1];
                var first = arguments[2];
                
                var variables = JSON.stringify({
                    id: userId,
                    include_reel: false,
                    fetch_mutual: false,
                    first: first,
                    after: after
                });
                
                // Try the graphql query endpoint
                var url = '/graphql/query/?query_hash=c76146de99bb02f6415203be841dd25a&variables=' + encodeURIComponent(variables);
                
                try {
                    var xhr = new XMLHttpRequest();
                    xhr.open('GET', url, false);  // synchronous
                    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                    xhr.send();
                    
                    if (xhr.status === 200) {
                        return xhr.responseText;
                    } else {
                        return JSON.stringify({error: xhr.status, text: xhr.responseText.substring(0, 200)});
                    }
                } catch(e) {
                    return JSON.stringify({error: e.message});
                }
            """, user_id, end_cursor, batch_size)

            if not result:
                logger.warning("- GraphQL query returned empty response")
                break

            data = json.loads(result)

            # Check for errors
            if "error" in data:
                logger.warning("- GraphQL API error: {}".format(data.get("error")))
                # Try alternative query hash
                if request_count == 1:
                    logger.info("- Trying alternative API approach...")
                    alt_result = _try_alternative_api(browser, user_id, end_cursor, batch_size)
                    if alt_result:
                        data = alt_result
                    else:
                        break
                else:
                    break

            # Parse the response
            edge_data = data.get("data", {}).get("user", {}).get("edge_followed_by", {})
            if not edge_data:
                logger.warning("- Unexpected API response structure")
                break

            edges = edge_data.get("edges", [])
            page_info = edge_data.get("page_info", {})

            for edge in edges:
                node = edge.get("node", {})
                uname = node.get("username")
                if uname:
                    followers.append(uname)

            has_next = page_info.get("has_next_page", False)
            end_cursor = page_info.get("end_cursor", "")

            logger.info("- Fetched batch {}: {} followers (total: {})".format(
                request_count, len(edges), len(followers)))

            if max_followers and len(followers) >= max_followers:
                followers = followers[:max_followers]
                break

            # Rate limiting: small delay to avoid throttling
            if has_next:
                sleep(0.5)

        except json.JSONDecodeError as e:
            logger.warning("- Failed to parse API response: {}".format(str(e)[:100]))
            break
        except Exception as e:
            logger.warning("- API request failed: {}".format(str(e)[:100]))
            break

    logger.info("- API method collected {} followers in {} requests".format(len(followers), request_count))
    return followers


def _try_alternative_api(browser, user_id, after, first):
    """Try alternative GraphQL query hashes that Instagram may use."""
    alt_hashes = [
        "37479f2b8209594dde7facb0d904896a",  # Another known followers hash
        "5aefa9893005572d237da5068082d8d5",  # Yet another variant
    ]

    for query_hash in alt_hashes:
        try:
            result = browser.execute_script("""
                var userId = arguments[0];
                var after = arguments[1];
                var first = arguments[2];
                var queryHash = arguments[3];
                
                var variables = JSON.stringify({
                    id: userId,
                    include_reel: false,
                    fetch_mutual: false,
                    first: first,
                    after: after
                });
                
                var url = '/graphql/query/?query_hash=' + queryHash + '&variables=' + encodeURIComponent(variables);
                
                var xhr = new XMLHttpRequest();
                xhr.open('GET', url, false);
                xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                xhr.send();
                
                if (xhr.status === 200) {
                    return xhr.responseText;
                }
                return null;
            """, user_id, after, first, query_hash)

            if result:
                data = json.loads(result)
                if "data" in data and "user" in data.get("data", {}):
                    return data
        except Exception:
            continue

    return None


def _fetch_followers_via_scroll(browser, username, logger, max_followers):
    """Fallback: fetch followers by scrolling through the dialog/page."""
    logger.info("- Using scroll method to collect followers...")

    followers_url = "https://www.instagram.com/{}/followers/".format(username)
    web_address_navigator(browser, followers_url)
    sleep(5)

    # Find scrollable container
    scroll_container = None
    try:
        browser.find_element(By.XPATH, "//div[@role='dialog']")
        scroll_container = browser.execute_script("""
            var dialog = document.querySelector('div[role="dialog"]');
            if (!dialog) return null;
            var divs = dialog.querySelectorAll('div');
            var best = null;
            var bestHeight = 0;
            for (var i = 0; i < divs.length; i++) {
                var div = divs[i];
                var style = window.getComputedStyle(div);
                var overflowY = style.overflowY;
                if ((overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'hidden') 
                    && div.scrollHeight > div.clientHeight + 10
                    && div.clientHeight > 100) {
                    if (div.scrollHeight > bestHeight) {
                        bestHeight = div.scrollHeight;
                        best = div;
                    }
                }
            }
            return best;
        """)
    except NoSuchElementException:
        pass

    if scroll_container is None:
        scroll_container = browser.execute_script("""
            var main = document.querySelector('main') || document.body;
            var divs = main.querySelectorAll('div');
            var best = null;
            var bestHeight = 0;
            for (var i = 0; i < divs.length; i++) {
                var div = divs[i];
                var style = window.getComputedStyle(div);
                var overflowY = style.overflowY;
                if ((overflowY === 'auto' || overflowY === 'scroll')
                    && div.scrollHeight > div.clientHeight + 10
                    && div.clientHeight > 200) {
                    if (div.scrollHeight > bestHeight) {
                        bestHeight = div.scrollHeight;
                        best = div;
                    }
                }
            }
            return best;
        """)

    use_window_scroll = scroll_container is None
    followers = set()
    last_count = 0
    stale_rounds = 0

    while stale_rounds < 10:
        # Collect usernames from links
        try:
            links = browser.find_elements(By.CSS_SELECTOR, "a[href]")
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    parts = href.rstrip("/").split("/")
                    if not parts:
                        continue
                    candidate = parts[-1]
                    if (
                        candidate
                        and len(candidate) <= 30
                        and candidate.lower() not in _SKIP_PATHS
                        and candidate.lower() != username.lower()
                        and re.match(r'^[a-zA-Z0-9._]+$', candidate)
                    ):
                        followers.add(candidate)
                except Exception:
                    continue
        except Exception:
            pass

        current_count = len(followers)
        if current_count == last_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
            logger.info("- Scroll: collected {} followers...".format(current_count))
        last_count = current_count

        if max_followers and current_count >= max_followers:
            break

        # Scroll
        if use_window_scroll:
            browser.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        else:
            try:
                browser.execute_script("arguments[0].scrollTop += arguments[0].clientHeight * 2;", scroll_container)
            except Exception:
                browser.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        sleep(1.5)

    return sorted(list(followers))


_SKIP_PATHS = {
    "", "p", "explore", "accounts", "direct", "stories", "reels",
    "tags", "locations", "followers", "following", "mutual_followers",
    "about", "privacy", "terms", "help", "api", "press", "jobs",
    "nametag", "session", "emails", "login", "password", "edit",
    "archive", "saved", "close_friends", "discover", "web", "lite",
    "legal", "directory", "static", "two_factor",
}


def export_following(browser, username, logger, output_path=None, max_following=None):
    """
    Export following list (people you follow) using Instagram's internal GraphQL API.

    Args:
        browser: Selenium WebDriver instance (must be logged in)
        username: Instagram username whose following to export
        logger: Logger instance
        output_path: Path to save JSON file (default: ./following_{username}.json)
        max_following: Maximum number to collect (None = all)

    Returns:
        list: List of usernames you follow
    """
    logger.info("Starting following export for '{}'...".format(username))

    # Get user ID
    user_id = None
    try:
        result = browser.execute_script("""
            var username = arguments[0];
            try {
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/api/v1/users/web_profile_info/?username=' + username, false);
                xhr.setRequestHeader('X-IG-App-ID', '936619743392459');
                xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                xhr.send();
                if (xhr.status === 200) {
                    var data = JSON.parse(xhr.responseText);
                    return data.data.user.id;
                }
            } catch(e) {}
            return null;
        """, username)
        if result:
            user_id = str(result)
            logger.info("- Got user ID: {}".format(user_id))
    except Exception:
        pass

    if not user_id:
        logger.error("Could not get user ID for '{}' — cannot retrieve following".format(username))
        return []

    # Fetch following via GraphQL API (query hash for following)
    following = []
    has_next = True
    end_cursor = ""
    batch_size = 50
    request_count = 0

    logger.info("- Fetching following list via API...")

    while has_next:
        request_count += 1

        try:
            result = browser.execute_script("""
                var userId = arguments[0];
                var after = arguments[1];
                var first = arguments[2];

                var variables = JSON.stringify({
                    id: userId,
                    include_reel: false,
                    fetch_mutual: false,
                    first: first,
                    after: after
                });

                var url = '/graphql/query/?query_hash=d04b0a864b4b54837c0d870b0e77e076&variables=' + encodeURIComponent(variables);

                try {
                    var xhr = new XMLHttpRequest();
                    xhr.open('GET', url, false);
                    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                    xhr.send();
                    if (xhr.status === 200) {
                        return xhr.responseText;
                    }
                } catch(e) {}
                return null;
            """, user_id, end_cursor, batch_size)

            if not result:
                logger.warning("- GraphQL following query returned empty response")
                break

            data = json.loads(result)

            if "error" in data or "data" not in data:
                logger.warning("- GraphQL API error for following")
                break

            edge_data = data.get("data", {}).get("user", {}).get("edge_follow", {})
            if not edge_data:
                logger.warning("- Unexpected API response structure for following")
                break

            edges = edge_data.get("edges", [])
            page_info = edge_data.get("page_info", {})

            for edge in edges:
                node = edge.get("node", {})
                uname = node.get("username")
                if uname:
                    following.append(uname)

            has_next = page_info.get("has_next_page", False)
            end_cursor = page_info.get("end_cursor", "")

            if request_count % 5 == 0:
                logger.info("- Fetched {} following so far...".format(len(following)))

            if max_following and len(following) >= max_following:
                following = following[:max_following]
                break

            if has_next:
                sleep(0.5)

        except Exception as e:
            logger.error("- Error fetching following: {}".format(str(e)))
            break

    following_list = sorted(following)
    logger.info("- Total following collected: {}".format(len(following_list)))

    # Save to JSON file
    if output_path is None:
        output_path = "following_{}.json".format(username)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "username": username,
                "count": len(following_list),
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "following": following_list
            }, f, indent=2, ensure_ascii=False)
        logger.info("- Following saved to: {}".format(os.path.abspath(output_path)))
    except Exception as e:
        logger.warning("- Failed to save following JSON: {}".format(str(e)))

    # Print for easy copy-paste
    print("\n" + "=" * 70)
    print("FOLLOWING LIST FOR '{}' ({} total)".format(username, len(following_list)))
    print("=" * 70)
    print("\nCopy-paste for 'friends' variable:")
    formatted = "[{}]".format(",".join("'{}'".format(f) for f in following_list))
    print("friends = {}".format(formatted))
    print("\n" + "=" * 70)

    return following_list
