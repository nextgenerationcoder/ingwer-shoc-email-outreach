"""
Mark a contact as unsubscribed so it is permanently excluded from planning
and, as a safety net, from sending even if already queued.

Usage:
    python -m email_pipeline.unsubscribe someone@example.com
"""
import sys

from . import db


def unsubscribe(email: str) -> None:
    db.init_db()
    with db.connection() as conn:
        conn.execute(
            "UPDATE contacts SET status = 'unsubscribed' WHERE email = ?",
            (email.strip().lower(),),
        )
        conn.execute(
            "UPDATE send_queue SET status = 'failed', "
            "error = 'contact unsubscribed' "
            "WHERE contact_id = (SELECT id FROM contacts WHERE email = ?) "
            "AND status = 'scheduled'",
            (email.strip().lower(),),
        )
    print(f"{email} marked unsubscribed.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m email_pipeline.unsubscribe someone@example.com")
        sys.exit(1)
    unsubscribe(sys.argv[1])
