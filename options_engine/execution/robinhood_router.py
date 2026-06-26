"""
robinhood_router.py — turn an approved Signal into a Robinhood-MCP order ticket.

This engine NEVER places orders by itself. By design (mode="signal_only") it
writes a human-readable ticket plus the exact MCP call args, which you paste/say
to Claude. Claude then runs review_option_order -> (you confirm) -> place_option_order
on the agentic account 644037566 — the same human-in-the-loop flow your equity
bot already uses.

Why no direct API trade? Your docs note the agentic account is reachable ONLY via
Claude's MCP connector, not robin_stocks. So this router produces the instruction
payload rather than calling a brokerage SDK.

build_ticket() returns a dict with:
  * leg specs (OCC symbol, side, qty, limit price)
  * the suggested limit price (mid +/- buffer)
  * a copy-paste instruction string for Claude
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from ..strategies.base import Signal


class RobinhoodRouter:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.account = cfg["robinhood_account"]
        self.ticket_dir = cfg.get("ticket_dir", "tickets")
        os.makedirs(self.ticket_dir, exist_ok=True)

    def _limit_price(self, premium: float, side: str) -> float:
        buf = self.cfg["limit_buffer_pct"]
        # pay up a touch when buying, give a touch when selling, to improve fills
        if side.startswith("buy"):
            return round(premium * (1 + buf), 2)
        return round(premium * (1 - buf), 2)

    def build_ticket(self, signal: Signal) -> dict:
        legs = []
        for lg in signal.legs:
            legs.append(
                {
                    "option_symbol": lg.option_symbol,
                    "underlying": lg.symbol,
                    "strike": lg.strike,
                    "expiration": lg.expiration,
                    "type": lg.option_type,
                    "side": lg.side,
                    "quantity": lg.quantity,
                    "limit_price": self._limit_price(lg.entry_premium, lg.side),
                }
            )
        is_multi = len(legs) > 1
        ticket = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "account": self.account,
            "symbol": signal.symbol,
            "strategy": signal.strategy,
            "direction": signal.direction,
            "signal_type": signal.signal_type.value,
            "order_class": "multileg" if is_multi else "single",
            "order_type": self.cfg["default_order_type"],
            "legs": legs,
            "net_premium": round(signal.net_premium, 2),
            "max_risk_dollars": round(signal.max_risk * signal.quantity, 2),
            "profit_target": signal.profit_target,
            "stop_loss": signal.stop_loss,
            "quantity": signal.quantity,
            "confidence": signal.confidence,
            "notes": signal.notes,
            "claude_instruction": self._instruction(signal, legs),
            "mcp_call": self._mcp_call(signal, legs),
        }
        return ticket

    def _instruction(self, signal: Signal, legs: list[dict]) -> str:
        verb = "BUY" if legs[0]["side"].startswith("buy") else "SELL"
        if len(legs) == 1:
            l = legs[0]
            return (
                f'In Claude: "Review and place an options order — {verb} '
                f'{l["quantity"]} {l["underlying"]} {l["expiration"]} '
                f'{l["strike"]:g} {l["type"].upper()} at ${l["limit_price"]:.2f} limit '
                f'on account {self.account}." Run review_option_order first, confirm, '
                f"then place_option_order."
            )
        desc = " / ".join(
            f'{lg["side"]} {lg["quantity"]} {lg["strike"]:g}{lg["type"][0].upper()}'
            for lg in legs
        )
        return (
            f'In Claude: "Review and place a multi-leg {signal.signal_type.value} on '
            f'{signal.symbol} {legs[0]["expiration"]}: {desc}, net '
            f'${abs(signal.net_premium):.2f}, on account {self.account}." '
            f"Run review_option_order first, confirm, then place_option_order."
        )

    def _mcp_call(self, signal: Signal, legs: list[dict]) -> dict:
        """Approximate argument payload for the Robinhood MCP option-order tool.
        Field names mirror review_option_order / place_option_order conventions;
        Claude adapts exact keys at call time."""
        return {
            "tool": "place_option_order",
            "account_id": self.account,
            "symbol": signal.symbol,
            "order_type": self.cfg["default_order_type"],
            "time_in_force": "gfd",
            "legs": [
                {
                    "instrument": lg["option_symbol"],
                    "side": lg["side"],
                    "quantity": lg["quantity"],
                    "limit_price": lg["limit_price"],
                }
                for lg in legs
            ],
        }

    def write_ticket(self, ticket: dict) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.join(self.ticket_dir, f"{ticket['symbol']}_{ts}.json")
        with open(fname, "w") as fh:
            json.dump(ticket, fh, indent=2)
        return fname
