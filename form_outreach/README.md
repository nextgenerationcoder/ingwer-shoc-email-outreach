# Contact-form outreach crawler (local, no daily cap)

Runs on your own computer, not the VPS -- this doesn't need to be
always-on like the email pipeline, so there's no scheduler/daily cap
here. Run it whenever you want, for as many shops as you want in one
sitting.

## What it does

For each shop URL:
1. Finds the contact/Kontakt page
2. Finds the contact form and classifies its fields (name, email,
   subject, message) by their labels/placeholders
3. Generates an inquiry email with DeepSeek (same prompt style as the
   email pipeline -- a genuine question, not a pitch)
4. Fills the form in
5. **If no CAPTCHA is present: submits automatically.**
6. **If a CAPTCHA is present: opens a visible browser window and waits
   (up to 5 minutes) for you to solve it and click Submit yourself** --
   this can't be done unattended since most CAPTCHAs are tied to a live
   browser session.
7. Waits a random 5-15 seconds before moving to the next site, so it
   doesn't hammer sites back-to-back and look like a bot.

## Setup

```bash
pip install -r form_outreach/requirements.txt
playwright install chromium
```

Set the same settings the email pipeline uses (either via that webapp's
Settings page if you're pointing this at the same DB, or via env vars):
- `DEEPSEEK_API_KEY`
- `SENDER_COMPANY_NAME`, `PRODUCT_DESCRIPTION`
- `CONTACT_REPLY_EMAIL` -- the address put into the form's "your email"
  field so replies reach you (falls back to mailbox 1's address if unset)

## Run

```bash
cp form_outreach/targets.example.csv targets.csv
# edit targets.csv with real shop URLs
python -m form_outreach.crawl targets.csv
```

## Known limitations

- Form detection is heuristic (matches field names/labels/placeholders
  against keywords) -- unusual or multi-step forms will be skipped, not
  guessed at.
- Sites with bot-detection (e.g. Cloudflare challenge pages) may block
  the crawler before it even reaches the form.
- No reply tracking -- unlike the email pipeline, there's no inbox to
  check responses in. You'd need to check each shop's reply manually.
