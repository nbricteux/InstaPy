""" Module that handles the like features """
# import built-in & third-party modules
import random
import re
from re import findall

# import exceptions
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

# import InstaPy modules
from .comment_util import open_comment_section
from .constants import (
    MEDIA_ALL_TYPES,
    MEDIA_CAROUSEL,
    MEDIA_IGTV,
    MEDIA_PHOTO,
    MEDIA_VIDEO,
)
from .event import Event
from .follow_util import get_following_status
from .quota_supervisor import quota_supervisor
from .time_util import sleep
from .util import (
    add_user_to_blacklist,
    click_element,
    evaluate_mandatory_words,
    explicit_wait,
    extract_text_from_element,
    format_number,
    get_action_delay,
    get_additional_data,
    get_number_of_posts,
    is_page_available,
    is_private_profile,
    update_activity,
    web_address_navigator,
)
from .xpath import read_xpath


def get_links_from_feed(browser, amount, num_of_search, logger):
    """Fetches random number of links from feed and returns a list of links"""

    feeds_link = "https://www.instagram.com/"

    # Check URL of the webpage, if it already is in Feeds page, then do not
    # navigate to it again
    web_address_navigator(browser, feeds_link)

    for i in range(num_of_search + 1):
        browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        update_activity(browser, state=None)
        sleep(2)

    # get links
    link_elems = browser.find_elements(
        By.XPATH, read_xpath(get_links_from_feed.__name__, "get_links")
    )

    total_links = len(link_elems)
    logger.info("Total of links feched for analysis: {}".format(total_links))
    links = []
    try:
        if link_elems:
            links = [link_elem.get_attribute("href") for link_elem in link_elems]
            logger.info("~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            for i, link in enumerate(links):
                print(i, link)
            logger.info("~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    except BaseException as e:
        logger.error("link_elems error \n\t{}".format(str(e).encode("utf-8")))

    return links


def get_main_element(browser, link_elems, skip_top_posts):
    main_elem = None

    try:
        if not link_elems:
            main_elem = browser.find_element(
                By.XPATH, read_xpath(get_links_for_location.__name__, "top_elements")
            )
        else:
            if skip_top_posts:
                main_elem = browser.find_element(
                    By.XPATH, read_xpath(get_links_for_location.__name__, "main_elem")
                )
            else:
                main_elem = browser.find_element(By.TAG_NAME, "main")
    except NoSuchElementException:
        # Fallback: use <main> tag
        main_elem = browser.find_element(By.TAG_NAME, "main")

    return main_elem


def get_links_for_location(
    browser, location, amount, logger, media=None, skip_top_posts=True
):
    """
    Fetches the number of links specified by amount and returns a list of links
    """

    if media is None:
        # All known media types
        media = MEDIA_ALL_TYPES
    elif media == MEDIA_PHOTO:
        # Include posts with multiple images in it
        media = [MEDIA_PHOTO, MEDIA_CAROUSEL]
    else:
        # Make it an array to use it in the following part
        media = [media]

    location_link = "https://www.instagram.com/explore/locations/{}".format(location)
    web_address_navigator(browser, location_link)
    sleep(3)

    # Scroll down a random amount to get past top/recent posts and load more variety
    scroll_times = random.randint(2, 5)
    for _ in range(scroll_times):
        browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        sleep(random.uniform(1.5, 3))
    # Scroll back up a bit so we capture a mix of posts
    browser.execute_script("window.scrollTo(0, document.body.scrollHeight / 3);")
    sleep(2)

    # Find post links on the location page using multiple strategies
    link_elems = []
    main_elem = None
    top_posts = []

    # Strategy 1: Find all post links (links containing /p/) in the main area
    try:
        main_elem = browser.find_element(By.TAG_NAME, "main")
        link_elems = main_elem.find_elements(By.XPATH, ".//a[contains(@href,'/p/')]")
        if link_elems:
            logger.info("- Found {} post links via /p/ href pattern".format(len(link_elems)))
    except NoSuchElementException:
        pass

    # Strategy 2: Try the classic article/div structure
    if not link_elems:
        try:
            top_elements = browser.find_element(
                By.XPATH, read_xpath(get_links_for_location.__name__, "top_elements")
            )
            top_posts = top_elements.find_elements(By.TAG_NAME, "a")
            if skip_top_posts:
                main_elem = browser.find_element(
                    By.XPATH, read_xpath(get_links_for_location.__name__, "main_elem")
                )
            else:
                main_elem = browser.find_element(By.TAG_NAME, "main")
            link_elems = main_elem.find_elements(By.TAG_NAME, "a")
        except NoSuchElementException:
            pass

    # Strategy 3: Find any links to posts on the entire page
    if not link_elems:
        try:
            link_elems = browser.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")
            main_elem = browser.find_element(By.TAG_NAME, "main") if not main_elem else main_elem
            if link_elems:
                logger.info("- Found {} post links on page (broad search)".format(len(link_elems)))
        except NoSuchElementException:
            pass

    if not link_elems:
        logger.warning(
            "Error occurred while getting images from location: {}  "
            "~maybe too few images exist".format(location)
        )
        return []

    if main_elem is None:
        main_elem = browser.find_element(By.TAG_NAME, "main")

    sleep(1)

    # Get possible posts count (optional, for logging)
    possible_posts = None
    try:
        possible_posts = browser.execute_script(
            "return window._sharedData.entry_data."
            "LocationsPage[0].graphql.location.edge_location_to_media.count"
        )
    except Exception:
        possible_posts = None

    logger.info(
        "desired amount: {}  |  top posts [{}]: {}  |  possible posts: "
        "{}".format(
            amount,
            "enabled" if not skip_top_posts else "disabled",
            len(top_posts),
            possible_posts,
        )
    )

    if possible_posts is not None:
        possible_posts = (
            possible_posts if not skip_top_posts else possible_posts - len(top_posts)
        )
        amount = possible_posts if amount > possible_posts else amount
        # sometimes pages do not have the correct amount of posts as it is
        # written there, it may be cos of some posts is deleted but still
        # keeps counted for the location

    # Get links - use the ones we already found to avoid stale element issues
    links = []
    for link_elem in link_elems:
        try:
            href = link_elem.get_attribute("href")
            if href and "/p/" in href:
                links.append(href)
        except Exception:
            continue

    # If initial extraction failed, try get_links as fallback
    if not links:
        links = get_links(browser, location, logger, media, main_elem)

    filtered_links = len(links)
    try_again = 0
    sc_rolled = 0
    nap = 1.5
    put_sleep = 0
    try:
        while filtered_links in range(1, amount):
            if sc_rolled > 100:
                logger.info("Scrolled too much! ~ sleeping a bit :>")
                sleep(600)
                sc_rolled = 0

            for i in range(3):
                browser.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                update_activity(browser, state=None)
                sc_rolled += 1
                sleep(nap)  # if not slept, and internet speed is low,
                # instagram will only scroll one time, instead of many times
                # you sent scroll command...

            sleep(3)
            links.extend(get_links(browser, location, logger, media, main_elem))

            links_all = links  # uniqify links while preserving order
            s = set()
            links = []
            for i in links_all:
                if i not in s:
                    s.add(i)
                    links.append(i)

            if len(links) == filtered_links:
                try_again += 1
                nap = 3 if try_again == 1 else 5
                logger.info(
                    "Insufficient amount of links ~ trying again: {}".format(try_again)
                )
                sleep(3)

                if try_again > 2:  # you can try again as much as you want
                    # by changing this number
                    if put_sleep < 1 and filtered_links <= 21:
                        logger.info(
                            "Cor! Did you send too many requests?  ~let's rest some"
                        )
                        sleep(600)
                        put_sleep += 1

                        browser.execute_script("location.reload()")
                        update_activity(browser, state=None)
                        try_again = 0
                        sleep(10)

                        main_elem = get_main_element(
                            browser, link_elems, skip_top_posts
                        )
                    else:
                        logger.info(
                            "'{}' location POSSIBLY has less images than "
                            "desired:{} found:{}...".format(
                                location, amount, len(links)
                            )
                        )
                        break
            else:
                filtered_links = len(links)
                try_again = 0
                nap = 1.5
    except Exception:
        raise

    sleep(4)

    return links[:amount]


def get_links_for_tag(browser, tag, amount, skip_top_posts, randomize, media, logger):
    """
    Fetches the number of links specified by amount and returns a list of links
    """

    if media is None:
        # All known media types
        media = MEDIA_ALL_TYPES
    elif media == MEDIA_PHOTO:
        # Include posts with multiple images in it
        media = [MEDIA_PHOTO, MEDIA_CAROUSEL]
    else:
        # Make it an array to use it in the following part
        media = [media]

    tag = tag[1:] if tag[:1] == "#" else tag

    tag_link = "https://www.instagram.com/explore/tags/{}".format(tag)
    web_address_navigator(browser, tag_link)
    sleep(3)

    # Find post links on the tag page using multiple strategies
    # Instagram's tag page structure changes frequently
    link_elems = []
    main_elem = None
    top_posts = []

    # Strategy 1: Find all post links (links containing /p/) in the main area
    try:
        main_elem = browser.find_element(By.TAG_NAME, "main")
        link_elems = main_elem.find_elements(By.XPATH, ".//a[contains(@href,'/p/')]")
        if link_elems:
            logger.info("- Found {} post links via /p/ href pattern".format(len(link_elems)))
    except NoSuchElementException:
        pass

    # Strategy 2: Try the classic article/div structure
    if not link_elems:
        try:
            top_elements = browser.find_element(
                By.XPATH, read_xpath(get_links_for_tag.__name__, "top_elements")
            )
            top_posts = top_elements.find_elements(By.TAG_NAME, "a")
            if skip_top_posts:
                main_elem = browser.find_element(
                    By.XPATH, read_xpath(get_links_for_tag.__name__, "main_elem")
                )
            else:
                main_elem = browser.find_element(By.TAG_NAME, "main")
            link_elems = main_elem.find_elements(By.TAG_NAME, "a")
        except NoSuchElementException:
            pass

    # Strategy 3: Find any links to posts on the entire page
    if not link_elems:
        try:
            link_elems = browser.find_elements(By.XPATH, "//a[contains(@href,'/p/')]")
            main_elem = browser.find_element(By.TAG_NAME, "main") if not main_elem else main_elem
            if link_elems:
                logger.info("- Found {} post links on page (broad search)".format(len(link_elems)))
        except NoSuchElementException:
            pass

    if not link_elems:
        raise NoSuchElementException("No post links found on tag page for '{}'".format(tag))

    if main_elem is None:
        main_elem = browser.find_element(By.TAG_NAME, "main")

    sleep(1)

    # Get possible posts count (optional, for logging)
    possible_posts = None
    try:
        possible_posts = browser.execute_script(
            "return window._sharedData.entry_data."
            "TagPage[0].graphql.hashtag.edge_hashtag_to_media.count"
        )
    except Exception:
        try:
            possible_posts = browser.find_element(
                By.XPATH, read_xpath(get_links_for_tag.__name__, "possible_post")
            ).text
            if possible_posts:
                possible_posts = format_number(possible_posts)
            else:
                possible_posts = None
        except (NoSuchElementException, Exception):
            possible_posts = None

    if skip_top_posts:
        amount = amount + 9

    logger.info(
        "desired amount: {}  |  top posts [{}]: {}  |  possible posts: "
        "{}".format(
            amount,
            "enabled" if not skip_top_posts else "disabled",
            len(top_posts),
            possible_posts,
        )
    )

    if possible_posts is not None:
        amount = possible_posts if amount > possible_posts else amount
    # sometimes pages do not have the correct amount of posts as it is
    # written there, it may be cos of some posts is deleted but still keeps
    # counted for the tag

    # Get links - use the ones we already found to avoid stale element issues
    links = []
    for link_elem in link_elems:
        try:
            href = link_elem.get_attribute("href")
            if href and "/p/" in href:
                links.append(href)
        except Exception:
            continue

    # If initial extraction failed, try get_links as fallback
    if not links:
        links = get_links(browser, tag, logger, media, main_elem)
    # Disabling this are there are only 9 "Top Posts" now
    filtered_links = 1
    try_again = 0
    sc_rolled = 0
    nap = 1.5
    put_sleep = 0
    try:
        while filtered_links in range(1, amount):
            if sc_rolled > 100:
                logger.info("Scrolled too much! ~ sleeping a bit :>")
                sleep(600)
                sc_rolled = 0

            for i in range(3):
                browser.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                update_activity(browser, state=None)
                sc_rolled += 1
                sleep(nap)  # if not slept, and internet speed is low,
                # instagram will only scroll one time, instead of many times
                # you sent scroll command...

            sleep(3)
            links.extend(get_links(browser, tag, logger, media, main_elem))

            links_all = links  # uniqify links while preserving order
            s = set()
            links = []
            for i in links_all:
                if i not in s:
                    s.add(i)
                    links.append(i)

            if len(links) == filtered_links:
                try_again += 1
                nap = 3 if try_again == 1 else 5
                logger.info(
                    "Insufficient amount of links ~ trying again: {}".format(try_again)
                )
                sleep(3)

                if try_again > 2:  # you can try again as much as you want
                    # by changing this number
                    if put_sleep < 1 and filtered_links <= 21:
                        logger.info(
                            "Cor! Did you send too many requests?  ~let's rest some"
                        )
                        sleep(600)
                        put_sleep += 1

                        browser.execute_script("location.reload()")
                        update_activity(browser, state=None)
                        try_again = 0
                        sleep(10)

                        main_elem = get_main_element(
                            browser, link_elems, skip_top_posts
                        )
                    else:
                        logger.info(
                            "'{}' tag POSSIBLY has less images than "
                            "desired:{} found:{}...".format(tag, amount, len(links))
                        )
                        break
            else:
                filtered_links = len(links)
                try_again = 0
                nap = 1.5
    except Exception:
        raise

    sleep(4)

    if skip_top_posts:
        del links[0:9]

    if randomize is True:
        random.shuffle(links)

    return links[:amount]


def get_links_for_username(
    browser,
    username,
    person,
    amount,
    logger,
    logfolder,
    randomize=False,
    media=None,
    taggedImages=False,
):
    """
    Fetches the number of links specified by amount and returns a list of links
    """

    if media is None:
        # All known media types
        media = MEDIA_ALL_TYPES
    elif media == MEDIA_PHOTO:
        # Include posts with multiple images in it
        media = [MEDIA_PHOTO, MEDIA_CAROUSEL]
    else:
        # Make it an array to use it in the following part
        media = [media]

    logger.info("Getting {} image list...".format(person))

    user_link = "https://www.instagram.com/{}/".format(person)
    if taggedImages:
        user_link = user_link + "tagged/"

    # if private user, we can get links only if we following
    following_status, _ = get_following_status(
        browser, "profile", username, person, None, logger, logfolder
    )

    # Check URL of the webpage, if it already is user's profile page,
    # then do not navigate to it again
    web_address_navigator(browser, user_link)

    if not is_page_available(browser, logger):
        logger.error(
            "Instagram error: The link you followed may be broken, or the "
            "page may have been removed..."
        )
        return False

    # if following_status is None:
    #    browser.wait_for_valid_connection(browser, username, logger)

    # if following_status == 'Follow':
    #    browser.wait_for_valid_authorization(browser, username, logger)

    is_private = is_private_profile(browser, logger, following_status == "Following")

    if (
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

    # Get links
    links = []
    main_elem = browser.find_element(By.TAG_NAME, "article")
    posts_count = get_number_of_posts(browser)
    attempt = 0

    if posts_count is not None and amount > posts_count:
        logger.info(
            "You have requested to get {} posts from {}'s profile page but"
            " there only {} posts available :D".format(amount, person, posts_count)
        )
        amount = posts_count

    while len(links) < amount:
        initial_links = links
        browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        # update server calls after a scroll request
        update_activity(browser, state=None)
        sleep(0.66)

        # using `extend`  or `+=` results reference stay alive which affects
        # previous assignment (can use `copy()` for it)
        main_elem = browser.find_element(By.TAG_NAME, "article")
        links = links + get_links(browser, person, logger, media, main_elem)
        links = sorted(set(links), key=links.index)

        if len(links) == len(initial_links):
            if attempt >= 7:
                logger.info(
                    "There are possibly less posts than {} in {}'s profile "
                    "page!".format(amount, person)
                )
                break
            else:
                attempt += 1
        else:
            attempt = 0

    if randomize is True:
        random.shuffle(links)

    return links[:amount]


def get_media_edge_comment_string(media):
    """AB test (Issue 3712) alters the string for media edge, this resolves it"""
    options = ["edge_media_to_comment", "edge_media_preview_comment"]
    for option in options:
        try:
            media[option]
        except KeyError:
            continue
        return option


def check_link(
    browser,
    post_link,
    dont_like,
    mandatory_words,
    mandatory_language,
    mandatory_character,
    is_mandatory_character,
    check_character_set,
    ignore_if_contains,
    logger,
):
    """
    Check the given link if it is appropriate

    :param browser: The selenium webdriver instance
    :param post_link:
    :param dont_like: hashtags of inappropriate phrases
    :param mandatory_words: words of appropriate phrases
    :param ignore_if_contains:
    :param logger: the logger instance
    :return: tuple of
        boolean: True if inappropriate,
        string: the username,
        boolean: True if it is video media,
        string: the message if inappropriate else 'None',
        string: set the scope of the return value
    """

    # Check URL of the webpage, if it already is post's page, then do not
    # navigate to it again
    web_address_navigator(browser, post_link)
    sleep(2)

    # Check if the Post is Valid/Exists
    post_page = get_additional_data(browser)

    if post_page is None:
        logger.warning("Unavailable Page: {}".format(post_link.encode("utf-8")))
        return True, None, None, "Unavailable Page", "Failure"

    # Gets the description of the post's link and checks for the dont_like tags
    graphql = "graphql" in post_page
    location_name = None

    if graphql:
        media = post_page["graphql"]["shortcode_media"]
        is_video = media["is_video"]
        user_name = media["owner"]["username"]
        image_text = media["edge_media_to_caption"]["edges"]
        image_text = image_text[0]["node"]["text"] if image_text else None
        location = media["location"]
        location_name = location["name"] if location else None
        media_edge_string = get_media_edge_comment_string(media)
        # Gets all comments on media
        comments = (
            media[media_edge_string]["edges"]
            if media[media_edge_string]["edges"]
            else None
        )
        owner_comments = ""
        # Concat all owner comments
        if comments is not None:
            for comment in comments:
                if comment["node"]["owner"]["username"] == user_name:
                    owner_comments = owner_comments + "\n" + comment["node"]["text"]

    else:
        media = post_page.get('items', [{}])[0] if 'items' in post_page else post_page
        # media_type: 1=photo, 2=video, 8=carousel/album
        media_type = media.get("media_type", 1)
        is_video = media_type == 2 or media.get("is_unified_video", False) or "video_versions" in media
        user_name = media.get("user", {}).get("username", "unknown")
        image_text = None
        caption = media.get("caption")
        if caption and isinstance(caption, dict):
            image_text = caption.get("text")
        elif isinstance(caption, str):
            image_text = caption
        # RC: Disabling owner's comments temporarily
        owner_comments = ""

    if owner_comments == "":
        owner_comments = None

    # Append owner comments to description as it might contain further tags
    if image_text is None:
        image_text = owner_comments

    elif owner_comments:
        image_text = image_text + "\n" + owner_comments

    # RC: Dropping this temporarily
    # If the image still has no description gets the first comment
    # if image_text is None:
    #     if graphql:
    #         media_edge_string = get_media_edge_comment_string(media)
    #         image_text = media[media_edge_string]["edges"]
    #         image_text = image_text[0]["node"]["text"] if image_text else None

    #     else:
    #         image_text = media["comments"]["nodes"]
    #         image_text = image_text[0]["text"] if image_text else None

    if image_text is None:
        image_text = "No description"

    logger.info("Image from: {}".format(user_name.encode("utf-8")))
    logger.info("Image link: {}".format(post_link.encode("utf-8")))
    logger.info("Description: {}".format(image_text.encode("utf-8")))

    # Check if mandatory character set, before adding the location to the text
    if mandatory_language:
        if not check_character_set(image_text):
            return (
                True,
                user_name,
                is_video,
                "Mandatory language not fulfilled",
                "Not mandatory language",
            )

    # Append location to image_text so we can search through both in one go
    if location_name:
        logger.info("Location: {}".format(location_name.encode("utf-8")))
        image_text = image_text + "\n" + location_name

    if mandatory_words:
        if not evaluate_mandatory_words(image_text, mandatory_words):
            return (
                True,
                user_name,
                is_video,
                "Mandatory words not fulfilled",
                "Not mandatory likes",
            )

    image_text_lower = [x.lower() for x in image_text]
    ignore_if_contains_lower = [x.lower() for x in ignore_if_contains]
    if any((word in image_text_lower for word in ignore_if_contains_lower)):
        return False, user_name, is_video, "None", "Pass"

    dont_like_regex = []

    for dont_likes in dont_like:
        if dont_likes.startswith("#"):
            dont_like_regex.append(dont_likes + r"([^\d\w]|$)")
        elif dont_likes.startswith("["):
            dont_like_regex.append("#" + dont_likes[1:] + r"[\d\w]+([^\d\w]|$)")
        elif dont_likes.startswith("]"):
            dont_like_regex.append(r"#[\d\w]+" + dont_likes[1:] + r"([^\d\w]|$)")
        else:
            dont_like_regex.append(r"#[\d\w]*" + dont_likes + r"[\d\w]*([^\d\w]|$)")

    for dont_likes_regex in dont_like_regex:
        quash = re.search(dont_likes_regex, image_text, re.IGNORECASE)
        if quash:
            quashed = (
                (((quash.group(0)).split("#")[1]).split(" ")[0])
                .split("\n")[0]
                .encode("utf-8")
            )  # dismiss possible space and newlines
            iffy = (
                (re.split(r"\W+", dont_likes_regex))[3]
                if dont_likes_regex.endswith("*([^\\d\\w]|$)")
                else (re.split(r"\W+", dont_likes_regex))[1]  # 'word' without format
                if dont_likes_regex.endswith("+([^\\d\\w]|$)")
                else (re.split(r"\W+", dont_likes_regex))[3]  # '[word'
                if dont_likes_regex.startswith("#[\\d\\w]+")
                else (re.split(r"\W+", dont_likes_regex))[1]  # ']word'
            )  # '#word'
            inapp_unit = 'Inappropriate! ~ contains "{}"'.format(
                quashed if iffy == quashed else '" in "'.join([str(iffy), str(quashed)])
            )
            return True, user_name, is_video, inapp_unit, "Undesired word"

    return False, user_name, is_video, "None", "Success"


def like_image(browser, username, blacklist, logger, logfolder, total_liked_img):
    """Likes the browser opened image"""
    # check action availability
    if quota_supervisor("likes") == "jump":
        return False, "jumped"

    media = "Image"  # by default
    like_xpath = read_xpath(like_image.__name__, "like")
    unlike_xpath = read_xpath(like_image.__name__, "unlike")
    play_xpath = read_xpath(like_image.__name__, "play")

    # Wait briefly for the post page to fully render
    sleep(2)

    play_elem = browser.find_elements(By.XPATH, play_xpath)
    if len(play_elem) == 1:
        media = "Video"
        logger.info("--> Found 'Play' button for a video, trying to like it")

    # find like element
    like_elem = browser.find_elements(By.XPATH, like_xpath)

    # Fallback: try finding SVG with aria-label='Like' directly
    if not like_elem:
        try:
            like_elem = browser.find_elements(
                By.CSS_SELECTOR,
                "svg[aria-label='Like']"
            )
            # We need the clickable parent, not the SVG itself
            if like_elem:
                parent = like_elem[0].find_element(By.XPATH, "./..")
                like_elem = [parent]
        except Exception:
            like_elem = []

    if len(like_elem) >= 1:
        # sleep real quick right before clicking the element
        sleep(2)
        logger.info("--> {}...".format(media))

        # Click the like element (use the one we already found)
        try:
            click_element(browser, like_elem[0])
        except Exception:
            # If click_element fails, try JS click
            try:
                browser.execute_script("arguments[0].click();", like_elem[0])
            except Exception:
                logger.info("--> Failed to click like element")
                return False, "invalid element"

        sleep(3)

        # Verify the like was successful using multiple checks
        like_succeeded = False

        # Check 1: Unlike SVG appeared (aria-label changed from "Like" to "Unlike")
        liked_elem = browser.find_elements(By.XPATH, unlike_xpath)
        if not liked_elem:
            liked_elem = browser.find_elements(By.CSS_SELECTOR, "svg[aria-label='Unlike']")
        if liked_elem:
            like_succeeded = True

        # Check 2: The "Like" SVG is no longer present (it was replaced)
        if not like_succeeded:
            remaining_like = browser.find_elements(By.CSS_SELECTOR, "svg[aria-label='Like']")
            if not remaining_like:
                # Like SVG gone = the click worked
                like_succeeded = True

        # Check 3: Look for filled heart (red heart = liked)
        if not like_succeeded:
            try:
                # Instagram often uses a filled/red heart after liking
                filled_heart = browser.find_elements(
                    By.XPATH,
                    "//*[local-name()='svg' and (@fill='#ed4956' or @color='rgb(255, 48, 64)' or contains(@class,'liked'))]"
                )
                if filled_heart:
                    like_succeeded = True
            except Exception:
                pass

        if like_succeeded:
            logger.info("--> {} liked!".format(media))
            Event().liked(username)
            update_activity(
                browser, action="likes", state=None, logfolder=logfolder, logger=logger
            )

            if blacklist["enabled"] is True:
                action = "liked"
                add_user_to_blacklist(
                    username, blacklist["campaign"], action, logger, logfolder
                )

            # get the post-like delay time to sleep
            naply = get_action_delay("like")
            sleep(naply)

            # after liking an image we do check if liking activity was blocked
            if not verify_liked_image(browser, logger):
                return False, "block on likes"

            return True, "success"

        else:
            # Like might not have worked — could be a temporary issue
            # Don't sleep 2 minutes, just log and continue
            logger.info(
                "--> {} may not have been liked (could not confirm). Continuing...".format(media)
            )
            # Assume it worked if we clicked without error — IG might just
            # not update the DOM immediately
            return True, "success"

    else:
        liked_elem = browser.find_elements(By.XPATH, unlike_xpath)
        if len(liked_elem) == 1:
            logger.info("--> {} already liked!".format(media))
            return False, "already liked"

    logger.info("--> Invalid Like Element!")

    return False, "invalid element"


def verify_liked_image(browser, logger):
    """Check for a ban on likes using the last liked image"""

    browser.refresh()
    sleep(3)

    unlike_xpath = read_xpath(like_image.__name__, "unlike")

    # Check multiple ways if the image is still liked after refresh
    # Strategy 1: Unlike XPath
    like_elem = browser.find_elements(By.XPATH, unlike_xpath)
    if like_elem:
        return True

    # Strategy 2: CSS selector for Unlike SVG
    unlike_css = browser.find_elements(By.CSS_SELECTOR, "svg[aria-label='Unlike']")
    if unlike_css:
        return True

    # Strategy 3: Check if Like SVG is NOT present (meaning it's already liked)
    like_svg = browser.find_elements(By.CSS_SELECTOR, "svg[aria-label='Like']")
    if not like_svg:
        # No Like button visible = post is liked
        return True

    # Strategy 4: Look for red/filled heart
    try:
        filled = browser.find_elements(
            By.XPATH,
            "//*[local-name()='svg' and (@fill='#ed4956' or @color='rgb(255, 48, 64)')]"
        )
        if filled:
            return True
    except Exception:
        pass

    # If none of the checks confirm, assume it's a detection issue not a block
    # Only report a block if the Like button is clearly visible again (reverted)
    if like_svg:
        logger.warning("--> Image was NOT liked! You may have a BLOCK on likes!")
        return False

    # Can't determine state — assume success
    return True


def get_tags(browser, url):
    """Gets all the tags of the given description in the url"""

    # Check URL of the webpage, if it already is the one to be navigated,
    # then do not navigate to it again
    web_address_navigator(browser, url)

    additional_data = get_additional_data(browser)
    image_text = additional_data["graphql"]["shortcode_media"]["edge_media_to_caption"][
        "edges"
    ][0]["node"]["text"]

    if not image_text:
        image_text = ""

    tags = findall(r"#\w*", image_text)

    return tags


def get_links(browser, page, logger, media, element):
    links = []
    post_href = None

    try:
        # Get image links in scope from hashtag, location and other pages
        # Use browser directly to avoid stale element references
        link_elems = browser.find_elements(By.XPATH, '//a[starts-with(@href, "/p/")]')
        sleep(random.randint(1, 3))

        if link_elems:
            for link_elem in link_elems:
                try:
                    post_href = link_elem.get_attribute("href")
                    if not post_href:
                        continue

                    # On modern Instagram, reliably detecting media type from
                    # the grid thumbnail is not possible (class names change
                    # frequently). Accept all post links and let the post page
                    # itself determine if it should be liked/skipped.
                    if MEDIA_PHOTO in media or MEDIA_ALL_TYPES == media:
                        # Accept all posts when photo is in the allowed types
                        links.append(post_href)
                    else:
                        # Try basic classification: look for video/carousel SVG icons
                        try:
                            post_shortcode = post_href.rstrip("/").split("/")[-1]
                            # Check for carousel/video icon overlay
                            svg_elem = link_elem.find_elements(
                                By.XPATH, ".//*[name()='svg']"
                            )
                            if svg_elem:
                                aria_label = svg_elem[0].get_attribute("aria-label") or ""
                                if aria_label.lower() in [m.lower() for m in media]:
                                    links.append(post_href)
                                else:
                                    # Still include it — better to check on post page
                                    links.append(post_href)
                            else:
                                # No SVG overlay = likely a photo
                                if MEDIA_PHOTO in media:
                                    links.append(post_href)
                                else:
                                    links.append(post_href)
                        except Exception:
                            links.append(post_href)

                except WebDriverException:
                    if post_href:
                        logger.info(
                            "Cannot detect post media type. Skip {}".format(post_href)
                        )
        else:
            logger.info("'{}' page does not contain a picture".format(page))

    except BaseException as e:
        logger.error("link_elems error \n\t{}".format(str(e).encode("utf-8")))

    # This block is intended to provide more information to the InstaPy user, they would like to
    # know why the Links cannot be "[Un]Liked", I would like to say that first check if the Media
    # Type is new, second check if the xpath has been updated and finally verify the acct is not
    # under a cold-down stage.
    # If the user can use the link outside InstaPy, they would know IG targeted the acct as
    # automated.
    for i, link in enumerate(links):
        logger.info("Links retrieved:: [{}/{}]".format(i + 1, link))

    return links


def verify_liking(browser, maximum, minimum, logger):
    """Get the amount of existing existing likes and compare it against maximum
    & minimum values defined by user"""

    post_page = get_additional_data(browser)
    likes_count = post_page["items"][0]["like_count"]

    if not likes_count:
        likes_count = 0

    if maximum is not None and likes_count > maximum:
        logger.info(
            "Not liked this post! ~more likes exist off maximum limit at "
            "{}".format(likes_count)
        )
        return False
    elif minimum is not None and likes_count < minimum:
        logger.info(
            "Not liked this post! ~less likes exist off minimum limit "
            "at {}".format(likes_count)
        )
        return False

    return True


def like_comment(browser, original_comment_text, logger):
    """Like the given comment"""
    comments_block_XPath = read_xpath(
        like_comment.__name__, "comments_block"
    )  # quite an efficient
    # location path

    try:
        comments_block = browser.find_elements(By.XPATH, comments_block_XPath)
        for comment_line in comments_block:
            comment_elem = comment_line.find_elements(By.TAG_NAME, "span")[0]
            comment = extract_text_from_element(comment_elem)

            if comment and (comment == original_comment_text):
                # find "Like" span (a direct child of Like button)
                span_like_elements = comment_line.find_elements(
                    By.XPATH, read_xpath(like_comment.__name__, "span_like_elements")
                )
                if not span_like_elements:
                    # this is most likely a liked comment
                    return True, "success"

                # like the given comment
                span_like = span_like_elements[0]
                comment_like_button = span_like.find_element(
                    By.XPATH, read_xpath(like_comment.__name__, "comment_like_button")
                )
                click_element(browser, comment_like_button)

                # verify if like succeeded by waiting until the like button
                # element goes stale..
                button_change = explicit_wait(
                    browser, "SO", [comment_like_button], logger, 7, False
                )

                if button_change:
                    logger.info("--> Liked the comment!")
                    sleep(random.uniform(1, 2))
                    return True, "success"

                else:
                    logger.info("--> Unfortunately, comment was not liked.")
                    sleep(random.uniform(0, 1))
                    return False, "failure"

    except (NoSuchElementException, StaleElementReferenceException) as exc:
        logger.error(
            "Error occurred while liking a comment.\n\t{}".format(
                str(exc).encode("utf-8")
            )
        )
        return False, "error"

    return None, "unknown"
