"""
Run once per day (via cron). For each of the 4 mailboxes, picks a random
10-15 pending contacts assigned to that mailbox and schedules each at a
random time within today's send window.

Idempotent: if it has already planned today, it does nothing, so it's safe
if cron fires twice or is re-triggered manually.
"""
import random
from datetime import date, datetime, timedelta

from . import config, db
from .assign import NUM_MAILBOXES


def plan_today() -> None:
    db.init_db()
    today = date.today().isoformat()

    with db.connection() as conn:
        already = conn.execute(
            "SELECT 1 FROM plan_log WHERE plan_date = ?", (today,)
        ).fetchone()
        if already:
            print(f"Already planned for {today}, skipping.")
            return

        total_queued = 0
        for mailbox_id in range(1, NUM_MAILBOXES + 1):
            count = random.randint(
                config.get_daily_min_per_mailbox(), config.get_daily_max_per_mailbox()
            )
            candidates = conn.execute(
                "SELECT id FROM contacts WHERE status = 'pending' "
                "AND assigned_mailbox_id = ? ORDER BY RANDOM() LIMIT ?",
                (mailbox_id, count),
            ).fetchall()

            for row in candidates:
                scheduled_at = _random_time_today(today)
                conn.execute(
                    "INSERT INTO send_queue (contact_id, mailbox_id, "
                    "scheduled_at, status) VALUES (?, ?, ?, 'scheduled')",
                    (row["id"], mailbox_id, scheduled_at),
                )
                conn.execute(
                    "UPDATE contacts SET status = 'queued' WHERE id = ?",
                    (row["id"],),
                )
                total_queued += 1

        conn.execute("INSERT INTO plan_log (plan_date) VALUES (?)", (today,))
        print(f"Planned {total_queued} sends for {today}.")


def _random_time_today(today_iso: str) -> str:
    day = date.fromisoformat(today_iso)
    start = datetime.combine(day, datetime.min.time()) + timedelta(
        hours=config.get_send_window_start_hour()
    )
    end = datetime.combine(day, datetime.min.time()) + timedelta(
        hours=config.get_send_window_end_hour()
    )
    delta_seconds = int((end - start).total_seconds())
    offset = random.randint(0, max(delta_seconds, 0))
    return (start + timedelta(seconds=offset)).isoformat(sep=" ")


if __name__ == "__main__":
    plan_today()
