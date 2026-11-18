"""Sending email.

With SMTP settings in .env (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM),
mail goes out through that server. Without them (local development), each email is saved
as a .eml file in instance/outbox/ instead, so nothing is sent by accident and you can
open the files to see exactly what would have gone out.
"""
import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid

log = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(os.environ.get("NEULEARNING_DATA_DIR") or os.path.join(BASE_DIR, "instance"), "outbox")
# Reserved domains that can never receive mail (demo and test accounts use them).
FAKE_TLDS = {"test", "example", "invalid", "localhost"}


def smtp_configured():
    return bool(os.environ.get("SMTP_HOST"))


def build(to, subject, text, html=None, unsubscribe_url=None):
    msg = EmailMessage()
    msg["From"] = os.environ.get("MAIL_FROM", "NeuLearning <no-reply@neulearning.local>")
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="neulearning")
    if unsubscribe_url:
        # Lets mail apps show their own "Unsubscribe" button.
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(to, subject, text, html=None, unsubscribe_url=None, outbox=None):
    """Send one email. Returns True if it was sent (or saved to the outbox)."""
    msg = build(to, subject, text, html, unsubscribe_url)
    if not smtp_configured():
        folder = outbox or OUTBOX
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        safe_to = "".join(c if c.isalnum() else "_" for c in to)[:40]
        with open(os.path.join(folder, f"{stamp}-{safe_to}.eml"), "wb") as f:
            f.write(bytes(msg))
        log.info("Email saved to outbox (no SMTP configured): %s", subject)
        return True
    if to.rsplit(".", 1)[-1].lower() in FAKE_TLDS:     # demo/test accounts: nobody to deliver to
        log.info("Skipped email to test address %s", to)
        return False
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        context = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, context=context, timeout=15)
        else:
            server = smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=15)
            server.starttls(context=context)
        with server:
            if os.environ.get("SMTP_USER"):
                server.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.warning("Email to %s failed: %s", to, e)
        return False


def send_later(*args, **kwargs):
    """Send in the background so a page never waits on the mail server."""
    threading.Thread(target=send, args=args, kwargs=kwargs, daemon=True).start()
