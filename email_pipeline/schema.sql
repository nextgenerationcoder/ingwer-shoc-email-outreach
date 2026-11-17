-- Email outreach pipeline schema (SQLite)

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    company TEXT,
    -- the shop's own website, fetched server-side (website_research.py)
    -- and handed to the AI as research context -- see generate.py's
    -- RESEARCH step. Optional: a contact imported without one just gets
    -- a more general, less-personalized email.
    website TEXT,
    source TEXT,
    -- pending: never contacted, eligible to be queued
    -- queued: picked for today's batch, waiting to be sent
    -- sent: already emailed, must never be queued again
    -- unsubscribed: must never be emailed again
    -- failed: exceeded retry attempts, must never be queued again
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'queued', 'sent', 'unsubscribed', 'failed')),
    -- fixed for the contact's lifetime: which of the 4 mailboxes is allowed
    -- to email them. This is what guarantees rule (a): only one mailbox
    -- will ever be permitted to contact a given customer.
    assigned_mailbox_id INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_contacted_at TEXT
);

CREATE TABLE IF NOT EXISTS send_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- one row per contact per queueing attempt; a contact can only have
    -- one non-terminal queue row at a time (enforced in application code)
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    mailbox_id INTEGER NOT NULL,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'sent', 'failed')),
    subject TEXT,
    body TEXT,
    sent_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_queue_due
    ON send_queue (status, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_contacts_status_mailbox
    ON contacts (status, assigned_mailbox_id);

-- one row per calendar day the planner has already run for, so
-- plan_daily.py never double-plans if the cron fires twice
CREATE TABLE IF NOT EXISTS plan_log (
    plan_date TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- key/value store for settings entered through the web UI (API keys,
-- mailbox SMTP credentials, sender info, prompt override). Values here
-- take priority over the equivalent environment variable, see config.py.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- shop websites for the contact-form outreach feature (separate from
-- the email contacts table -- no daily cap/scheduling here, since there's
-- no mailbox-reputation reason to spread these out).
CREATE TABLE IF NOT EXISTS form_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    name TEXT,
    company TEXT,
    -- 0 (default): `url` is the shop's general website -- find_contact_page
    --   looks for a "Kontakt"/"Contact" link on it if there's no form
    --   right there already.
    -- 1: `url` IS ALREADY the exact contact-form page -- skip hunting for
    --   a contact link entirely; if there's no form on this exact page,
    --   report a clean failure instead of wandering off to a different
    --   page (see form_crawler.py's crawl_target_headless `is_contact_page`).
    is_contact_page INTEGER NOT NULL DEFAULT 0 CHECK (is_contact_page IN (0, 1)),
    -- pending: not yet crawled
    -- pending_review: form found, filled, and a screenshot taken -- waiting
    --   for you to look it over and click Approve before it's submitted
    -- submitted: form submitted (either auto, or after your approval)
    -- needs_manual: a CAPTCHA was found -- the contact page URL is kept
    --   so you can open it yourself and finish submitting by hand
    -- failed: no contact form could be found/filled
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'pending_review', 'submitted', 'needs_manual', 'failed')),
    contact_url TEXT,
    subject TEXT,
    body TEXT,
    screenshot TEXT,
    -- JSON list of fields on the form we couldn't classify (so nothing
    -- was typed into them) -- e.g. a "Firma"/company field, a dropdown,
    -- a date picker. Surfaced in the UI so you notice and can ask for
    -- support to be added, instead of it silently staying blank.
    unmatched_fields TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);
