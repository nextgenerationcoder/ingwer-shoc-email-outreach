-- Email outreach pipeline schema (SQLite)

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    company TEXT,
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
