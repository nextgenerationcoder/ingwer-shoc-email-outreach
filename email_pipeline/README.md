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
- **(d) triggers every day** — `plan_daily.py` is meant to run once daily via
  cron; it's idempotent (checks `plan_log`) so re-running it the same day
  is a no-op.

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
