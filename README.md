# Email outreach pipeline

Plain Python + SQLite, no external workflow tool required.

## How the 4 rules are enforced

- **(a) no two mailboxes email the same customer** — `assign.py` hashes each
  email address to permanently pick one of the 4 mailboxes at import time.
  That assignment never changes, so only one mailbox is ever allowed to
  contact a given contact.
- **(b) no customer receives an email twice** — a contact's `status` moves
  `pending -> queued -> sent` and stays `sent` forever; the planner only
  ever selects `status = 'pending'` contacts, so a `sent` contact can never
  be queued again.
- **(c) ~10-15 emails/day per mailbox at random hours** — `plan_daily.py`
  picks `random.randint(10, 15)` contacts per mailbox and gives each a
  random timestamp inside the send window (default 08:00-21:00).
- **(d) triggers every day** — handled two ways depending on how you run
  it: the webapp (see below) schedules itself internally once it's
  deployed and running, or `plan_daily.py` can be run once daily via
  external cron instead. Either way it's idempotent (checks `plan_log`)
  so re-running it the same day is a no-op.

## Files

- `schema.sql` / `db.py` — SQLite schema and connection helper.
- `import_contacts.py` — load a CSV of emails into `contacts`.
- `plan_daily.py` — daily job: builds today's send queue.
- `worker.py` — minute-by-minute job: sends anything due, generates the
  text via DeepSeek first.
- `generate.py` — calls the DeepSeek API to write the subject/body.
- `sender.py` — SMTP send.
- `unsubscribe.py` — CLI to permanently opt a contact out.

## Web UI

`webapp/` is a small Flask app to configure and test everything without
touching env vars or the CLI:

```
pip install -r email_pipeline/requirements.txt
python -m email_pipeline.webapp.app
```

Then open http://localhost:5000. It has:

- **Settings** — enter the 4 mailboxes' SMTP credentials and the
  DeepSeek API key; saved to the DB (`settings` table) and used
  immediately by the cron jobs and CLI too, no restart needed.
- **AI Prompt** — view/edit the exact prompt sent to the model, with a
  reset-to-default button.
- **Test & Preview** — generate a sample email for a fake name/company
  with the current prompt/settings, with nothing sent or saved, plus an
  optional "send test email to yourself" button to verify SMTP actually
  works before running anything live.
- **Contacts** — import a CSV, see status per contact, manually
  unsubscribe someone.
- **Dashboard** — counts by status, recent queue activity, and buttons to
  run `plan_daily` / `worker` on demand instead of waiting for cron.

Settings entered in the UI override the environment variables described
below (DB takes priority; env vars are the fallback for headless/cron use).

### Self-triggering (no external cron needed)

The webapp process itself schedules the daily plan (00:05 server time)
and the per-minute send check via APScheduler (`webapp/scheduler.py`) --
as long as the webapp is deployed and running continuously, that's all
that's needed to keep the campaign going. This means:

- **The web process has to stay up 24/7.** If it's not running, nothing
  gets planned or sent that day -- this is not a "visit the page to
  trigger it" setup, it needs to be an always-on deployment (a small VPS,
  or any host that keeps a Python process running, behind your
  subdomain).
- **Run exactly one worker process.** With gunicorn:
  ```
  gunicorn -w 1 -b 0.0.0.0:8000 email_pipeline.wsgi:app
  ```
  `-w 1` matters -- each worker process would run its own copy of the
  scheduler, and more than one means duplicate/early sends. If your host
  wants more concurrency for the web UI itself, keep the scheduler in a
  single dedicated process instead and disable it in the others (not
  set up here since one worker is plenty for this app's traffic).
- **Server timezone matters.** The daily plan and the 08:00-21:00 send
  window run in the server's local timezone, not necessarily yours --
  check `date` on the host, or set the container/VM's timezone
  explicitly, so emails actually go out when you expect.
- Put a reverse proxy (nginx, Caddy, or your host's built-in one) in
  front of gunicorn for TLS on your subdomain; gunicorn itself shouldn't
  be exposed directly to the internet.

If you'd rather not keep a process running yourself, any host that runs
a persistent Python web service works (a small VPS with systemd, Fly.io,
Railway, PythonAnywhere's "always-on task", etc.) -- the requirement is
just "the process doesn't stop", not any specific platform.

## Setup (CLI / cron only, no UI)

1. Install deps: `pip install -r email_pipeline/requirements.txt` (Flask
   is only needed for the web UI; `openai` is used to talk to DeepSeek's
   OpenAI-compatible API for AI-generated text — without a
   `DEEPSEEK_API_KEY` set, a plain template is used instead so the
   pipeline still runs).

2. Set environment variables for your 4 mailboxes (repeat for N=1..4):

   ```
   MAILBOX1_SMTP_HOST=smtp.example.com
   MAILBOX1_SMTP_PORT=587
   MAILBOX1_USERNAME=you1@example.com
   MAILBOX1_PASSWORD=...
   MAILBOX1_FROM_NAME="Your Name"
   ```

   Plus `DEEPSEEK_API_KEY=...` (optional but recommended).

3. Import contacts:

   ```
   python -m email_pipeline.import_contacts contacts.csv
   ```

   CSV needs an `email` column; `name`, `company`, `source` are optional.

4. Cron (edit with `crontab -e`):

   ```
   # plan today's batch once, right after midnight
   5 0 * * * cd /path/to/repo && python -m email_pipeline.plan_daily >> email_pipeline/plan.log 2>&1

   # check every minute for sends that are due
   * * * * * cd /path/to/repo && python -m email_pipeline.worker >> email_pipeline/worker.log 2>&1
   ```

5. To opt someone out at any time:

   ```
   python -m email_pipeline.unsubscribe someone@example.com
   ```

## Known gaps (not handled yet)

- **No SPF/DKIM/DMARC guidance built in** — deliverability depends on the
  sending domains being properly authenticated; the pipeline can't fix
  that for you.
- **No bounce/reply parsing** — "Reply UNSUBSCRIBE" is included in the
  email text, but nothing currently reads mailbox replies to act on it
  automatically; `unsubscribe.py` has to be run manually until an IMAP
  reply-scanner is added.
- **No legal consent tracking** — make sure you have a lawful basis
  (opt-in, existing relationship, etc.) for every address you import;
  the schema has a `source` column for this but doesn't enforce anything.
- **No per-mailbox daily send cap across restarts** — if `worker.py` or
  the box it runs on goes down mid-day, missed sends simply run late
  when it comes back, they aren't dropped or rescheduled to the next day.
- **No open/click tracking** — there's no visibility into whether emails
  are actually landing or being read.
