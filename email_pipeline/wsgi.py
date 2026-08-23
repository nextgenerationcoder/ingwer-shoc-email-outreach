"""
WSGI entry point for production deployment (gunicorn, uwsgi, etc.).

    gunicorn -w 1 -b 0.0.0.0:8000 email_pipeline.wsgi:app

-w 1 is intentional: the scheduler (daily plan + per-minute send) starts
once per worker process, so more than one worker would run the jobs
that many times over and send duplicate emails.
"""
from .webapp.app import app

if __name__ == "__main__":
    app.run()
