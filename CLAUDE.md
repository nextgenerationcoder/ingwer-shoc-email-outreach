# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two related but independent outreach systems sharing the same AI email-generation code and DB:

1. **`email_pipeline/`** — cold *email* outreach. Imports contacts, assigns each permanently to one of 4 mailboxes, queues ~10-15 sends/mailbox/day at random times in a send window, and sends via SMTP.
2. **`form_outreach/` + `chrome-extension/`** — cold outreach via a shop's own *contact form* instead of email (crawls the site, finds/fills the form, submits or flags for manual review). The webapp's headless version of this lives in `email_pipeline/form_crawler.py` and is driven from the webapp's `/forms` page; `form_outreach/crawl.py` and the Chrome extension are two alternative ways to run the *same* crawl logic when the headless path gets stuck (CAPTCHA, bot detection).

Both flows call `email_pipeline/generate.py` to write the actual email/message text via the DeepSeek API (OpenAI-compatible client), and both are configured through the same `email_pipeline/config.py` + the `settings` table (see "Settings precedence" below).

**Important deployment note:** this exact checkout (`/opt/ingwer-shoc-email-outreach`) is what `docker compose build` actually builds from (see `/root/n8n-compose/compose.yaml`'s `emailmarketing` service, `build: /opt/ingwer-shoc-email-outreach`) and serves at `emailmarketing.amirseyti.de`. There may be other clones of this repo elsewhere on this host that are *not* kept up to date — before assuming "the deployed code", confirm you're editing this one, and that this one's git remote (`github.com/nextgenerationcoder/ingwer-shoc-email-outreach`) matches what you intend to push.

## Commands

No test suite exists in this repo — there is a manual "Test & Preview" page in the webapp instead (`/test`), which generates (and optionally sends) a sample email without touching real contacts/the DB.

```bash
# Install
pip install -r email_pipeline/requirements.txt   # Flask is only needed for the web UI
playwright install --with-deps chromium            # needed by form_crawler.py (used by both the webapp's /forms page and form_outreach/crawl.py)

# Run the webapp locally (also self-schedules the daily plan + per-minute worker — see Architecture)
python -m email_pipeline.webapp.app
# -> http://localhost:5000

# One-off CLI runs, instead of waiting for cron/the scheduler
python -m email_pipeline.import_contacts contacts.csv   # email column required; name, company, website, source optional
python -m email_pipeline.plan_daily                      # idempotent per calendar day (checks plan_log)
python -m email_pipeline.worker                           # sends whatever in send_queue is due now
python -m email_pipeline.unsubscribe someone@example.com

# Interactive (headed-browser) form-outreach crawl, as an alternative to the webapp's headless /forms page
pip install -r form_outreach/requirements.txt
playwright install chromium
python -m form_outreach.crawl targets.csv   # url column required; name, company optional

# Production
docker build -t emailmarketing .
gunicorn -w 1 -b 0.0.0.0:8000 email_pipeline.wsgi:app   # -w 1 is load-bearing, see Architecture
```

To redeploy the live instance: rebuild/restart the `emailmarketing` service from `/root/n8n-compose/compose.yaml` (`docker compose build emailmarketing && docker compose up -d emailmarketing`). The DB lives in the `email_data` volume at `/data/pipeline.db` inside the container, so it survives rebuilds.

## Architecture

**Self-scheduling webapp, not external cron.** `email_pipeline/webapp/scheduler.py` starts an in-process APScheduler on import (`start_scheduler()`, called at module load in `webapp/app.py`) that runs `plan_daily.plan_today()` once a day at 00:05 and `worker.run_due_sends()` every minute. As long as the webapp process is up, the campaign runs itself — there's no separate crontab to maintain in production. **This is why the app must run with exactly one worker process** (`gunicorn -w 1`): each worker would start its own copy of the scheduler, causing duplicate/early sends. `plan_daily`'s own idempotency (checking `plan_log`) is a second safety net, not a substitute for the single-worker rule.

**Settings precedence: DB over env vars, always.** Every config value in `config.py` is read through `_setting(key, env_key, default)`, which checks the `settings` table first and only falls back to the environment variable if the DB has nothing. This means editing a value in the web UI (Settings, AI Prompt, etc.) takes effect on the *next* scheduled job or request with no restart — but it also means **a value saved in the DB silently shadows the env var and the code's own default forever**, until changed again in the UI or explicitly cleared in the DB. When a setting doesn't seem to be taking effect after a code change (e.g. a new default prompt), check `SELECT value FROM settings WHERE key = ...` before assuming the code is wrong.

**Mailbox assignment is a one-way hash, not a lookup.** `assign.py`'s `assign_mailbox(email)` hashes the email address to deterministically and *permanently* pick 1 of 4 mailboxes — there's no stored assignment to look up or change; the same email always re-derives the same mailbox. This is the mechanism guaranteeing a contact is only ever emailed from one mailbox.

**Contact lifecycle is a one-way status chain**, enforced by application code, not by the schema: `pending → queued → sent`, or `→ unsubscribed` / `→ failed` (after `MAX_SEND_ATTEMPTS`). `plan_daily` only ever selects `status = 'pending'`, so a contact can't be re-queued once sent, and `worker.py` re-checks `unsubscribed` status at send time (not just at queue time) as a last-second safety net before actually sending.

**AI generation is one shared function for both flows.** `generate.py`'s `generate_from_fields()` takes a `prompt_template` override (or falls back to `DEFAULT_PROMPT_TEMPLATE`), a `website`, and the usual name/company fields, formats the prompt (`_SafePlaceholders` leaves an unrecognized `{placeholder}` untouched instead of raising, since a custom prompt saved via the UI might reference a placeholder that doesn't apply to the current flow), calls DeepSeek, and parses the response with `_parse_subject_body()`. That parser works by locating the literal `Subject:`/`Body:` markers in the raw text (not by checking each line in isolation) and converts a literal `\n` into a real newline — this is deliberate: the model doesn't reliably follow a "use \n for line breaks" instruction literally, and a naive per-line parse would silently truncate a multi-paragraph body to its first line whenever the model used real newlines instead.

**Website research is a real fetch, not the model "browsing".** DeepSeek's plain chat-completion call has no tool/browsing capability, so when the active prompt references `{website_content}`, `generate_from_fields()` calls `website_research.fetch_website_text()` first — a short-lived headless Chromium page load (reusing the same Playwright dependency `form_crawler.py` needs) that extracts visible page text as grounding context, never raising on failure (a down/blocking site just yields an empty string, which the default prompt is written to handle by keeping personalization general rather than inventing details). The fetch is skipped entirely if the active prompt template doesn't contain `{website_content}`, so a customized prompt that doesn't use it never pays for the browser launch.

**Two outreach flows, one shared crawler module.** `form_crawler.py`'s form-finding/filling/CAPTCHA-detection logic (`find_best_form`, `fill_form`, `has_captcha`, etc.) is called both headlessly from the webapp (`/forms` routes in `webapp/app.py`) and interactively from `form_outreach/crawl.py` (opens a real visible browser window and waits for you to solve a CAPTCHA by hand) — if you change form-filling behavior, both call sites need to keep working, not just one.

**A target URL can be a general website or an exact contact-form page — `form_targets.is_contact_page` says which.** By default, `crawl_target_headless()` tries the given URL directly, and only falls back to hunting for a "Kontakt"/"Contact" nav link (`find_contact_page`) if there's no form right there. When `is_contact_page` is set (via the `/forms` checkbox, a per-row CSV column, or the API/extension), that fallback search is skipped entirely — no form on the given page means a clean failure, not a wander to a different page that might be the wrong one (a newsletter signup, a stale contact page, etc.). `form_outreach/crawl.py` mirrors this same flag independently (it doesn't call `crawl_target_headless` — it re-implements the same find→fill→submit sequence for its own visible-browser flow), so a change to one side's "should we search for a contact page" logic needs the same change on the other.

**The Chrome extension talks to the webapp over a small JSON API**, not the DB directly: `webapp/app.py`'s `/api/forms/*` routes, gated by a shared-secret `X-API-Key` header (`config.get_extension_api_key()`, auto-generated on first use and shown in Settings). This lets someone work through the `form_targets` queue by hand in their own browser while staying in sync with whatever the headless crawler already did.
