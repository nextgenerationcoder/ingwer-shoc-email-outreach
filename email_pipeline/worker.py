"""
Run every minute (via cron). Sends any queued email whose scheduled time
has arrived: generates the text, sends it through its assigned mailbox,
and marks the contact as sent so it can never be queued again.
"""
from datetime import datetime

from . import config, db
from .generate import generate_email
from .sender import send_email


def run_due_sends() -> None:
    db.init_db()
    mailboxes = {m.id: m for m in config.load_mailboxes()}
    now = datetime.now().isoformat(sep=" ")

    with db.connection() as conn:
        due = conn.execute(
            "SELECT * FROM send_queue WHERE status = 'scheduled' "
            "AND scheduled_at <= ?",
            (now,),
        ).fetchall()

        for item in due:
            contact = conn.execute(
                "SELECT * FROM contacts WHERE id = ?", (item["contact_id"],)
            ).fetchone()

            # Safety net: if the contact was unsubscribed after being
            # queued, never send.
            if contact["status"] == "unsubscribed":
                conn.execute(
                    "UPDATE send_queue SET status = 'failed', "
                    "error = 'contact unsubscribed' WHERE id = ?",
                    (item["id"],),
                )
                continue

            mailbox = mailboxes.get(item["mailbox_id"])
            if mailbox is None:
                conn.execute(
                    "UPDATE send_queue SET status = 'failed', "
                    "error = 'mailbox not configured' WHERE id = ?",
                    (item["id"],),
                )
                continue

            try:
                subject, body = generate_email(contact)
                send_email(mailbox, contact["email"], subject, body)
            except Exception as exc:
                attempts = contact["attempt_count"] + 1
                new_status = (
                    "failed" if attempts >= config.MAX_SEND_ATTEMPTS else "pending"
                )
                conn.execute(
                    "UPDATE contacts SET attempt_count = ?, status = ? "
                    "WHERE id = ?",
                    (attempts, new_status, contact["id"]),
                )
                conn.execute(
                    "UPDATE send_queue SET status = 'failed', error = ? "
                    "WHERE id = ?",
                    (str(exc), item["id"]),
                )
                continue

            sent_at = datetime.now().isoformat(sep=" ")
            conn.execute(
                "UPDATE send_queue SET status = 'sent', sent_at = ?, "
                "subject = ?, body = ? WHERE id = ?",
                (sent_at, subject, body, item["id"]),
            )
            conn.execute(
                "UPDATE contacts SET status = 'sent', last_contacted_at = ? "
                "WHERE id = ?",
                (sent_at, contact["id"]),
            )
            print(f"Sent to {contact['email']} via mailbox {mailbox.id}")


if __name__ == "__main__":
    run_due_sends()
