"""
dispatcher.py — send through channels in priority order; first success wins.

If no channel is configured (no creds yet), runs in DRY-RUN mode: prints the
message to stdout and logs it, so the whole assistant is testable today. Every
sent message is also appended to alerts_log for the dashboard's "Recent Alerts".
"""

from __future__ import annotations

from datetime import datetime

from .base import NotifyResult
from .telegram import TelegramNotifier
from .twilio_sms import TwilioSMSNotifier
from .email_smtp import EmailNotifier


class NotificationDispatcher:
    def __init__(self, cfg: dict, alert_log: list | None = None):
        self.cfg = cfg
        self.priority = cfg.get("priority", ["telegram", "sms", "email"])
        self._channels = {
            "telegram": TelegramNotifier(cfg.get("telegram", {})),
            "sms": TwilioSMSNotifier(cfg.get("sms", {})),
            "email": EmailNotifier(cfg.get("email", {})),
        }
        # in-memory ring buffer the dashboard reads; also useful for tests
        self.alert_log = alert_log if alert_log is not None else []

    def any_configured(self) -> bool:
        return any(c.configured() for c in self._channels.values())

    def configured_channels(self) -> list[str]:
        return [name for name in self.priority if self._channels[name].configured()]

    def send(self, text: str, subject: str | None = None, kind: str = "info") -> NotifyResult:
        self.alert_log.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind, "subject": subject or "", "text": text,
        })
        if len(self.alert_log) > 200:
            del self.alert_log[:-200]

        if not self.any_configured():
            print(f"\n[DRY-RUN notify | {kind}] {subject or ''}\n{text}\n", flush=True)
            return NotifyResult("dry-run", True, "printed (no channel configured)")

        last = NotifyResult("none", False, "no channel attempted")
        for name in self.priority:
            ch = self._channels[name]
            if not ch.configured():
                continue
            last = ch.send(text, subject)
            if last.ok:
                return last
        return last  # all configured channels failed
