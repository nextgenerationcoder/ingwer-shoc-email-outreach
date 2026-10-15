"""
Shared contact-form crawling logic. Used two ways:
  - headless, from the webapp (webapp/app.py) -- auto-submits when there's
    no CAPTCHA, and just records "needs_manual" + the contact page URL
    when there is one, since a server has no screen for you to solve it on
  - headed/interactive, from the local form_outreach/crawl.py script --
    same logic, but opens a real window and waits for you when a CAPTCHA
    is found
"""
import re

CAPTCHA_SELECTORS = [
    # the actual v2 "I'm not a robot" challenge iframe -- Google titles it
    # exactly "reCAPTCHA"; deliberately NOT matching on iframe src alone,
    # since the harmless, always-visible reCAPTCHA v3 corner badge (which
    # never needs solving) is also served from a recaptcha.google.com
    # iframe and would otherwise cause a false "needs you" on every site
    # that merely has background bot-scoring installed.
    'iframe[title="reCAPTCHA"]',
    'iframe[src*="hcaptcha.com/captcha"]',
    'div.g-recaptcha',
    'div.h-captcha',
]

CONTACT_LINK_PATTERN = re.compile(
    r"contact|kontakt|impressum-kontakt", re.IGNORECASE
)

FIELD_KEYWORDS = {
    "email": ["email", "mail", "e-mail"],
    "name": ["name", "vorname", "firstname"],
    "phone": ["phone", "telefon", "mobile", "handy", "fax"],
    "subject": ["subject", "betreff", "topic", "reason"],
    "message": ["message", "nachricht", "comment", "anliegen", "inquiry", "body", "frage"],
}


def find_contact_page(page, base_url: str) -> str:
    links = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => ({href: e.href, text: e.innerText}))"
    )
    for link in links:
        if CONTACT_LINK_PATTERN.search(link.get("text", "")) or CONTACT_LINK_PATTERN.search(
            link.get("href", "")
        ):
            return link["href"]
    return base_url


COOKIE_ACCEPT_TEXTS = [
    "Alle akzeptieren", "Akzeptieren", "Alle Cookies akzeptieren", "Zustimmen",
    "Accept all", "Accept All", "Accept All Cookies", "I Accept", "Accept",
    "Alles akzeptieren", "OK", "Got it", "Verstanden",
]


def dismiss_cookie_banner(page) -> bool:
    """Best-effort: click an "accept" button on a cookie-consent banner if
    one is showing. Purely cosmetic for the preview screenshot -- fill()
    already uses force=True so it works whether or not this succeeds --
    but a banner sitting over the form makes the preview hard to read."""
    for text in COOKIE_ACCEPT_TEXTS:
        try:
            button = page.get_by_role("button", name=text, exact=False).first
            if button.count() == 0 or not button.is_visible():
                continue
            button.click(timeout=2000)
            page.wait_for_timeout(300)
            return True
        except Exception:
            continue
    return False


def classify_field(name: str, field_id: str, placeholder: str, label: str) -> str | None:
    signal = " ".join(filter(None, [name, field_id, placeholder, label])).lower()
    for category, keywords in FIELD_KEYWORDS.items():
        if any(kw in signal for kw in keywords):
            return category
    return None


def has_captcha(page) -> bool:
    """True only if a CAPTCHA is actually visible to a human. Many sites
    (Shopify included) embed an invisible reCAPTCHA v3 badge purely for
    background spam scoring, with no challenge ever shown to the user --
    that shouldn't block an otherwise-normal auto-submit."""
    for selector in CAPTCHA_SELECTORS:
        locator = page.locator(selector)
        for i in range(locator.count()):
            try:
                if locator.nth(i).is_visible():
                    return True
            except Exception:
                continue
    return False


def _classify_fields_in(form) -> tuple[list[tuple], list[dict]]:
    """Returns (matches, unmatched) for every input/textarea/select inside
    the given form locator. matches is [(field_locator, category), ...]
    for fields we know how to fill; unmatched is
    [{"name", "id", "placeholder", "label"}, ...] for every field we
    could NOT classify -- so you can see what's being silently skipped
    (e.g. a "Firma"/company field, a dropdown, a date picker) instead of
    it just staying blank with no indication why."""
    matches = []
    unmatched = []
    inputs = form.locator("input, textarea, select")
    for i in range(inputs.count()):
        field = inputs.nth(i)
        try:
            tag = (field.evaluate("el => el.tagName") or "").lower()
            field_type = (field.get_attribute("type") or "text").lower()
            if field_type in ("submit", "button", "hidden"):
                continue
            name = field.get_attribute("name") or ""
            field_id = field.get_attribute("id") or ""
            placeholder = field.get_attribute("placeholder") or ""
            label_text = ""
            if field_id:
                label = field.page.locator(f'label[for="{field_id}"]')
                if label.count() > 0:
                    label_text = label.first.inner_text()

            if field_type in ("checkbox", "radio"):
                # usually consent/opt-in toggles, not something we'd want
                # to auto-tick -- note them but never auto-fill/check
                unmatched.append({"name": name, "id": field_id, "placeholder": placeholder, "label": label_text, "type": field_type})
                continue

            if tag == "select":
                # a dropdown needs select_option() with the right choice
                # picked, not fill() with arbitrary text -- out of scope
                # for keyword-based text filling, so always report it
                # rather than silently attempting (and failing) a fill()
                unmatched.append({"name": name, "id": field_id, "placeholder": placeholder, "label": label_text, "type": "select"})
                continue

            category = classify_field(name, field_id, placeholder, label_text)
            if category:
                matches.append((field, category))
            else:
                unmatched.append({"name": name, "id": field_id, "placeholder": placeholder, "label": label_text, "type": field_type})
        except Exception:
            continue
    return matches, unmatched


def find_best_form(page):
    """A real page often has more than one <form> -- a header search box,
    a newsletter signup, a cart form -- before the actual contact form.
    Picking the first one on the page silently fills/submits the wrong
    thing (or finds nothing to fill and gives up). Score every form by
    how many recognizable fields it has and pick the best one, requiring
    at least an email or message field so a lone search/newsletter form
    never wins.

    Returns (form_locator, [(field_locator, category), ...], [unmatched
    field dicts]) or (None, [], []) if nothing on the page looks like a
    contact form."""
    forms = page.locator("form")
    best_form, best_matches, best_unmatched = None, [], []
    for i in range(forms.count()):
        form = forms.nth(i)
        matches, unmatched = _classify_fields_in(form)
        categories = {cat for _, cat in matches}
        if "email" not in categories and "message" not in categories:
            continue
        if len(matches) > len(best_matches):
            best_form, best_matches, best_unmatched = form, matches, unmatched
    return best_form, best_matches, best_unmatched


def fill_form(form, matches: list[tuple], sender_name: str, sender_email: str,
              subject: str, body: str, sender_phone: str = "") -> bool:
    """Fills the recognized fields found by find_best_form. Returns
    whether at least one field was filled."""
    values = {
        "email": sender_email,
        "name": sender_name,
        "phone": sender_phone,
        "subject": subject,
        "message": body,
    }
    filled_any = False
    for field, category in matches:
        value = values.get(category)
        if not value:
            continue
        try:
            # force=True: skip Playwright's actionability/overlay check --
            # cookie-consent banners and sticky headers commonly sit over
            # a form without actually blocking it, and failing silently
            # there is worse than a slightly-too-permissive fill.
            field.fill(value, force=True)
            filled_any = True
        except Exception:
            continue

    return filled_any


def submit_form(form) -> bool:
    submit_button = form.locator('button[type="submit"], input[type="submit"]').first
    if submit_button.count() == 0:
        submit_button = form.locator("button").first
    if submit_button.count() == 0:
        return False
    submit_button.click(force=True)
    return True


def crawl_target_headless(browser, url: str, name: str, company: str, sender_name: str,
                           sender_email: str, subject: str, body: str,
                           sender_phone: str = "",
                           dry_run: bool = False, screenshot_path: str | None = None) -> dict:
    """Headless crawl for one shop: finds the contact form and fills it.

    dry_run=False (default): auto-submits if there's no CAPTCHA. If there
    is one, stops short and returns the contact page URL for you to
    finish by hand -- a server process has no screen to solve a CAPTCHA on.

    dry_run=True: fills the form and takes a screenshot (if
    screenshot_path is given) but never clicks submit -- returns
    "pending_review" so the dashboard can show you the filled form before
    you approve it. Call this function again with dry_run=False to
    actually submit once you've approved it (the form gets filled fresh
    each time rather than reusing a kept-open browser tab, since a
    browser session can't be held open across separate web requests)."""
    page = browser.new_page()
    try:
        page.goto(url, timeout=30000)
        dismiss_cookie_banner(page)

        # if the page we're already on has a real contact form, use it --
        # don't go hunting for a "Kontakt" nav link that might point
        # somewhere else (a different/legacy contact page) and wander
        # away from the exact page you gave us.
        form, matches, unmatched = find_best_form(page)
        if form is not None:
            contact_url = page.url
        else:
            contact_url = find_contact_page(page, url)
            if contact_url != url:
                page.goto(contact_url, timeout=30000)
                form, matches, unmatched = find_best_form(page)
            else:
                contact_url = page.url

        if form is None:
            return {"status": "failed", "contact_url": contact_url, "error": "no form/fields found", "unmatched_fields": []}

        filled = fill_form(form, matches, sender_name, sender_email, subject, body, sender_phone)
        if not filled:
            return {"status": "failed", "contact_url": contact_url, "error": "no form/fields found", "unmatched_fields": unmatched}

        if screenshot_path:
            try:
                # scoped to just the form itself, not the whole (often very
                # long) page -- this is what you actually need to see to
                # approve it
                form.screenshot(path=screenshot_path)
            except Exception:
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    pass

        if has_captcha(page):
            return {"status": "needs_manual", "contact_url": contact_url, "error": None, "unmatched_fields": unmatched}

        if dry_run:
            return {"status": "pending_review", "contact_url": contact_url, "error": None, "unmatched_fields": unmatched}

        if submit_form(form):
            return {"status": "submitted", "contact_url": contact_url, "error": None, "unmatched_fields": unmatched}
        return {"status": "failed", "contact_url": contact_url, "error": "no submit button found", "unmatched_fields": unmatched}
    except Exception as exc:
        return {"status": "failed", "contact_url": url, "error": str(exc), "unmatched_fields": []}
    finally:
        page.close()
