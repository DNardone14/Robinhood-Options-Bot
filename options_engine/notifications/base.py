"""base.py — notifier interface + result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    detail: str = ""


class Notifier(ABC):
    channel = "base"

    @abstractmethod
    def configured(self) -> bool:
        """True if credentials are present and the channel can actually send."""

    @abstractmethod
    def send(self, text: str, subject: str | None = None) -> NotifyResult:
        ...
