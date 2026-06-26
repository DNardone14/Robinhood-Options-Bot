"""
telegram.py — primary channel. Uses the Telegram Bot HTTP API (requests only,
no extra dependency).

Setup (1 minute):
  1. In Telegram, message @BotFather -> /newbot -> follow prompts -> copy the token.
  2. Message your new bot once (say "hi"), then visit
     https://api.telegram.org/bot<token>/getUpdates and copy the chat.id.
  3. export TELEGRAM_BOT_TOKEN="..."  and  export TELEGRAM_CHAT_ID="..."

Supports long messages (auto-split at 4096 chars) and HTML formatting. Also can
send a photo (e.g. a chart) via send_photo().
"""

from __future__ import annotations

import requests

from .base import Notifier, NotifyResult

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX = 4096


class TelegramNotifier(Notifier):
    channel = "telegram"

    def __init__(self, cfg: dict):
        self.token = cfg.get("bot_token", "")
        self.chat_id = cfg.get("chat_id", "")

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _split(self, text: str) -> list[str]:
        if len(text) <= _MAX:
            return [text]
        chunks, cur = [], ""
        for line in text.split("\n"):
            if len(cur) + len(line) + 1 > _MAX:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur:
            chunks.append(cur)
        return chunks

    def send(self, text: str, subject: str | None = None) -> NotifyResult:
        if not self.configured():
            return NotifyResult(self.channel, False, "not configured")
        body = (f"<b>{subject}</b>\n{text}") if subject else text
        try:
            for chunk in self._split(body):
                r = requests.post(
                    _API.format(token=self.token, method="sendMessage"),
                    json={"chat_id": self.chat_id, "text": chunk,
                          "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=10,
                )
                if r.status_code != 200:
                    return NotifyResult(self.channel, False, f"HTTP {r.status_code}: {r.text[:120]}")
            return NotifyResult(self.channel, True, "sent")
        except requests.RequestException as exc:
            return NotifyResult(self.channel, False, str(exc))

    def send_photo(self, image_path: str, caption: str = "") -> NotifyResult:
        if not self.configured():
            return NotifyResult(self.channel, False, "not configured")
        try:
            with open(image_path, "rb") as fh:
                r = requests.post(
                    _API.format(token=self.token, method="sendPhoto"),
                    data={"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"photo": fh},
                    timeout=20,
                )
            ok = r.status_code == 200
            return NotifyResult(self.channel, ok, "sent" if ok else f"HTTP {r.status_code}")
        except (requests.RequestException, OSError) as exc:
            return NotifyResult(self.channel, False, str(exc))
