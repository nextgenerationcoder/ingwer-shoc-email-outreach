import hashlib

NUM_MAILBOXES = 4


def assign_mailbox(email: str) -> int:
    """
    Deterministically and permanently map an email address to one of the
    4 mailboxes. Same input always produces the same output, so a contact
    is bound to exactly one mailbox for their entire lifetime in the
    system -- this is what rule (a) relies on (no two mailboxes ever
    contact the same customer), independent of import order or reruns.
    """
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return (int(digest, 16) % NUM_MAILBOXES) + 1
