# import built-in & third-party modules
import glob
import json
import os
import random
import time
from datetime import datetime

# import exceptions
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By

# import InstaPy modules
from .follow_util import get_following_status
from .time_util import sleep
from .util import (
    get_relationship_counts,
    interruption_handler,
    is_private_profile,
    progress_tracker,
    truncate_float,
    web_address_navigator,
)


def get_followers(
    browser,
    self_username,
    username,
    grab,
    relationship_data,
    live_match,
    store_locally,
    logger,
    logfolder,
    verified_only=False,
):
    """Get entire list of followers using the internal API via XHR."""

    all_followers = []

    if username not in relationship_data:
        relationship_data.update({username: {"all_following": [], "all_followers": []}})

    grab_info = (
        'at "full" range' if grab == "full" else "at the range of {}".format(grab)
    )
    tense = (
        "live"
        if (live_match is True or not relationship_data[username]["all_followers"])
        else "fresh"
    )

    logger.info(
        "Retrieving {} `Followers` data of {} {}".format(tense, username, grab_info)
    )

    # Get followers count
    followers_count, _ = get_relationship_counts(browser, username, logger)

    if followers_count is None:
        followers_count = 0

    if grab != "full" and isinstance(grab, int) and grab > followers_count:
        logger.info(
            "You have requested higher amount than existing followers count "
            " ~gonna grab all available"
        )
        grab = followers_count

    # Get user ID via API
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
    except Exception:
        pass

    if not user_id:
        logger.error("Could not get user ID for '{}' — cannot retrieve followers".format(username))
        return all_followers

    # Fetch followers via GraphQL API using XHR from the browser
    has_next = True
    end_cursor = ""
    batch_size = 50
    request_count = 0
    max_amount = followers_count if grab == "full" else grab

    logger.info("- Fetching followers via API (user_id: {}, target: {})...".format(user_id, max_amount))

    while has_next and len(all_followers) < max_amount:
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
                
                var url = '/graphql/query/?query_hash=c76146de99bb02f6415203be841dd25a&variables=' + encodeURIComponent(variables);
                
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
                logger.warning("- GraphQL followers query returned empty response")
                break

            data = json.loads(result)

            if "error" in data or "data" not in data:
                logger.warning("- GraphQL API error or unexpected response")
                break

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
                    all_followers.append(uname)

            has_next = page_info.get("has_next_page", False)
            end_cursor = page_info.get("end_cursor", "")

            if request_count % 10 == 0:
                logger.info("- Fetched {} followers so far...".format(len(all_followers)))

            # Rate limiting
            if has_next:
                sleep(0.5)

        except Exception as e:
            logger.error("Sorry, an error occurred: {}".format(str(e)))
            break

    logger.info("- Total followers retrieved: {}".format(len(all_followers)))

    # Update relationship data
    relationship_data[username]["all_followers"] = all_followers

    # Store locally if requested
    if store_locally and all_followers:
        try:
            store_path = "{}followers_{}.json".format(logfolder, username)
            with open(store_path, "w") as f:
                json.dump(all_followers, f)
        except Exception:
            pass

    return all_followers


def get_following(
    browser,
    self_username,
    username,
    grab,
    relationship_data,
    live_match,
    store_locally,
    logger,
    logfolder,
):
    """Get entire list of following using graphql queries."""

    # Variables
    user_data = {}
    variables = {}

    if username not in relationship_data:
        relationship_data.update({username: {"all_following": [], "all_followers": []}})

    grab_info = (
        'at "full" range' if grab == "full" else "at the range of {}".format(grab)
    )
    tense = (
        "live"
        if (live_match is True or not relationship_data[username]["all_following"])
        else "fresh"
    )

    logger.info(
        "Retrieving {} `Following` data of {} {}".format(tense, username, grab_info)
    )

    user_link = "https://www.instagram.com/{}/".format(username)
    web_address_navigator(browser, user_link)

    # Get following count
    _, following_count = get_relationship_counts(browser, username, logger)

    if grab != "full" and grab > following_count:
        logger.info(
            "You have requested higher amount than existing following count "
            " ~gonna grab all available"
        )
        grab = following_count

    # Check if user's account is private and we don't follow
    following_status, _ = get_following_status(
        browser, "profile", self_username, username, None, logger, logfolder
    )

    is_private = is_private_profile(browser, logger, following_status == "Following")
    if not username == self_username and (
        is_private is None
        or (is_private is True and following_status not in ["Following", True])
        or (following_status == "Blocked")
    ):
        logger.info(
            "This user is private and we are not following. '{}':'{}'".format(
                is_private, following_status
            )
        )
        return False

    # sets the amount of usernames to be matched in the next queries
    match = (
        None
        if live_match is True
        else 10
        if relationship_data[username]["all_following"]
        else None
    )

    # if there has been prior graphql query, use that existing data to speed
    # up querying time
    all_prior_following = (
        relationship_data[username]["all_following"] if match is not None else None
    )

    # FIXME: use util.py:get_query_hash to get the hash code
    graphql_endpoint = "view-source:https://www.instagram.com/graphql/query/"

    graphql_following = (
        graphql_endpoint + "?query_hash=58712303d941c6855d4e888c5f0cd22f"
    )

    try:
        user_data["id"] = browser.execute_script(
            "return window.__additionalData[Object.keys(window.__additionalData)[0]].data."
            "graphql.user.id"
        )
    except WebDriverException:
        user_data["id"] = browser.execute_script(
            "return window._sharedData.entry_data.ProfilePage[0].graphql.user.id"
        )

    variables["id"] = user_data["id"]
    variables["first"] = 50

    # get follower and user loop

    sc_rolled = 0
    grab_notifier = False
    local_read_failure = False
    passed_time = "time loop"
    all_following = []

    try:
        filename = None
        query_date = None
        graphql_queries = None
        has_next_data = True

        url = "{}&variables={}".format(graphql_following, str(json.dumps(variables)))
        web_address_navigator(browser, url)

        # Get stored graphql queries data to be used
        try:
            filename = "{}graphql_queries.json".format(logfolder)
            query_date = datetime.today().strftime("%d-%m-%Y")
            if not os.path.isfile(filename):
                with interruption_handler():
                    with open(filename, "w") as graphql_queries_file:
                        json.dump(
                            {username: {query_date: {"sc_rolled": 0}}},
                            graphql_queries_file,
                        )
                        graphql_queries_file.close()

            # Loads the existing graphql queries data
            with open(filename) as graphql_queries_file:
                graphql_queries = json.load(graphql_queries_file)
                stored_usernames = list(name for name, date in graphql_queries.items())
                if username not in stored_usernames:
                    graphql_queries[username] = {query_date: {"sc_rolled": 0}}
                stored_query_dates = list(
                    date for date, score in graphql_queries[username].items()
                )
                if query_date not in stored_query_dates:
                    graphql_queries[username][query_date] = {"sc_rolled": 0}
        except Exception as exc:
            logger.info(
                "Error occurred while getting `scroll` data from "
                "graphql_queries.json\n{}\n".format(str(exc).encode("utf-8"))
            )
            local_read_failure = True

        start_time = time.time()
        highest_value = following_count if grab == "full" else grab
        # fetch all user while still has data
        while has_next_data:
            try:
                pre = browser.find_element(By.TAG_NAME, "pre").text
            except NoSuchElementException as exc:
                logger.info(
                    "Encountered an error to find `pre` in page!"
                    "\t~grabbed {} usernames \n\t{}".format(
                        len(set(all_following)), str(exc).encode("utf-8")
                    )
                )
                return all_following

            data = json.loads(pre)["data"]

            # get following
            page_info = data["user"]["edge_follow"]["page_info"]
            edges = data["user"]["edge_follow"]["edges"]
            for user in edges:
                all_following.append(user["node"]["username"])

            grabbed = len(set(all_following))

            # write & update records at Progress Tracker
            progress_tracker(grabbed, highest_value, start_time, logger)
            print("\n")

            finish_time = time.time()
            diff_time = finish_time - start_time
            diff_n, diff_s = (
                (diff_time / 60 / 60, "hours")
                if diff_time / 60 / 60 >= 1
                else (diff_time / 60, "minutes")
                if diff_time / 60 >= 1
                else (diff_time, "seconds")
            )
            diff_n = truncate_float(diff_n, 2)
            passed_time = "{} {}".format(diff_n, diff_s)

            if match is not None:
                matched_following = len(set(all_following)) - len(
                    set(all_following) - set(all_prior_following)
                )
                if matched_following >= match:
                    new_following = set(all_following) - set(all_prior_following)
                    all_following = all_following + all_prior_following
                    logger.info(
                        "Grabbed {} new usernames from `Following` in {}  "
                        "~total of {} usernames".format(
                            len(set(new_following)),
                            passed_time,
                            len(set(all_following)),
                        )
                    )
                    grab_notifier = True
                    break

            if grab != "full" and grabbed >= grab:
                logger.info(
                    "Grabbed {} usernames from `Following` as requested at {}".format(
                        grabbed, passed_time
                    )
                )
                grab_notifier = True
                break

            has_next_data = page_info["has_next_page"]
            if has_next_data:
                variables["after"] = page_info["end_cursor"]

                url = "{}&variables={}".format(
                    graphql_following, str(json.dumps(variables))
                )

                web_address_navigator(browser, url)
                sc_rolled += 1

                # dumps the current graphql queries data
                if local_read_failure is not True:
                    try:
                        with interruption_handler():
                            with open(filename, "w") as graphql_queries_file:
                                graphql_queries[username][query_date]["sc_rolled"] += 1
                                json.dump(graphql_queries, graphql_queries_file)
                    except Exception as exc:
                        logger.info(
                            "Error occurred while writing `scroll` data to "
                            "graphql_queries.json\n{}\n".format(
                                str(exc).encode("utf-8")
                            )
                        )

                # take breaks gradually
                if sc_rolled > 91:
                    logger.info("Queried too much! ~ sleeping a bit :>")
                    sleep(600)
                    sc_rolled = 0

    except BaseException as exc:
        logger.info(
            "Unable to get `Following` data:\n\t{}\n".format(str(exc).encode("utf-8"))
        )

    # remove possible duplicates
    all_following = sorted(set(all_following), key=lambda x: all_following.index(x))

    if grab_notifier is False:
        logger.info(
            "Grabbed {} usernames from `Following` in {}".format(
                len(all_following), passed_time
            )
        )

    if len(all_following) > 0:
        if (
            store_locally is True
            and relationship_data[username]["all_following"] != all_following
        ):
            store_following_data(username, grab, all_following, logger, logfolder)
        elif store_locally is True:
            logger.info(
                "The `Following` data is identical with the data in previous "
                "query  ~not storing the file again"
            )

        if grab == "full":
            relationship_data[username].update({"all_following": all_following})

    sleep_t = sc_rolled * 6
    sleep_t = sleep_t if sleep_t < 600 else random.randint(585, 655)
    sleep_n, sleep_s = (
        (sleep_t / 60, "minutes") if sleep_t / 60 >= 1 else (sleep_t, "seconds")
    )
    sleep_n = truncate_float(sleep_n, 4)

    logger.info(
        "Zz :[ time to take a good nap  ~sleeping {} {}".format(sleep_n, sleep_s)
    )
    sleep(sleep_t)
    logger.info("Yawn :] let's go!\n")

    return all_following


def get_unfollowers(
    browser,
    self_username,
    username,
    compare_by,
    compare_track,
    relationship_data,
    live_match,
    store_locally,
    print_out,
    logger,
    logfolder,
):
    if compare_by not in ["latest", "day", "month", "year", "earliest"]:
        logger.info(
            "Please choose a valid compare point to pick Unfollowers  "
            "~leaving out of an invalid value"
        )
        return [], []

    elif compare_track not in ["first", "median", "last"]:
        logger.info(
            "Please choose a valid compare track to pick Unfollowers  "
            "~leaving out of an invalid value"
        )
        return [], []

    elif username is None or not isinstance(username, str):
        logger.info(
            "Please enter a username to pick Unfollowers  ~leaving out of an "
            "invalid value"
        )
        return [], []

    prior_followers, selected_filename = load_followers_data(
        username, compare_by, compare_track, logger, logfolder
    )

    if not prior_followers and selected_filename is None:
        logger.info(
            "Generate `Followers` data to find Unfollowers in future!  "
            "~couldn't pick Unfollowers"
        )
        return [], []

    current_followers = get_followers(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # if current_followers is False we have targeted a private account that we don't follow
    if not current_followers:
        return False, False

    all_unfollowers = [
        follower for follower in prior_followers if follower not in current_followers
    ]

    if len(all_unfollowers) > 0:
        current_following = get_following(
            browser,
            self_username,
            username,
            "full",
            relationship_data,
            live_match,
            store_locally,
            logger,
            logfolder,
        )

        active_unfollowers = [
            unfollower
            for unfollower in current_following
            if unfollower in all_unfollowers
        ]

        logger.info(
            "Unfollowers found from {}!  total: {}  |  active: {}  "
            ":|\n".format(
                selected_filename, len(all_unfollowers), len(active_unfollowers)
            )
        )
        if store_locally is True:
            # store all Unfollowers in a local file
            store_all_unfollowers(username, all_unfollowers, logger, logfolder)
            # store active Unfollowers in a local file
            store_active_unfollowers(username, active_unfollowers, logger, logfolder)

        if print_out is True:
            logger.info(
                "Unfollowers of {}:\n\n\tAll Unfollowers: {}\n\n\tActive "
                "Unfollowers: {}\n".format(
                    username, all_unfollowers, active_unfollowers
                )
            )
    else:
        logger.info(
            "Yay! You have no any Unfollowers from {}!  ^v^".format(selected_filename)
        )
        return [], []

    return all_unfollowers, active_unfollowers


def get_nonfollowers(
    browser,
    self_username,
    username,
    relationship_data,
    live_match,
    store_locally,
    logger,
    logfolder,
):
    """Finds Nonfollowers of a given user"""

    if username is None or not isinstance(username, str):
        logger.info(
            "Please enter a username to pick Nonfollowers  ~leaving out of "
            "an invalid value"
        )
        return []

    # get `Followers` data
    all_followers = get_followers(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # if all_followers is False we have targeted a private account that we don't follow
    if not all_followers:
        return False

    # get `Following` data
    all_following = get_following(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # using this approach we can preserve the order of elements to be used
    # with `FIFO`, `LIFO` or `RANDOM` styles
    nonfollowers = [user for user in all_following if user not in all_followers]

    # uniqify elements
    nonfollowers = sorted(set(nonfollowers), key=nonfollowers.index)

    logger.info(
        "There are {0} Nonfollowers of {1}  ~the users {1} is following WHO "
        "do not follow back\n".format(len(nonfollowers), username)
    )

    # store Nonfollowers' data in a local file
    store_nonfollowers(
        username,
        len(all_followers),
        len(all_following),
        nonfollowers,
        logger,
        logfolder,
    )

    return nonfollowers


def get_fans(
    browser,
    self_username,
    username,
    relationship_data,
    live_match,
    store_locally,
    logger,
    logfolder,
):
    """Find Fans of a given user"""

    if username is None or not isinstance(username, str):
        logger.info(
            "Please enter a username to pick Fans  ~leaving out of an invalid value"
        )
        return []

    # get `Followers` data
    all_followers = get_followers(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # if all_followers is False we have targeted a private account that we don't follow
    if not all_followers:
        return False

    # get `Following` data
    all_following = get_following(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # using this approach we can preserve the order of elements to be used
    # with `FIFO`, `LIFO` or `RANDOM` styles
    fans = [user for user in all_followers if user not in all_following]

    # uniqify elements
    fans = sorted(set(fans), key=fans.index)

    logger.info(
        "There are {0} Fans of {1}  ~the users following {1} WHOM {1} does "
        "not follow back\n".format(len(fans), username)
    )

    # store Nonfollowers data in a local file
    store_fans(
        username, len(all_followers), len(all_following), fans, logger, logfolder
    )

    return fans


def get_mutual_following(
    browser,
    self_username,
    username,
    relationship_data,
    live_match,
    store_locally,
    logger,
    logfolder,
):
    """Find Mutual Following of a given user"""

    if username is None or type(username) != str:
        logger.info(
            "Please enter a username to pick Mutual Following  ~leaving out "
            "of an invalid value"
        )
        return []

    # get `Followers` data
    all_followers = get_followers(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # if all_followers is False we have targeted a private account that we don't follow
    if not all_followers:
        return False

    # get `Following` data
    all_following = get_following(
        browser,
        self_username,
        username,
        "full",
        relationship_data,
        live_match,
        store_locally,
        logger,
        logfolder,
    )

    # using this approach we can preserve the order of elements to be used
    # with `FIFO`, `LIFO` or `RANDOM` styles
    mutual_following = [user for user in all_following if user in all_followers]

    # uniqify elements
    mutual_following = sorted(set(mutual_following), key=mutual_following.index)

    logger.info(
        "There are {0} Mutual Following of {1}  ~the users {1} is following "
        "WHO also follow back\n".format(len(mutual_following), username)
    )

    # store Mutual Following data in a local file
    store_mutual_following(
        username,
        len(all_followers),
        len(all_following),
        mutual_following,
        logger,
        logfolder,
    )

    return mutual_following


def store_followers_data(username, grab, grabbed_followers, logger, logfolder):
    """Store grabbed `Followers` data in a local storage at generated date"""
    query_date = datetime.today().strftime("%d-%m-%Y")
    grabbed_followers_size = len(grabbed_followers)
    file_directory = "{}/relationship_data/{}/followers/".format(logfolder, username)
    file_name = "{}{}~{}~{}".format(
        file_directory, query_date, grab, grabbed_followers_size
    )
    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as followers_data:
            with interruption_handler():
                json.dump(grabbed_followers, followers_data)
        logger.info("Stored `Followers` data at {} local file".format(final_file))

    except Exception as exc:
        logger.info(
            "Failed to store `Followers` data in a local file :Z\n{}".format(
                str(exc).encode("utf-8")
            )
        )


def store_following_data(username, grab, grabbed_following, logger, logfolder):
    """Store grabbed `Following` data in a local storage at generated date"""
    query_date = datetime.today().strftime("%d-%m-%Y")
    grabbed_following_size = len(grabbed_following)
    file_directory = "{}/relationship_data/{}/following/".format(logfolder, username)
    file_name = "{}{}~{}~{}".format(
        file_directory, query_date, grab, grabbed_following_size
    )
    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as following_data:
            with interruption_handler():
                json.dump(grabbed_following, following_data)
        logger.info("Stored `Following` data at {} local file".format(final_file))

    except Exception as exc:
        logger.info(
            "Failed to store `Following` data in a local file :Z\n{}".format(
                str(exc).encode("utf-8")
            )
        )


def store_all_unfollowers(username, all_unfollowers, logger, logfolder):
    """Store all Unfollowers data in a local storage at generated date"""
    generation_date = datetime.today().strftime("%d-%m-%Y")
    all_unfollowers_size = len(all_unfollowers)
    file_directory = "{}/relationship_data/{}/unfollowers/all_unfollowers/".format(
        logfolder, username
    )
    file_name = "{}{}~all~{}".format(
        file_directory, generation_date, all_unfollowers_size
    )
    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as unfollowers_data:
            with interruption_handler():
                json.dump(all_unfollowers, unfollowers_data)
        logger.info("Stored all Unfollowers data at {} local file\n".format(final_file))

    except Exception as exc:
        logger.info(
            "Failed to store all Unfollowers data in a local file :Z\n{}"
            "\n".format(str(exc).encode("utf-8"))
        )


def store_active_unfollowers(username, active_unfollowers, logger, logfolder):
    """Store active Unfollowers data in a local storage at generated date"""
    generation_date = datetime.today().strftime("%d-%m-%Y")
    active_unfollowers_size = len(active_unfollowers)
    file_directory = (
        "{}/relationship_data/{}"
        "/unfollowers/active_unfollowers/".format(logfolder, username)
    )
    file_name = "{}{}~active~{}".format(
        file_directory, generation_date, active_unfollowers_size
    )
    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as active_unfollowers_data:
            with interruption_handler():
                json.dump(active_unfollowers, active_unfollowers_data)
        logger.info(
            "Stored active Unfollowers data at {} local file\n".format(final_file)
        )

    except Exception as exc:
        logger.info(
            "Failed to store active Unfollowers data in a local file :Z\n{}"
            "\n".format(str(exc).encode("utf-8"))
        )


def store_nonfollowers(
    username, followers_size, following_size, nonfollowers, logger, logfolder
):
    """Store Nonfollowers data in a local storage at generated date"""
    generation_date = datetime.today().strftime("%d-%m-%Y")
    nonfollowers_size = len(nonfollowers)
    file_directory = "{}/relationship_data/{}/nonfollowers/".format(logfolder, username)
    file_name = "{}{}~[{}-{}]~{}".format(
        file_directory,
        generation_date,
        followers_size,
        following_size,
        nonfollowers_size,
    )
    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as nonfollowers_data:
            with interruption_handler():
                json.dump(nonfollowers, nonfollowers_data)
        logger.info("Stored Nonfollowers data at {} local file\n".format(final_file))

    except Exception as exc:
        logger.info(
            "Failed to store Nonfollowers data in a local file :Z\n{}"
            "\n".format(str(exc).encode("utf-8"))
        )


def store_fans(username, followers_size, following_size, fans, logger, logfolder):
    """Store Fans data in a local storage at generated date"""
    generation_date = datetime.today().strftime("%d-%m-%Y")
    fans_size = len(fans)

    file_directory = "{}/relationship_data/{}/fans/".format(logfolder, username)
    file_name = "{}{}~[{}-{}]~{}".format(
        file_directory, generation_date, followers_size, following_size, fans_size
    )

    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as fans_data:
            with interruption_handler():
                json.dump(fans, fans_data)
        logger.info("Stored Fans data at {} local file\n".format(final_file))

    except Exception as exc:
        logger.info(
            "Failed to store Fans data in a local file :Z\n{}\n".format(
                str(exc).encode("utf-8")
            )
        )


def store_mutual_following(
    username, followers_size, following_size, mutual_following, logger, logfolder
):
    """Store Mutual Following data in a local storage at generated date"""
    generation_date = datetime.today().strftime("%d-%m-%Y")
    mutual_following_size = len(mutual_following)

    file_directory = "{}/relationship_data/{}/mutual_following/".format(
        logfolder, username
    )
    file_name = "{}{}~[{}-{}]~{}".format(
        file_directory,
        generation_date,
        followers_size,
        following_size,
        mutual_following_size,
    )

    file_index = 0
    final_file = "{}.json".format(file_name)

    try:
        if not os.path.exists(file_directory):
            os.makedirs(file_directory)
        # this loop provides unique data files
        while os.path.isfile(final_file):
            file_index += 1
            final_file = "{}({}).json".format(file_name, file_index)

        with open(final_file, "w") as mutual_following_data:
            with interruption_handler():
                json.dump(mutual_following, mutual_following_data)
        logger.info(
            "Stored Mutual Following data at {} local file\n".format(final_file)
        )

    except Exception as exc:
        logger.info(
            "Failed to store Mutual Following data in a local file :Z\n{}"
            "\n".format(str(exc).encode("utf-8"))
        )


def load_followers_data(username, compare_by, compare_track, logger, logfolder):
    """Write grabbed `followers` data into local storage"""

    # Variables
    tracked_filenames = []
    structured_entries = {}
    selected_filename = None

    # get the list of all existing FULL `Followers` data files in
    # ~/logfolder/username/followers/ location
    files_location = "{}/relationship_data/{}/followers".format(logfolder, username)
    followers_data_files = [
        os.path.basename(file)
        for file in glob.glob("{}/*~full*.json".format(files_location))
    ]

    # check if there is any file to be compared
    if not followers_data_files:
        logger.info(
            "There are no any `Followers` data files in the {} location to "
            "compare".format(files_location)
        )
        return [], None

    # Filtrate and get the right track of file to compare
    for data_file in followers_data_files:
        tracked_filenames.append(data_file[:10])
    sorted_filenames = sorted(
        tracked_filenames, key=lambda x: datetime.strptime(x, "%d-%m-%Y")
    )

    this_day = datetime.today().strftime("%d")
    this_month = datetime.today().strftime("%m")
    this_year = datetime.today().strftime("%Y")

    for entry in sorted_filenames:
        entry_day, entry_month, entry_year = entry.split("-")

        structured_entries.setdefault("years", {}).setdefault(
            entry_year, {}
        ).setdefault("months", {}).setdefault(entry_month, {}).setdefault(
            "days", {}
        ).setdefault(
            entry_day, {}
        ).setdefault(
            "entries", []
        ).append(
            entry
        )

    if compare_by == "latest":
        selected_filename = sorted_filenames[-1]

    elif compare_by == "day":
        latest_day = sorted_filenames[-1]
        current_day = datetime.today().strftime("%d-%m-%Y")

        if latest_day == current_day:
            data_for_today = structured_entries["years"][this_year]["months"][
                this_month
            ]["days"][this_day]["entries"]

            if compare_track == "first" or len(data_for_today) <= 1:
                selected_filename = data_for_today[0]
            if compare_track == "median":
                median_index = int(len(data_for_today) / 2)
                selected_filename = data_for_today[median_index]
            if compare_track == "last":
                selected_filename = data_for_today[-1]

        else:
            selected_filename = sorted_filenames[-1]
            logger.info(
                "No any data exists for today!  ~choosing the last existing "
                "data from {}".format(selected_filename)
            )

    elif compare_by == "month":
        latest_month = sorted_filenames[-1][-7:]
        current_month = datetime.today().strftime("%m-%Y")

        if latest_month == current_month:
            data_for_month = []

            for day in structured_entries["years"][this_year]["months"][this_month][
                "days"
            ]:
                data_for_month.extend(
                    structured_entries["years"][this_year]["months"][this_month][
                        "days"
                    ][day]["entries"]
                )

            if compare_track == "first" or len(data_for_month) <= 1:
                selected_filename = data_for_month[0]
            if compare_track == "median":
                median_index = int(len(data_for_month) / 2)
                selected_filename = data_for_month[median_index]
            if compare_track == "last":
                selected_filename = data_for_month[-1]

        else:
            selected_filename = sorted_filenames[-1]
            logger.info(
                "No any data exists for this month!  ~choosing the last "
                "existing data from {}".format(selected_filename)
            )

    elif compare_by == "year":
        latest_year = sorted_filenames[-1][-4:]

        if latest_year == this_year:
            data_for_year = []

            for month in structured_entries["years"][this_year]["months"]:
                for day in structured_entries["years"][this_year]["months"][month][
                    "days"
                ]:
                    data_for_year.extend(
                        structured_entries["years"][this_year]["months"][month]["days"][
                            day
                        ]["entries"]
                    )

            if compare_track == "first" or len(data_for_year) <= 1:
                selected_filename = data_for_year[0]
            if compare_track == "median":
                median_index = int(len(data_for_year) / 2)
                selected_filename = data_for_year[median_index]
            if compare_track == "last":
                selected_filename = data_for_year[-1]

        else:
            selected_filename = sorted_filenames[-1]
            logger.info(
                "No any data exists for this year!  ~choosing the last existing data from {}".format(
                    selected_filename
                )
            )

    elif compare_by == "earliest":
        selected_filename = sorted_filenames[0]

    # load that file
    selected_file = (
        glob.glob("{}/{}~full*.json".format(files_location, selected_filename))
    )[0]
    with open(selected_file) as followers_data_file:
        followers_data = json.load(followers_data_file)

    logger.info(
        "Took prior `Followers` data file from {} with {} usernames "
        "to be compared with live data\n".format(selected_filename, len(followers_data))
    )

    # return that file to be compared
    return followers_data, selected_filename
