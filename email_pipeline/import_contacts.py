"""
Load contacts into the database.

Usage:
    python -m email_pipeline.import_contacts contacts.csv

CSV columns expected: email,name,company,source (only "email" is required;
the rest may be blank).

Emails already present in the DB are skipped (never re-imported/re-queued),
which is part of what guarantees rule (b): a contact is only ever entered
into the pipeline once.
"""
import csv
import sys

from . import db
from .assign import assign_mailbox


def import_csv(path: str) -> None:
    db.init_db()
    added = 0
    skipped = 0
    with open(path, newline="") as f, db.connection() as conn:
        reader = csv.DictReader(f)
        for row in reader:
            email = (row.get("email") or "").strip().lower()
            if not email:
                continue
            mailbox_id = assign_mailbox(email)
            try:
                conn.execute(
                    "INSERT INTO contacts (email, name, company, source, "
                    "assigned_mailbox_id) VALUES (?, ?, ?, ?, ?)",
                    (
                        email,
                        row.get("name"),
                        row.get("company"),
                        row.get("source"),
                        mailbox_id,
                    ),
                )
                added += 1
            except Exception:
                skipped += 1
    print(f"Imported {added} new contacts, skipped {skipped} duplicates.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m email_pipeline.import_contacts contacts.csv")
        sys.exit(1)
    import_csv(sys.argv[1])
