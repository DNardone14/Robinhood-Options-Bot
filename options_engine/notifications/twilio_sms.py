"""
twilio_sms.py — SMS via Twilio REST API (requests only; no twilio SDK needed).

Reuses your equity bot's Twilio account. Remember your lessons-learned note:
toll-free numbers need verification — use a local number for instant delivery.
SMS is short, so the dispatcher trims long bodies before handing them here.
"""

from __future__ import annotations

import requests

from .base import Notifier, NotifyResult

_SMS_LIMIT = 1500  # a few segments; Twilio concatenates


class TwilioSMSNotifier(Notifier):
    channel = "sms"

    def __init__(self, cfg: dict):
        self.sid = cfg.get("account_sid", "")
        self.token = cfg.get("auth_token", "")
        self.from_number = cfg.get("from_number", "")
        self.to_number = cfg.get("to_number", "")

    def configured(self) -> bool:
        return all([self.sid, self.token, self.from_number, self.to_number])

    def send(self, text: str, subject: str | None = None) -> NotifyResult:
        if not self.configured():
            return NotifyResult(self.channel, False, "not configured")
        body = (f"{subject}\n{text}") if subject else text
        body = body[:_SMS_LIMIT]
        try:
            r = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                data={"From": self.from_number, "To": self.to_number, "Body": body},
                auth=(self.sid, self.token),
                timeout=10,
            )
            ok = r.status_code in (200, 201)
            return NotifyResult(self.channel, ok, "sent" if ok else f"HTTP {r.status_code}: {r.text[:120]}")
        except requests.RequestException as exc:
            return NotifyResult(self.channel, False, str(exc))
