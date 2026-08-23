"""
Configuration. Everything here can be set either as an environment
variable (for cron/headless use) or through the web UI's Settings page,
which stores values in the `settings` table -- DB values take priority
over env vars whenever both are present, so editing Settings in the UI
takes effect immediately without restarting anything.

For each of the 4 mailboxes, set (env var form):
  MAILBOX{N}_SMTP_HOST
  MAILBOX{N}_SMTP_PORT   (defaults to 587)
  MAILBOX{N}_USERNAME
  MAILBOX{N}_PASSWORD
  MAILBOX{N}_FROM_NAME   (optional display name)

Plus:
  SENDER_COMPANY_NAME    (your company name, used in the generated email)
  PRODUCT_DESCRIPTION    (one line describing what you make/sell, used to
                          phrase the inquiry question)
  DB_PATH                (defaults to email_pipeline/pipeline.db)
  DEEPSEEK_API_KEY        (used to generate email text via the DeepSeek
                          API; falls back to a plain template if unset,
                          so the pipeline still runs end-to-end without it)
  DAILY_MIN_PER_MAILBOX  (defaults to 10)
  DAILY_MAX_PER_MAILBOX  (defaults to 15)
  SEND_WINDOW_START_HOUR (defaults to 8)
  SEND_WINDOW_END_HOUR   (defaults to 21)
"""
import json
import os
from dataclasses import dataclass

# DB_PATH can't itself live in the DB (chicken-and-egg), so it stays
# env-var-only.
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "pipeline.db"),
)

MAX_SEND_ATTEMPTS = int(os.environ.get("MAX_SEND_ATTEMPTS", "3"))


@dataclass(frozen=True)
class Mailbox:
    id: int
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_name: str


def _setting(key: str, env_key: str, default=None):
    from . import db

    value = db.get_setting(key)
    if value is not None and value != "":
        return value
    return os.environ.get(env_key, default)


def get_deepseek_api_key():
    return _setting("deepseek_api_key", "DEEPSEEK_API_KEY")


def get_sender_company_name() -> str:
    return _setting("sender_company_name", "SENDER_COMPANY_NAME", "our company")


def get_product_description() -> str:
    return _setting("product_description", "PRODUCT_DESCRIPTION", "our product")


def get_daily_min_per_mailbox() -> int:
    return int(_setting("daily_min_per_mailbox", "DAILY_MIN_PER_MAILBOX", "10"))


def get_daily_max_per_mailbox() -> int:
    return int(_setting("daily_max_per_mailbox", "DAILY_MAX_PER_MAILBOX", "15"))


def get_send_window_start_hour() -> int:
    return int(_setting("send_window_start_hour", "SEND_WINDOW_START_HOUR", "8"))


def get_send_window_end_hour() -> int:
    return int(_setting("send_window_end_hour", "SEND_WINDOW_END_HOUR", "21"))


def get_prompt_template_override():
    """A prompt saved via the UI's prompt editor, or None to use the default."""
    from . import db

    return db.get_setting("prompt_template")


def load_mailboxes() -> list[Mailbox]:
    from . import db

    stored = db.get_setting("mailboxes")
    if stored:
        return [Mailbox(**m) for m in json.loads(stored)]

    mailboxes = []
    for n in range(1, 5):
        prefix = f"MAILBOX{n}_"
        host = os.environ.get(prefix + "SMTP_HOST")
        if not host:
            continue
        mailboxes.append(
            Mailbox(
                id=n,
                smtp_host=host,
                smtp_port=int(os.environ.get(prefix + "SMTP_PORT", "587")),
                username=os.environ[prefix + "USERNAME"],
                password=os.environ[prefix + "PASSWORD"],
                from_name=os.environ.get(prefix + "FROM_NAME", ""),
            )
        )
    return mailboxes


def save_mailboxes(mailboxes: list[Mailbox]) -> None:
    from . import db

    db.set_setting(
        "mailboxes",
        json.dumps([m.__dict__ for m in mailboxes]),
    )
