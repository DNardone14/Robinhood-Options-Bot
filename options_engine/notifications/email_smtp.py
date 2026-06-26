"""
email_smtp.py — email via SMTP (e.g. SendGrid relay).

Your DigitalOcean box blocks direct outbound SMTP (25/465/587), so point this at
a relay (SendGrid SMTP shown in config defaults) rather than Gmail directly.
HTML body is stripped to text for the plain part; Telegram remains primary.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from .base import Notifier, NotifyResult


class EmailNotifier(Notifier):
    channel = "email"

    def __init__(self, cfg: dict):
        self.host = cfg.get("smtp_host", "")
        self.port = cfg.get("smtp_port", 587)
        self.username = cfg.get("username", "")
        self.password = cfg.get("password", "")
        self.from_addr = cfg.get("from_addr", "")
        self.to_addr = cfg.get("to_addr", "")

    def configured(self) -> bool:
        return all([self.host, self.username, self.password, self.from_addr, self.to_addr])

    def send(self, text: str, subject: str | None = None) -> NotifyResult:
        if not self.configured():
            return NotifyResult(self.channel, False, "not configured")
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = subject or "Options Assistant"
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        try:
            with smtplib.SMTP(self.host, self.port, timeout=15) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.sendmail(self.from_addr, [self.to_addr], msg.as_string())
            return NotifyResult(self.channel, True, "sent")
        except (smtplib.SMTPException, OSError) as exc:
            return NotifyResult(self.channel, False, str(exc))
