import smtplib
from email.message import EmailMessage

from .config import Mailbox


def send_email(mailbox: Mailbox, to_email: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{mailbox.from_name} <{mailbox.username}>" if mailbox.from_name else mailbox.username
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(mailbox.username, mailbox.password)
        smtp.send_message(msg)
