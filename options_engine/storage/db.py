"""
db.py — SQLite + CSV logging for signals and trades (backtest review).

Tables:
  signals(ts, symbol, strategy, signal_type, direction, premium, max_risk,
          profit_target, stop_loss, quantity, confidence, notes, approved, reason)
  trades(ts, position_id, symbol, strategy, action, premium, quantity,
         pnl, reason)

Everything also mirrors to CSV so you can pull it into pandas for analysis.
"""

from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime


class Storage:
    def __init__(self, cfg: dict):
        self.db_path = cfg["db_path"]
        self.signals_csv = cfg["signals_csv"]
        self.trades_csv = cfg["trades_csv"]
        self._conn = sqlite3.connect(self.db_path)
        self._init_db()
        self._init_csv()

    def _init_db(self) -> None:
        c = self._conn.cursor()
        c.execute(
            """CREATE TABLE IF NOT EXISTS signals(
                ts TEXT, symbol TEXT, strategy TEXT, signal_type TEXT,
                direction TEXT, premium REAL, max_risk REAL, profit_target REAL,
                stop_loss REAL, quantity INTEGER, confidence REAL, notes TEXT,
                approved INTEGER, reason TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS trades(
                ts TEXT, position_id TEXT, symbol TEXT, strategy TEXT,
                action TEXT, premium REAL, quantity INTEGER, pnl REAL, reason TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS alerts(
                ts TEXT, symbol TEXT, kind TEXT, subject TEXT, body TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS decisions(
                ts TEXT, symbol TEXT, kind TEXT, decision TEXT, pnl REAL, notes TEXT)"""
        )
        self._conn.commit()

    def _init_csv(self) -> None:
        if not os.path.exists(self.signals_csv):
            with open(self.signals_csv, "w", newline="") as fh:
                csv.writer(fh).writerow(
                    ["ts", "symbol", "strategy", "signal_type", "direction", "premium",
                     "max_risk", "profit_target", "stop_loss", "quantity", "confidence",
                     "notes", "approved", "reason"]
                )
        if not os.path.exists(self.trades_csv):
            with open(self.trades_csv, "w", newline="") as fh:
                csv.writer(fh).writerow(
                    ["ts", "position_id", "symbol", "strategy", "action", "premium",
                     "quantity", "pnl", "reason"]
                )

    def log_signal(self, signal, approved: bool, reason: str) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        row = (ts, signal.symbol, signal.strategy, signal.signal_type.value,
               signal.direction, round(signal.net_premium, 4), round(signal.max_risk, 2),
               signal.profit_target, signal.stop_loss, signal.quantity,
               signal.confidence, signal.notes, int(approved), reason)
        self._conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row
        )
        self._conn.commit()
        with open(self.signals_csv, "a", newline="") as fh:
            csv.writer(fh).writerow(row)

    def log_trade(self, position_id: str, symbol: str, strategy: str, action: str,
                  premium: float, quantity: int, pnl: float = 0.0, reason: str = "") -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        row = (ts, position_id, symbol, strategy, action, round(premium, 4),
               quantity, round(pnl, 2), reason)
        self._conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)", row)
        self._conn.commit()
        with open(self.trades_csv, "a", newline="") as fh:
            csv.writer(fh).writerow(row)

    # ----- alerts + decisions (PDR notification/perf layer) ---------- #
    def log_alert(self, symbol: str, kind: str, subject: str, body: str) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        self._conn.execute("INSERT INTO alerts VALUES (?,?,?,?,?)",
                           (ts, symbol, kind, subject, body))
        self._conn.commit()

    def recent_alerts(self, limit: int = 25) -> list[dict]:
        cur = self._conn.execute(
            "SELECT ts,symbol,kind,subject,body FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
        )
        return [dict(zip(("ts", "symbol", "kind", "subject", "body"), r)) for r in cur.fetchall()]

    def log_decision(self, symbol: str, kind: str, decision: str, pnl: float = 0.0, notes: str = "") -> None:
        """Record a human decision (approved/rejected/closed) for win-rate tracking."""
        ts = datetime.now().isoformat(timespec="seconds")
        self._conn.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                           (ts, symbol, kind, decision, round(pnl, 2), notes))
        self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()
