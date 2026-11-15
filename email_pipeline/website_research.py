"""
Fetches a shop's website and extracts plain, readable text, to ground the
outreach email's personalization in something real -- a bare chat-
completion call to DeepSeek has no way to actually browse a URL itself, so
this does the "visiting" server-side (see generate.py's RESEARCH step) and
hands the model what it found as context instead.

Reuses the same Playwright + Chromium this image already installs for the
form-outreach crawler (see form_crawler.py) rather than adding a second
fetch mechanism or a new dependency.

Never raises -- a site that's down, blocks bots, or times out just means
an empty research string, which generate.py is written to handle (tell the
model to keep the personal-connection part brief and general instead of
inventing specifics), not a failed email generation.
"""
import re

MAX_CONTENT_CHARS = 3000
FETCH_TIMEOUT_MS = 15000


def _clean_text(text: str) -> str:
    # Playwright's inner_text() already strips tags, but a typical shop
    # page still leaves behind a lot of layout whitespace -- collapse runs
    # of spaces/blank lines so the model's context budget goes to actual
    # content, not formatting.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_website_text(url: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Returns up to `max_chars` of the page's visible text (title + body),
    or "" if the URL is empty or the fetch fails for any reason."""
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=FETCH_TIMEOUT_MS)
                page.wait_for_timeout(1000)  # let obvious JS-rendered content settle
                title = page.title() or ""
                body_text = page.inner_text("body")
                return _clean_text(f"{title}\n\n{body_text}")[:max_chars]
            finally:
                browser.close()
    except Exception:
        return ""
