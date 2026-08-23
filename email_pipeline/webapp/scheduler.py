"""
Runs the daily planner and per-minute send worker inside the webapp
process itself, so deploying this as a normal always-on web service (no
separate crontab access needed) is enough to keep the campaign running.

Only start this once per process. If you deploy behind gunicorn/uwsgi,
run with a single worker (e.g. `gunicorn -w 1`) -- each worker process
would otherwise run its own copy of these jobs and double-send.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .. import config
from ..plan_daily import plan_today
from ..worker import run_due_sends

log = logging.getLogger(__name__)
_scheduler = None


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _safe(plan_today),
        trigger="cron",
        hour=0,
        minute=5,
        id="plan_daily",
        replace_existing=True,
    )
    scheduler.add_job(
        _safe(run_due_sends),
        trigger="interval",
        minutes=1,
        id="worker",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info("Scheduler started: daily plan at 00:05, worker every minute.")
    return scheduler


def _safe(fn):
    def wrapper():
        try:
            fn()
        except Exception:
            log.exception("Scheduled job %s failed", fn.__name__)

    return wrapper
