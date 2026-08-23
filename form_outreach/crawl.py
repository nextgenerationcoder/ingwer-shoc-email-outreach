"""
Local (not server-deployed) contact-form outreach crawler.

For each shop URL in a CSV, finds their contact form, fills it in with an
AI-generated inquiry (same prompt style as the email pipeline), and:
  - auto-submits immediately if no CAPTCHA is blocking it
  - opens a visible browser window and waits for you to solve the CAPTCHA
    and click submit yourself, if one is present

Run:
    pip install -r form_outreach/requirements.txt
    playwright install chromium
    python -m form_outreach.crawl targets.csv

targets.csv columns: url (required), name, company (optional, used for
personalization -- same fields as email_pipeline's contacts.csv).
"""
import csv
import random
import re
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, "..")
from email_pipeline import config
from email_pipeline.generate import generate_from_fields

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


def fill_and_submit(page, sender_name: str, sender_email: str, subject: str, body: str) -> bool:
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

    if not filled_any:
        return False

    if has_captcha(page):
        print("  CAPTCHA detected -- browser window is open, solve it and")
        print("  click Submit yourself. Waiting up to 5 minutes...")
        try:
            page.wait_for_event("close", timeout=5 * 60 * 1000)
        except PWTimeout:
            print("  Timed out waiting -- moving on (submit it manually if you still want to).")
        return True

    submit_button = form.locator('button[type="submit"], input[type="submit"]').first
    if submit_button.count() == 0:
        submit_button = form.locator("button").first
    if submit_button.count() == 0:
        return False
    submit_button.click()
    return True


def run(targets_csv: str):
    sender_company = config.get_sender_company_name()
    product_description = config.get_product_description()
    api_key = config.get_deepseek_api_key()

    with open(targets_csv, newline="") as f:
        rows = list(csv.DictReader(f))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        for row in rows:
            url = row["url"].strip()
            name = row.get("name", "")
            company = row.get("company", "")
            print(f"\n=== {company or url} ({url}) ===")

            page = browser.new_page()
            try:
                page.goto(url, timeout=30000)
                contact_url = find_contact_page(page, url)
                if contact_url != url:
                    page.goto(contact_url, timeout=30000)

                subject, body = generate_from_fields(
                    name=name,
                    company=company,
                    sender_company=sender_company,
                    product_description=product_description,
                    api_key=api_key,
                )

                ok = fill_and_submit(
                    page,
                    sender_name=sender_company,
                    sender_email=config.get_contact_reply_email(),
                    subject=subject,
                    body=body,
                )
                print("  submitted" if ok else "  could not find/fill a form -- skipping")
            except Exception as exc:
                print(f"  failed: {exc}")
            finally:
                page.close()

            time.sleep(random.uniform(5, 15))

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m form_outreach.crawl targets.csv")
        sys.exit(1)
    run(sys.argv[1])
