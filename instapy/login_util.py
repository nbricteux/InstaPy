"""Module only used for the login part of the script"""
# import built-in & third-party modules
import json
import os
import pickle
import random
import socket

# import exceptions
from selenium.common.exceptions import (
    MoveTargetOutOfBoundsException,
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# import InstaPy modules
from .time_util import sleep
from .util import (
    check_authorization,
    click_element,
    explicit_wait,
    reload_webpage,
    update_activity,
    web_address_navigator,
)
from .xpath import read_xpath


def bypass_suspicious_login(
    browser, logger, logfolder, bypass_security_challenge_using
):
    """Bypass suspicious loggin attempt verification."""

    # close sign up Instagram modal if available
    dismiss_get_app_offer(browser, logger)
    dismiss_notification_offer(browser, logger)
    dismiss_this_was_me(browser)

    option = None
    if bypass_security_challenge_using == "sms":
        try:
            option = browser.find_element(
                By.XPATH,
                read_xpath(bypass_suspicious_login.__name__, "bypass_with_sms_option"),
            )
        except NoSuchElementException:
            logger.warning(
                "Unable to choose ({}) option to bypass the challenge".format(
                    bypass_security_challenge_using.upper()
                )
            )

    if bypass_security_challenge_using == "email":
        try:
            option = browser.find_element(
                By.XPATH,
                read_xpath(
                    bypass_suspicious_login.__name__, "bypass_with_email_option"
                ),
            )
        except NoSuchElementException:
            logger.warning(
                "Unable to choose ({}) option to bypass the challenge".format(
                    bypass_security_challenge_using.upper()
                )
            )

    # click on your option
    (ActionChains(browser).move_to_element(option).click().perform())
    # next button click will miss the DOM reference for this element, so ->
    option_text = option.text

    # click on security code
    send_security_code_button = browser.find_element(
        By.XPATH,
        read_xpath(bypass_suspicious_login.__name__, "send_security_code_button"),
    )
    (ActionChains(browser).move_to_element(send_security_code_button).click().perform())

    # update server calls
    update_activity(browser, state=None)

    logger.info("Instagram detected an unusual login attempt")
    logger.info('Check Instagram App for "Suspicious Login attempt" prompt')
    logger.info("A security code was sent to your {}".format(option_text))

    security_code = None
    try:
        path = "{}state.json".format(logfolder)
        data = {}
        # check if file exists and has content
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            # load JSON file
            with open(path, "r") as json_file:
                data = json.load(json_file)

        # update connection state
        security_code = data["challenge"]["security_code"]
    except Exception:
        logger.info("Security Code not present in {}state.json file".format(logfolder))

    if security_code is None:
        security_code = input("Type the security code here: ")

    security_code_field = browser.find_element(
        By.XPATH, read_xpath(bypass_suspicious_login.__name__, "security_code_field")
    )

    (
        ActionChains(browser)
        .move_to_element(security_code_field)
        .click()
        .send_keys(security_code)
        .perform()
    )

    # update server calls for both 'click' and 'send_keys' actions
    for _ in range(2):
        update_activity(browser, state=None)

    submit_security_code_button = browser.find_element(
        By.XPATH,
        read_xpath(bypass_suspicious_login.__name__, "submit_security_code_button"),
    )

    (
        ActionChains(browser)
        .move_to_element(submit_security_code_button)
        .click()
        .perform()
    )

    # update server calls
    update_activity(browser, state=None)

    try:
        sleep(3)
        # locate wrong security code message
        wrong_login = browser.find_element(
            By.XPATH, read_xpath(bypass_suspicious_login.__name__, "wrong_login")
        )

        if wrong_login is not None:
            wrong_login_msg = "Wrong security code! Please check the code Instagram sent you and try again."
            update_activity(
                browser,
                action=None,
                state=wrong_login_msg,
                logfolder=logfolder,
                logger=logger,
            )
            logger.warning(wrong_login_msg)

    except NoSuchElementException:
        # correct security code
        pass


def check_browser(browser, logfolder, logger, proxy_address):
    # set initial state to offline
    update_activity(
        browser,
        action=None,
        state="trying to connect",
        logfolder=logfolder,
        logger=logger,
    )

    # check connection status
    try:
        logger.info("-- Connection Checklist [1/2] (Internet Connection Status)")
        browser.get("view-source:https://freegeoip.app/json")
        pre = browser.find_element(By.TAG_NAME, "pre").text
        current_ip_info = json.loads(pre)
        if (
            proxy_address is not None
            and socket.gethostbyname(proxy_address) != current_ip_info["ip"]
        ):
            logger.warning("- Proxy is set, but it's not working properly")
            logger.warning(
                '- Expected Proxy IP is "{}", and the current IP is "{}"'.format(
                    proxy_address, current_ip_info["ip"]
                )
            )
            logger.warning("- Try again or disable the Proxy Address on your setup")
            logger.warning("- Aborting connection...")
            return False
        else:
            logger.info("- Internet Connection Status: ok")
            logger.info(
                '- Current IP is "{}" and it\'s from "{}/{}"'.format(
                    current_ip_info["ip"],
                    current_ip_info["country_name"],
                    current_ip_info["country_code"],
                )
            )
            update_activity(
                browser,
                action=None,
                state="Internet connection is ok",
                logfolder=logfolder,
                logger=logger,
            )
    except Exception:
        logger.warning("- Internet Connection Status: error")
        update_activity(
            browser,
            action=None,
            state="There is an issue with the internet connection",
            logfolder=logfolder,
            logger=logger,
        )
        return False

    # check if hide-selenium extension is running
    logger.info("-- Connection Checklist [2/2] (Hide Selenium Extension)")
    webdriver = browser.execute_script("return window.navigator.webdriver")
    logger.info("- window.navigator.webdriver response: {}".format(webdriver))
    if webdriver:
        logger.warning("- Hide Selenium Extension: error")
    else:
        logger.info("- Hide Selenium Extension: ok")

    # everything is ok, then continue(True)
    return True


def login_user(
    browser,
    username,
    password,
    logger,
    logfolder,
    proxy_address,
    security_code_to_phone,
    security_codes,
    want_check_browser,
):
    """Logins the user with the given username and password"""
    assert username, "Username not provided"
    assert password, "Password not provided"

    # Hotfix - this check crashes more often than not -- plus in not necessary,
    # I can verify my own connection
    #if want_check_browser:
    #    if not check_browser(browser, logfolder, logger, proxy_address):
    #        return False

    ig_homepage = "https://www.instagram.com"
    web_address_navigator(browser, ig_homepage)
    
    # Handle cookie popup immediately when page loads
    sleep(2)
    try:
        # Try multiple variations of the cookie accept button
        cookie_elem = browser.find_element(
            By.XPATH, 
            "//button[contains(text(),'Allow') or contains(text(),'Accept') or contains(text(),'cookies')]"
        )
        logger.info("- Cookie popup detected, accepting cookies...")
        cookie_elem.click()
        sleep(2)
    except NoSuchElementException:
        logger.info("- No cookie popup detected or already accepted")

    cookie_file = "{0}{1}_cookie.pkl".format(logfolder, username)
    cookie_loaded = None
    login_state = None

    # try to load cookie from username
    try:
        for cookie in pickle.load(open(cookie_file, "rb")):
            # SameSite = Strict, your cookie will only be sent in a
            # first-party context. In user terms, the cookie will only be sent
            # if the site for the cookie matches the site currently shown in
            # the browser's URL bar.
            if "sameSite" in cookie and cookie["sameSite"] == "None":
                cookie["sameSite"] = "Strict"

            browser.add_cookie(cookie)

        sleep(4)
        cookie_loaded = True
        logger.info("- Cookie file for user '{}' loaded...".format(username))

        # force refresh after cookie load or check_authorization() will FAIL
        reload_webpage(browser)
        sleep(4)

        # cookie has been LOADED, so the user SHOULD be logged in
        login_state = check_authorization(
            browser, username, "activity counts", logger, False
        )
        sleep(4)

    except (WebDriverException, OSError, IOError):
        # Just info the user, not an error
        logger.info("- Cookie file not found, creating cookie...")

    if login_state and cookie_loaded:
        # Cookie loaded and joined IG, dismiss following features if availables
        dismiss_notification_offer(browser, logger)
        dismiss_save_information(browser, logger)
        accept_igcookie_dialogue(browser, logger)
        return True

    # This fix comes from comment in #6060 If not necessary we can remove it
    accept_igcookie_dialogue(browser, logger)

    # if user is still not logged in, then there is an issue with the cookie
    # so go create a new cookie.
    if cookie_loaded:
        logger.warning(
            "- Issue with cookie for user '{}'. Creating new cookie...".format(username)
        )

        # Error could be faced due to "<button class="sqdOP L3NKy y3zKF"
        # type="button"> Cookie could not be loaded" or similar.
        # Session displayed we are in, but then a failure for the first
        # `login_elem` like the element is no longer attached to the DOM.
        # Saw this issue when session hasn't been used for a while; which means
        # "expiry" values in cookie are outdated.
        try:
            # Since having issues with the cookie a new one can be generated,
            # if cookie cannot be created or deleted stop execution.
            logger.info("- Deleting browser cookies...")
            browser.delete_all_cookies()
            browser.refresh()
            # Delete file from Filesystem if any issue with the cookie
            os.remove(cookie_file)
            sleep(random.randint(3, 5))

        except Exception as e:
            # NF: start
            if isinstance(e, WebDriverException):
                logger.exception(
                    "Error occurred while deleting cookies from web browser!\n\t{}".format(
                        str(e).encode("utf-8")
                    )
                )
            return False
            # NF: end

    web_address_navigator(browser, ig_homepage)

    # Instead of trying to find and click "Log in" link, go directly to login page
    # This is more reliable than trying to find the link on the homepage
    login_url = "https://www.instagram.com/accounts/login/"
    
    # Check if the first div is 'Create an Account' or 'Log In'
    # Only try to click if we're on the homepage, not already on login page
    if "/accounts/login" not in browser.current_url:
        login_elem = None
        try:
            login_elem = browser.find_element(
                By.XPATH, read_xpath(login_user.__name__, "login_elem")
            )
        except NoSuchElementException:
            logger.warning("Login A/B test detected! Trying another string...")
            try:
                login_elem = browser.find_element(
                    By.XPATH,
                    read_xpath(login_user.__name__, "login_elem_no_such_exception"),
                )
            except NoSuchElementException:
                logger.warning("Could not pass the login A/B test. Trying third string...")
                try:
                    login_elem = browser.find_element(
                        By.XPATH,
                        read_xpath(login_user.__name__, "login_elem_no_such_exception_2"),
                    )
                except NoSuchElementException:
                    logger.warning("Could not pass the login A/B test. Navigating directly to login page...")
                    # Instead of failing, just navigate directly to login page
                    web_address_navigator(browser, login_url)
                    login_elem = None

    if login_elem is not None:
        try:
            logger.info("- Clicking 'Log in' link...")
            (ActionChains(browser).move_to_element(login_elem).click().perform())
        except MoveTargetOutOfBoundsException:
            login_elem.click()

        # update server calls
        update_activity(browser, state=None)
    else:
        logger.info("- Navigated directly to login page")

    # Enter username and password and logs the user in
    # Sometimes the element name isn't 'Username' and 'Password'
    # (valid for placeholder too)

    # wait until it navigates to the login page
    # 10/2020 -> "WebDriver:GetTitle" - {"value":"Instagram"}
    login_page_title = "Instagram"
    explicit_wait(browser, "TC", login_page_title, logger)

    # wait until the 'username' input element is located and visible
    input_username_XP = read_xpath(login_user.__name__, "input_username_XP")
    explicit_wait(browser, "VOEL", [input_username_XP, "XPath"], logger)

    # user
    input_username = browser.find_element(By.XPATH, input_username_XP)

    (
        ActionChains(browser)
        .move_to_element(input_username)
        .click()
        .send_keys(username)
        .perform()
    )

    # update server calls for both 'click' and 'send_keys' actions
    for _ in range(2):
        update_activity(browser, state=None)

    sleep(1)

    # password
    input_password = browser.find_element(
        By.XPATH, read_xpath(login_user.__name__, "input_password")
    )

    if not isinstance(password, str):
        password = str(password)

    (
        ActionChains(browser)
        .move_to_element(input_password)
        .click()
        .send_keys(password)
        .perform()
    )

    sleep(1)

    # Find and click the login button instead of pressing ENTER
    logger.info("- Looking for login submit button...")
    
    # Wait longer for the button to appear (Instagram SPA needs time to render)
    sleep(3)
    
    try:
        # Try multiple XPath variations for the login button with explicit wait
        login_button = None
        max_attempts = 5
        attempt = 0
        
        while login_button is None and attempt < max_attempts:
            attempt += 1
            logger.info(f"- Attempt {attempt}/{max_attempts} to find login button...")
            
            # Try 1: Instagram Bloks framework - div with role="button" and aria-label="Log in"
            # This is the current Instagram login page structure (wbloks framework)
            try:
                login_button = browser.find_element(
                    By.XPATH,
                    "//div[@role='button'][@aria-label='Log in']"
                )
                logger.info("- Found login button by div[role='button'][aria-label='Log in']")
                break
            except NoSuchElementException:
                pass
            
            # Try 2: Button with type submit (classic Instagram)
            if login_button is None:
                try:
                    login_button = browser.find_element(By.XPATH, "//button[@type='submit']")
                    logger.info("- Found login button by type='submit'")
                    break
                except NoSuchElementException:
                    pass
            
            # Try 3: Button containing "Log in" text (case insensitive)
            if login_button is None:
                try:
                    login_button = browser.find_element(
                        By.XPATH, 
                        "//button[contains(translate(text(), 'LOGIN', 'login'), 'log in')]"
                    )
                    logger.info("- Found login button by text content (case insensitive)")
                    break
                except NoSuchElementException:
                    pass
            
            # Try 4: Any div[role='button'] containing "Log in" text (Bloks variant)
            if login_button is None:
                try:
                    login_button = browser.find_element(
                        By.XPATH,
                        "//div[@role='button'][.//span[contains(text(),'Log in') or contains(text(),'Log In')]]"
                    )
                    logger.info("- Found login button by div[role='button'] with 'Log in' span text")
                    break
                except NoSuchElementException:
                    pass
            
            # Try 5: Any button in a form with username/password inputs
            if login_button is None:
                try:
                    login_button = browser.find_element(
                        By.XPATH, 
                        "//form[.//input[@name='username'] and .//input[@name='password']]//button[@type='submit']"
                    )
                    logger.info("- Found login button in form with username/password")
                    break
                except NoSuchElementException:
                    pass
            
            # Try 6: Button with specific div structure (Instagram often uses nested divs)
            if login_button is None:
                try:
                    login_button = browser.find_element(
                        By.XPATH, 
                        "//button[.//div[contains(text(),'Log in') or contains(text(),'Log In')]]"
                    )
                    logger.info("- Found login button with nested div")
                    break
                except NoSuchElementException:
                    pass
            
            # Try 7: CSS selector fallback for Bloks framework
            if login_button is None:
                try:
                    login_button = browser.find_element(
                        By.CSS_SELECTOR,
                        "div[data-bloks-name='bk.components.Flexbox'][role='button'][aria-label='Log in']"
                    )
                    logger.info("- Found login button by CSS selector (Bloks framework)")
                    break
                except NoSuchElementException:
                    pass
            
            # Wait before next attempt
            if login_button is None and attempt < max_attempts:
                logger.info(f"- Button not found, waiting 2 seconds before retry...")
                sleep(2)
        
        if login_button is not None:
            logger.info("- Clicking login button...")
            try:
                # Scroll button into view first
                browser.execute_script("arguments[0].scrollIntoView(true);", login_button)
                sleep(0.5)
                
                # Try multiple click strategies for reliability
                tag_name = login_button.tag_name.lower()
                
                # Strategy A: ActionChains (simulates real user interaction)
                try:
                    (
                        ActionChains(browser)
                        .move_to_element(login_button)
                        .click()
                        .perform()
                    )
                    logger.info("- Login button clicked via ActionChains")
                except Exception as e:
                    logger.warning("- ActionChains click failed: {}".format(str(e)))
                    # Strategy B: Direct .click()
                    try:
                        login_button.click()
                        logger.info("- Login button clicked via direct click")
                    except Exception as e2:
                        logger.warning("- Direct click failed: {}".format(str(e2)))
                        # Strategy C: JavaScript click as last resort
                        browser.execute_script("arguments[0].click();", login_button)
                        logger.info("- Login button clicked via JavaScript")
            except Exception as e:
                logger.warning("- All button click methods failed: {}".format(str(e)))
                # Final fallback: press Enter on the password field
                logger.info("- Falling back to pressing ENTER on password field...")
                (
                    ActionChains(browser)
                    .move_to_element(input_password)
                    .click()
                    .send_keys(Keys.ENTER)
                    .perform()
                )
        else:
            raise NoSuchElementException("Could not find login button with any method after {} attempts".format(max_attempts))
            
    except NoSuchElementException as e:
        # Fallback to pressing ENTER if button not found
        logger.warning("- Login button not found, pressing ENTER as fallback...")
        logger.warning("- Error: {}".format(str(e)))
        
        # Save screenshot for debugging
        try:
            screenshot_path = "{}login_button_not_found.png".format(logfolder)
            browser.save_screenshot(screenshot_path)
            logger.info("- Screenshot saved to: {}".format(screenshot_path))
        except Exception as screenshot_error:
            logger.warning("- Could not save screenshot: {}".format(str(screenshot_error)))
        
        # Try to log the page source for debugging
        try:
            page_source_path = "{}login_page_source.html".format(logfolder)
            with open(page_source_path, "w", encoding="utf-8") as f:
                f.write(browser.page_source)
            logger.info("- Page source saved to: {}".format(page_source_path))
        except Exception as source_error:
            logger.warning("- Could not save page source: {}".format(str(source_error)))
        
        (
            ActionChains(browser)
            .move_to_element(input_password)
            .click()
            .send_keys(Keys.ENTER)
            .perform()
        )

    # update server calls for both 'click' and 'send_keys' actions
    for _ in range(4):
        update_activity(browser, state=None)

    # Wait for login to process - Instagram needs time after credentials submit
    logger.info("- Waiting for login to process...")
    sleep(5)

    # Check if login form is still visible (password field present).
    # If the password field is gone, the login button click worked and
    # we've moved to 2FA, challenge, or the feed.
    current_url = browser.current_url
    logger.info("- Current URL after login attempt: {}".format(current_url))
    
    password_still_present = False
    try:
        browser.find_element(By.XPATH, "//input[@name='password']")
        password_still_present = True
    except NoSuchElementException:
        password_still_present = False

    if password_still_present:
        logger.info("- Password field still present, login button click may not have worked. Trying ENTER...")
        try:
            input_password_retry = browser.find_element(
                By.XPATH, read_xpath(login_user.__name__, "input_password")
            )
            (
                ActionChains(browser)
                .move_to_element(input_password_retry)
                .click()
                .send_keys(Keys.ENTER)
                .perform()
            )
            # update server calls
            for _ in range(2):
                update_activity(browser, state=None)
            sleep(5)
        except Exception as e:
            logger.warning("- ENTER fallback failed: {}".format(str(e)))
    else:
        logger.info("- Password field gone, login form submitted successfully")

    # Check if account is protected with Two Factor Authentication
    two_factor_authentication(browser, logger, security_codes)

    # Dismiss following features if availables
    dismiss_get_app_offer(browser, logger)
    dismiss_notification_offer(browser, logger)
    dismiss_save_information(browser, logger)

    # IG: "Accept cookies from Instagram on this browser?"
    # They said that they're using cookies to help to personalize the content,
    # server relevant ads and provide safer experience.
    accept_igcookie_dialogue(browser, logger)

    # check for login error messages and display it in the logs
    if "instagram.com/challenge" in browser.current_url:
        # check if account is disabled by Instagram,
        # or there is an active challenge to solve
        try:
            account_disabled = browser.find_element(
                By.XPATH, read_xpath(login_user.__name__, "account_disabled")
            )
            logger.warning(account_disabled.text)
            update_activity(
                browser,
                action=None,
                state=account_disabled.text,
                logfolder=logfolder,
                logger=logger,
            )
            return False
        except NoSuchElementException:
            pass

        # in case the user doesnt have a phone number linked to the Instagram account
        try:
            browser.find_element(
                By.XPATH, read_xpath(login_user.__name__, "add_phone_number")
            )
            challenge_warn_msg = (
                "Instagram initiated a challenge before allow your account to login. "
                "At the moment there isn't a phone number linked to your Instagram "
                "account. Plefase, add a phone number to your account, and try again."
            )
            logger.warning(challenge_warn_msg)
            update_activity(
                browser,
                action=None,
                state=challenge_warn_msg,
                logfolder=logfolder,
                logger=logger,
            )
            return False
        except NoSuchElementException:
            pass

        # try to initiate security code challenge
        try:
            browser.find_element(
                By.XPATH, read_xpath(login_user.__name__, "suspicious_login_attempt")
            )
            update_activity(
                browser,
                action=None,
                state="Trying to solve suspicious attempt login",
                logfolder=logfolder,
                logger=logger,
            )
            bypass_suspicious_login(browser, logger, logfolder, security_code_to_phone)
        except NoSuchElementException:
            pass

    # check for wrong username or password message, and show it to the user
    try:
        error_alert = browser.find_element(
            By.XPATH, read_xpath(login_user.__name__, "error_alert")
        )
        logger.warning(error_alert.text)
        update_activity(
            browser,
            action=None,
            state=error_alert.text,
            logfolder=logfolder,
            logger=logger,
        )
        return False
    except NoSuchElementException:
        pass

    if "instagram.com/accounts/onetap" in browser.current_url:
        browser.get(ig_homepage)

    # wait until page fully load
    explicit_wait(browser, "PFL", [], logger, 10)

    # Check if user is logged-in using multiple strategies
    # Instagram's modern layout may not have a traditional <nav> element
    logged_in = False

    # Strategy 1: Check URL - if we're no longer on /accounts/login, likely logged in
    current_url = browser.current_url
    if (
        "/accounts/login" not in current_url
        and "/challenge" not in current_url
        and "instagram.com" in current_url
    ):
        logged_in = True
        logger.info("- Login detected via URL change: {}".format(current_url))

    # Strategy 2: Look for nav element (classic Instagram)
    if not logged_in:
        try:
            nav = browser.find_element(By.XPATH, "//nav")
            if nav is not None:
                logged_in = True
                logger.info("- Login detected via nav element")
        except NoSuchElementException:
            pass

    # Strategy 3: Look for Home icon SVG (modern Instagram)
    if not logged_in:
        try:
            browser.find_element(
                By.XPATH,
                "//*[local-name()='svg' and (@aria-label='Home' or @aria-label='home')]"
            )
            logged_in = True
            logger.info("- Login detected via Home icon SVG")
        except NoSuchElementException:
            pass

    # Strategy 4: Look for profile link or avatar in navigation
    if not logged_in:
        try:
            browser.find_element(
                By.XPATH,
                "//a[contains(@href,'/{}/')]".format(username)
            )
            logged_in = True
            logger.info("- Login detected via profile link")
        except NoSuchElementException:
            pass

    # Strategy 5: Check for role="navigation" attribute (Bloks framework)
    if not logged_in:
        try:
            browser.find_element(By.XPATH, "//*[@role='navigation']")
            logged_in = True
            logger.info("- Login detected via role='navigation' element")
        except NoSuchElementException:
            pass

    if logged_in:
        # create cookie for username and save it
        cookies_list = browser.get_cookies()

        for cookie in cookies_list:
            if "sameSite" in cookie and cookie["sameSite"] == "None":
                cookie["sameSite"] = "Strict"

        try:
            # Open the cookie file to store the data
            with open(cookie_file, "wb") as cookie_f_handler:
                pickle.dump(cookies_list, cookie_f_handler)

        except pickle.PicklingError:
            # Next time, cookie will be created for the session so we are safe
            logger.warning("- Browser cookie list could not be saved to your local...")

        finally:
            return True

    else:
        return False


def dismiss_get_app_offer(browser, logger):
    """Dismiss 'Get the Instagram App' page after a fresh login"""
    offer_elem = read_xpath(dismiss_get_app_offer.__name__, "offer_elem")
    dismiss_elem = read_xpath(dismiss_get_app_offer.__name__, "dismiss_elem")

    # wait a bit and see if the 'Get App' offer rises up
    offer_loaded = explicit_wait(
        browser, "VOEL", [offer_elem, "XPath"], logger, 5, False
    )

    if offer_loaded:
        dismiss_elem = browser.find_element(By.XPATH, dismiss_elem)
        click_element(browser, dismiss_elem)


def dismiss_notification_offer(browser, logger):
    """Dismiss 'Turn on Notifications' offer on session start"""
    offer_elem_loc = read_xpath(dismiss_notification_offer.__name__, "offer_elem_loc")
    dismiss_elem_loc = read_xpath(
        dismiss_notification_offer.__name__, "dismiss_elem_loc"
    )

    # wait a bit and see if the 'Turn on Notifications' offer rises up
    offer_loaded = explicit_wait(
        browser, "VOEL", [offer_elem_loc, "XPath"], logger, 4, False
    )

    if offer_loaded:
        dismiss_elem = browser.find_element(By.XPATH, dismiss_elem_loc)
        click_element(browser, dismiss_elem)


def dismiss_save_information(browser, logger):
    """Dismiss 'Save Your Login Info?' offer on session start"""
    # This question occurs when pkl doesn't exist
    offer_elem_loc = read_xpath(dismiss_save_information.__name__, "offer_elem_loc")
    dismiss_elem_loc = read_xpath(dismiss_save_information.__name__, "dismiss_elem_loc")

    offer_loaded = explicit_wait(
        browser, "VOEL", [offer_elem_loc, "XPath"], logger, 4, False
    )

    if offer_loaded:
        # When prompted chose "Not Now", we don't know if saving information
        # contributes or stimulate IG to target the acct, it would be better to
        # just pretend that we are using IG in different browsers.
        logger.info("- Do not save Login Info by now...")
        dismiss_elem = browser.find_element(By.XPATH, dismiss_elem_loc)
        click_element(browser, dismiss_elem)


def dismiss_this_was_me(browser):
    try:
        # click on "This was me" button if challenge page was called
        this_was_me_button = browser.find_element(
            By.XPATH, read_xpath(dismiss_this_was_me.__name__, "this_was_me_button")
        )
        (ActionChains(browser).move_to_element(this_was_me_button).click().perform())
        # update server calls
        update_activity(browser, state=None)
    except NoSuchElementException:
        # no verification needed
        pass


def two_factor_authentication(browser, logger, security_codes):
    """
    Check if account is protected with Two Factor Authentication codes

    Args:
        :browser: Web driver
        :logger: Library to log actions
        :security_codes: List of Two Factor Authentication codes, also named as Recovery Codes.

    Returns: None
    """

    # Wait until page is loaded after user and password were introduced
    sleep(random.randint(3, 5))

    # Detect 2FA page via URL or page content
    is_two_factor_page = False
    current_url = browser.current_url
    
    if "two_factor" in current_url:
        is_two_factor_page = True
    else:
        # Check page content for 2FA indicators (modern Instagram)
        try:
            browser.find_element(
                By.XPATH,
                "//*[contains(text(),'authentication app') or contains(text(),'security code') or contains(text(),'6-digit code') or contains(text(),'two-factor')]"
            )
            is_two_factor_page = True
        except NoSuchElementException:
            pass
        
        # Also check for a code input field with no password field (likely 2FA)
        if not is_two_factor_page:
            try:
                browser.find_element(
                    By.XPATH,
                    "//input[contains(@placeholder,'Code') or contains(@placeholder,'code') or @name='verificationCode']"
                )
                is_two_factor_page = True
            except NoSuchElementException:
                pass

    if is_two_factor_page:

        logger.info("- Two Factor Authentication is enabled...")
        logger.info("- Current URL: {}".format(browser.current_url))

        # Chose one code from the security_codes list
        # 0000 is used if no codes were provided in constructor.
        code = random.choice(security_codes)
        logger.info("- Using security code from provided list...")

        # Check if backup codes button is visible
        # On modern Instagram, this may appear as "Try another way" link
        try:
            # Try: "Try another way" link/button (new Instagram 2FA page)
            m2a_elem = browser.find_element(
                By.XPATH, 
                "//*[contains(text(),'Try another way') or contains(text(),'try another way')]"
            )
            logger.info("- Found 'Try another way' option, clicking...")
            (ActionChains(browser).move_to_element(m2a_elem).click().perform())
            sleep(3)
            
            # After clicking "Try another way", a choice page appears with radio options.
            # We need to select "Backup code" and then click "Continue"
            backup_selected = False
            
            # Try clicking the "Backup code" radio/row by its container or label
            try:
                # Click on the row/container that contains "Backup code" text
                backup_option = browser.find_element(
                    By.XPATH,
                    "//*[contains(text(),'Backup code') or contains(text(),'backup code')]//ancestor::div[@role='button' or @role='radio' or @tabindex]"
                )
                logger.info("- Found backup code option (ancestor container), clicking...")
                (ActionChains(browser).move_to_element(backup_option).click().perform())
                backup_selected = True
            except (NoSuchElementException, Exception):
                pass
            
            if not backup_selected:
                try:
                    # Try clicking the radio circle/input near "Backup code"
                    backup_option = browser.find_element(
                        By.XPATH,
                        "//input[@type='radio'][following-sibling::*[contains(text(),'Backup')] or preceding-sibling::*[contains(text(),'Backup')]]"
                    )
                    logger.info("- Found backup code radio input, clicking...")
                    browser.execute_script("arguments[0].click();", backup_option)
                    backup_selected = True
                except (NoSuchElementException, Exception):
                    pass
            
            if not backup_selected:
                try:
                    # Try: click the parent div/label of the "Backup code" text using JS
                    backup_text = browser.find_element(
                        By.XPATH,
                        "//*[contains(text(),'Backup code') or contains(text(),'backup code')]"
                    )
                    # Click the parent element which is likely the clickable row
                    parent = backup_text.find_element(By.XPATH, "./..")
                    logger.info("- Found backup code text, clicking parent element...")
                    browser.execute_script("arguments[0].click();", parent)
                    backup_selected = True
                except (NoSuchElementException, Exception):
                    pass

            if not backup_selected:
                try:
                    # Last resort: find all clickable rows and click the second one
                    # (first is "Authentication app", second is "Backup code")
                    rows = browser.find_elements(
                        By.XPATH,
                        "//div[@role='button'] | //div[@tabindex='0'] | //label"
                    )
                    if len(rows) >= 2:
                        logger.info("- Clicking second option row (backup code)...")
                        browser.execute_script("arguments[0].click();", rows[1])
                        backup_selected = True
                except (NoSuchElementException, Exception):
                    pass

            if backup_selected:
                logger.info("- Backup code option selected, clicking Continue...")
                sleep(1)
                # Click "Continue" button
                try:
                    continue_btn = browser.find_element(
                        By.XPATH,
                        "//button[contains(text(),'Continue') or contains(text(),'continue')] | //div[@role='button'][contains(.,'Continue')]"
                    )
                    (ActionChains(browser).move_to_element(continue_btn).click().perform())
                    sleep(3)
                    logger.info("- Continue button clicked, waiting for code input page...")
                except NoSuchElementException:
                    logger.warning("- Continue button not found, trying ENTER...")
                    from selenium.webdriver.common.keys import Keys as K
                    ActionChains(browser).send_keys(K.ENTER).perform()
                    sleep(3)
            else:
                logger.warning("- Could not select backup code option")

        except NoSuchElementException:
            # Try legacy backup codes button
            try:
                m2a_elem = browser.find_element(
                    By.XPATH, 
                    "//button[contains(text(),'backup codes') or contains(text(),'Backup codes') or contains(text(),'recovery code')]"
                )
                logger.info("- Found legacy backup codes button, clicking...")
                m2a_elem.click()
                sleep(2)
            except NoSuchElementException:
                logger.info("- No backup codes button found - will try direct code entry")

        try:
            # Check Security code is numeric
            int(code)

            # Try to find the verification code input field with multiple selectors
            security_code = None
            
            # Try 1: Standard name='verificationCode'
            verification_code_xp = read_xpath(login_user.__name__, "verification_code")
            try:
                explicit_wait(browser, "VOEL", [verification_code_xp, "XPath"], logger, 10)
                security_code = browser.find_element(By.XPATH, verification_code_xp)
                logger.info("- Found verification code input by name='verificationCode'")
            except Exception:
                pass
            
            # Try 2: Input with placeholder 'Code' (new Instagram 2FA page)
            if security_code is None:
                try:
                    security_code = browser.find_element(
                        By.XPATH,
                        "//input[contains(@placeholder,'Code') or contains(@placeholder,'code') or contains(@aria-label,'Code') or contains(@aria-label,'code')]"
                    )
                    logger.info("- Found verification code input by placeholder/aria-label 'Code'")
                except NoSuchElementException:
                    pass
            
            # Try 3: Any text input on the 2FA page (usually there's only one)
            if security_code is None:
                try:
                    security_code = browser.find_element(
                        By.XPATH,
                        "//input[@type='text' or @type='number' or @type='tel']"
                    )
                    logger.info("- Found verification code input by type (text/number/tel)")
                except NoSuchElementException:
                    pass

            if security_code is None:
                raise NoSuchElementException("Could not find verification code input field")
            
            logger.info("- Found verification code input field...")

            (
                ActionChains(browser)
                .move_to_element(security_code)
                .click()
                .send_keys(code)
                .perform()
            )
            
            logger.info("- Entered security code: {}".format(code))

            sleep(random.randint(1, 3))

            # Find and click the confirm/continue button
            confirm = None
            confirm_xp = read_xpath(login_user.__name__, "confirm")
            try:
                confirm = browser.find_element(By.XPATH, confirm_xp)
                logger.info("- Found confirm button by xpath")
            except NoSuchElementException:
                pass
            
            # Try: div[role='button'] with Continue text (Bloks framework)
            if confirm is None:
                try:
                    confirm = browser.find_element(
                        By.XPATH,
                        "//div[@role='button'][contains(.,'Continue') or contains(.,'Confirm')]"
                    )
                    logger.info("- Found confirm button (Bloks div[role='button'])")
                except NoSuchElementException:
                    pass
            
            # Try: any button on the page
            if confirm is None:
                try:
                    confirm = browser.find_element(
                        By.XPATH,
                        "//button[not(contains(text(),'another way')) and not(contains(text(),'Back'))]"
                    )
                    logger.info("- Found confirm button (generic button)")
                except NoSuchElementException:
                    pass

            if confirm is not None:
                logger.info("- Clicking confirm/continue button...")
                (
                    ActionChains(browser)
                    .move_to_element(confirm)
                    .click()
                    .perform()
                )
            else:
                # Fallback: press ENTER on the code field
                logger.info("- No confirm button found, pressing ENTER...")
                (
                    ActionChains(browser)
                    .move_to_element(security_code)
                    .send_keys(Keys.ENTER)
                    .perform()
                )
            
            logger.info("- 2FA code submitted...")

            # update server calls for both 'click' and 'send_keys' actions
            for _ in range(2):
                update_activity(browser, state=None)

            sleep(random.randint(1, 3))
            
            logger.info("- Two Factor Authentication completed successfully")

        except NoSuchElementException as e:
            # Unable to login to Instagram!
            logger.warning(
                "- Security code field not found! Instagram may have changed the 2FA page structure.\n\t{}".format(
                    str(e).encode("utf-8")
                )
            )
            logger.warning("- You may need to manually enter the code or update the XPath selectors")
        except ValueError:
            # Unable to login to Instagram!
            logger.warning("- Security code provided is not a number")
            # Unable to login to Instagram!
            logger.warning("- Security code provided is not a number")
    else:
        # Two Factor Authentication is not enabled or the security code has
        # already been entered in previous session.
        # Return None and login to Instagram!
        return


def accept_igcookie_dialogue(browser, logger):
    """Presses 'Accept' button on IG cookie dialogue"""

    offer_elem_loc = read_xpath(accept_igcookie_dialogue.__name__, "accept_button")

    offer_loaded = explicit_wait(
        browser, "VOEL", [offer_elem_loc, "XPath"], logger, 4, False
    )

    if offer_loaded:
        logger.info("- Accepted IG cookies by default...")
        accept_elem = browser.find_element(By.XPATH, offer_elem_loc)
        click_element(browser, accept_elem)
