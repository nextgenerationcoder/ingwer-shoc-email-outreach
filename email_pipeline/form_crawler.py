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
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'div.g-recaptcha',
    'div.h-captcha',
    '[class*="captcha" i]',
    '[id*="captcha" i]',
]

CONTACT_LINK_PATTERN = re.compile(
    r"contact|kontakt|impressum-kontakt", re.IGNORECASE
)

FIELD_KEYWORDS = {
    "email": ["email", "mail", "e-mail"],
    "name": ["name", "vorname", "firstname"],
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


def _classify_fields_in(form) -> list[tuple]:
    """Returns [(field_locator, category), ...] for every recognizable
    field inside the given form locator."""
    matches = []
    inputs = form.locator("input, textarea, select")
    for i in range(inputs.count()):
        field = inputs.nth(i)
        try:
            field_type = (field.get_attribute("type") or "text").lower()
            if field_type in ("submit", "button", "hidden", "checkbox", "radio"):
                continue
            name = field.get_attribute("name") or ""
            field_id = field.get_attribute("id") or ""
            placeholder = field.get_attribute("placeholder") or ""
            label_text = ""
            if field_id:
                label = field.page.locator(f'label[for="{field_id}"]')
                if label.count() > 0:
                    label_text = label.first.inner_text()

            category = classify_field(name, field_id, placeholder, label_text)
            if category:
                matches.append((field, category))
        except Exception:
            continue
    return matches


def find_best_form(page):
    """A real page often has more than one <form> -- a header search box,
    a newsletter signup, a cart form -- before the actual contact form.
    Picking the first one on the page silently fills/submits the wrong
    thing (or finds nothing to fill and gives up). Score every form by
    how many recognizable fields it has and pick the best one, requiring
    at least an email or message field so a lone search/newsletter form
    never wins.

    Returns (form_locator, [(field_locator, category), ...]) or
    (None, []) if nothing on the page looks like a contact form."""
    forms = page.locator("form")
    best_form, best_matches = None, []
    for i in range(forms.count()):
        form = forms.nth(i)
        matches = _classify_fields_in(form)
        categories = {cat for _, cat in matches}
        if "email" not in categories and "message" not in categories:
            continue
        if len(matches) > len(best_matches):
            best_form, best_matches = form, matches
    return best_form, best_matches


def fill_form(page, sender_name: str, sender_email: str, subject: str, body: str) -> bool:
    """Finds the best-matching form on the page (see find_best_form) and
    fills its recognized fields. Returns whether at least one field was
    filled."""
    form, matches = find_best_form(page)
    if form is None:
        return False

    values = {
        "email": sender_email,
        "name": sender_name,
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


def submit_form(page) -> bool:
    form, _ = find_best_form(page)
    if form is None:
        return False
    submit_button = form.locator('button[type="submit"], input[type="submit"]').first
    if submit_button.count() == 0:
        submit_button = form.locator("button").first
    if submit_button.count() == 0:
        return False
    submit_button.click(force=True)
    return True


def crawl_target_headless(browser, url: str, name: str, company: str, sender_name: str,
                           sender_email: str, subject: str, body: str,
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
        contact_url = find_contact_page(page, url)
        if contact_url != url:
            page.goto(contact_url, timeout=30000)
        else:
            contact_url = page.url

        filled = fill_form(page, sender_name, sender_email, subject, body)
        if not filled:
            return {"status": "failed", "contact_url": contact_url, "error": "no form/fields found"}

        if screenshot_path:
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                pass

        if has_captcha(page):
            return {"status": "needs_manual", "contact_url": contact_url, "error": None}

        if dry_run:
            return {"status": "pending_review", "contact_url": contact_url, "error": None}

        if submit_form(page):
            return {"status": "submitted", "contact_url": contact_url, "error": None}
        return {"status": "failed", "contact_url": contact_url, "error": "no submit button found"}
    except Exception as exc:
        return {"status": "failed", "contact_url": url, "error": str(exc)}
    finally:
        page.close()
