# Form Outreach -- Chrome extension

Runs in your real Chrome browser instead of a headless server crawler,
so CAPTCHAs, cookie-wall quirks, and bot-detection mostly stop being a
problem -- you're just... browsing normally. It fills the form; you
look it over and click Submit yourself.

## How it fits with the rest of the pipeline

- The webapp (`/forms`) is for bulk/unattended runs: auto-submits
  wherever there's no CAPTCHA, flags the rest as `needs_manual`.
- This extension is for working through a list by hand, or for sites
  the headless crawler keeps struggling with (bot-protected, JS-heavy,
  CAPTCHA-walled). It talks to the same `form_targets` table through a
  small JSON API, so progress stays in sync either way.

## Install (unpacked, for personal use)

1. Go to `chrome://extensions`
2. Turn on "Developer mode" (top right)
3. Click "Load unpacked" and select this `chrome-extension/` folder

## Set up

1. Click the extension icon once (or right-click it -> Options)
2. Enter your dashboard's web address (e.g.
   `https://emailmarketing.amirseyti.de`) and the API key from the
   dashboard's **Settings** page (under "Chrome extension")
3. Click "Test connection" to confirm it's working

## Use

**Batch mode** (the main flow -- works through your whole pending list):

1. Click the extension icon, then **Start batch**
2. It navigates your current tab to the first pending shop, auto-generates
   the text, and auto-fills the form
3. Review it, solve any CAPTCHA, click Submit yourself on the page
   (this closes the popup -- that's normal)
4. Click the extension icon again -- it remembers where it was and shows
   **Submitted -- next** / **Skip -- next** / **Stop batch**
5. Click **Submitted -- next**: it marks that one done, navigates to the
   next shop, and auto-fills it. Repeat from step 3.

**Single-page mode** (for one site at a time, not working through a list):

1. Navigate to a shop's contact page in a normal tab
2. Click the extension icon -- it looks up whether that site already
   has a target on file (matched by domain); if not, you can add it
   on the spot
3. Click **Generate text**, review/edit it, then **Fill form on page**
4. Review it, solve any CAPTCHA, click Submit yourself on the page
5. Back in the popup, click **Submitted -- next** (or **Skip**)

## Files

- `manifest.json` -- Manifest V3 config
- `popup.html` / `popup.js` -- the main UI; handles both batch mode
  (walks the pending list, driving tab navigation itself via
  `chrome.tabs.update` + `chrome.storage.session` to remember where it
  is across popup open/close) and single-page mode
- `options.html` / `options.js` -- dashboard URL + API key setup
- `fill-logic.js` -- the field-detection/fill logic, injected into the
  page on demand. Deliberately mirrors
  `email_pipeline/form_crawler.py`'s `classify_field`/`find_best_form`/
  `fill_form` (including the cookie-banner dismiss) so a form is filled
  the same way whether the server crawler or this extension does it.
