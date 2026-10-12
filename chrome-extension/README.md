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

1. Navigate to a shop's contact page in a normal tab
2. Click the extension icon -- it looks up whether that site already
   has a target on file (matched by domain); if not, you can add it
   on the spot
3. Click **Generate text** -- same AI prompt as the rest of the
   pipeline, editable before filling
4. Click **Fill form on page** -- fills the real form in your actual
   tab (filled fields get a green outline so you can see what changed)
5. Review it, solve any CAPTCHA, click Submit yourself on the page
6. Back in the popup, click **Mark submitted** (or **Skip** if it
   didn't work out) to record the result on the dashboard

## Files

- `manifest.json` -- Manifest V3 config
- `popup.html` / `popup.js` -- the main UI
- `options.html` / `options.js` -- dashboard URL + API key setup
- `fill-logic.js` -- the field-detection/fill logic, injected into the
  page on demand. Deliberately mirrors
  `email_pipeline/form_crawler.py`'s `classify_field`/`find_best_form`/
  `fill_form` so a form is filled the same way whether the server
  crawler or this extension does it.
