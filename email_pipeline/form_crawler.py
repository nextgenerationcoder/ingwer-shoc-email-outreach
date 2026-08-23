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
    "message": ["message", "nachricht", "comment", "anliegen", "inquiry"],
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
    for selector in CAPTCHA_SELECTORS:
        if page.locator(selector).count() > 0:
            return True
    return False


def fill_form(page, sender_name: str, sender_email: str, subject: str, body: str) -> bool:
    """Fills any recognized fields in the page's first form. Returns
    whether at least one field was filled."""
    form = page.locator("form").first
    if form.count() == 0:
        return False

    filled_any = False
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
                label = page.locator(f'label[for="{field_id}"]')
                if label.count() > 0:
                    label_text = label.first.inner_text()

            category = classify_field(name, field_id, placeholder, label_text)
            value = {
                "email": sender_email,
                "name": sender_name,
                "subject": subject,
                "message": body,
            }.get(category)
            if value:
                field.fill(value)
                filled_any = True
        except Exception:
            continue

    return filled_any


def submit_form(page) -> bool:
    form = page.locator("form").first
    submit_button = form.locator('button[type="submit"], input[type="submit"]').first
    if submit_button.count() == 0:
        submit_button = form.locator("button").first
    if submit_button.count() == 0:
        return False
    submit_button.click()
    return True


def crawl_target_headless(browser, url: str, name: str, company: str, sender_name: str,
                           sender_email: str, subject: str, body: str) -> dict:
    """Headless crawl for one shop: finds the contact form, fills it, and
    auto-submits if there's no CAPTCHA. If there is one, stops short and
    returns the contact page URL for you to finish by hand -- a server
    process has no screen to solve a CAPTCHA on."""
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

        if has_captcha(page):
            return {"status": "needs_manual", "contact_url": contact_url, "error": None}

        if submit_form(page):
            return {"status": "submitted", "contact_url": contact_url, "error": None}
        return {"status": "failed", "contact_url": contact_url, "error": "no submit button found"}
    except Exception as exc:
        return {"status": "failed", "contact_url": url, "error": str(exc)}
    finally:
        page.close()
