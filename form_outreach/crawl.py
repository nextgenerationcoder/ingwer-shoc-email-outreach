"""
Local (not server-deployed) contact-form outreach crawler -- the
interactive version. The webapp has a headless version of the same
crawling logic (see email_pipeline/form_crawler.py + the "Form Outreach"
page) that auto-submits and just records a link for you to finish
CAPTCHA-blocked ones by hand. This script instead opens a visible
browser and waits for you right there, if you'd rather work that way.

Run:
    pip install -r form_outreach/requirements.txt
    playwright install chromium
    python -m form_outreach.crawl targets.csv

targets.csv columns: url (required), name, company (optional, used for
personalization -- same fields as email_pipeline's contacts.csv).
"""
import csv
import random
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, "..")
from email_pipeline import config
from email_pipeline.generate import generate_from_fields
from email_pipeline.form_crawler import (
    find_contact_page,
    find_best_form,
    fill_form,
    submit_form,
    has_captcha,
    dismiss_cookie_banner,
)


def fill_and_submit(page, form, matches, sender_name: str, sender_email: str, subject: str, body: str) -> bool:
    filled = fill_form(form, matches, sender_name, sender_email, subject, body)
    if not filled:
        return False

    if has_captcha(page):
        print("  CAPTCHA detected -- browser window is open, solve it and")
        print("  click Submit yourself. Waiting up to 5 minutes...")
        try:
            page.wait_for_event("close", timeout=5 * 60 * 1000)
        except PWTimeout:
            print("  Timed out waiting -- moving on (submit it manually if you still want to).")
        return True

    return submit_form(form)


def run(targets_csv: str):
    sender_company = config.get_sender_company_name()
    product_description = config.get_product_description()
    api_key = config.get_deepseek_api_key()
    reply_email = config.get_contact_reply_email()

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
                dismiss_cookie_banner(page)

                # use the page you gave us directly if it already has a
                # real contact form -- don't wander off to a "Kontakt"
                # nav link that might point to a different page
                form, matches, unmatched = find_best_form(page)
                if form is None:
                    contact_url = find_contact_page(page, url)
                    if contact_url != url:
                        page.goto(contact_url, timeout=30000)
                        form, matches, unmatched = find_best_form(page)

                if form is None:
                    print("  could not find/fill a form -- skipping")
                    page.close()
                    time.sleep(random.uniform(5, 15))
                    continue

                if unmatched:
                    names = [u.get("label") or u.get("placeholder") or u.get("name") or u.get("id") or "?" for u in unmatched]
                    print(f"  note: {len(unmatched)} field(s) on this form aren't being filled: {', '.join(names)}")

                subject, body = generate_from_fields(
                    name=name,
                    company=company,
                    sender_company=sender_company,
                    product_description=product_description,
                    api_key=api_key,
                )

                ok = fill_and_submit(
                    page,
                    form,
                    matches,
                    sender_name=sender_company,
                    sender_email=reply_email,
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
