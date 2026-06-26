"""
main.py — entry point. Runs the scan loop and live terminal dashboard.

Usage (from the options_engine/ parent directory):
    python -m options_engine.main                 # live dashboard + scan loop
    python -m options_engine.main --once          # single scan, print, exit
    python -m options_engine.main --no-dashboard  # loop, log to stdout only
    python -m options_engine.main --symbol NVDA   # scan one symbol once

Set your Tradier token first:
    export TRADIER_TOKEN="xxxx"

Nothing here auto-trades. Approved signals are written as order tickets in the
tickets/ folder and surfaced on the dashboard; you execute them through Claude's
Robinhood MCP (review_option_order -> confirm -> place_option_order).
"""

from __future__ import annotations

import argparse
import time

from .engine import Engine
from .config import ENGINE_CONFIG
from .dashboard import render_dashboard, render_text


def run_once(engine: Engine, symbol: str | None = None) -> None:
    if symbol:
        sigs = engine.scan_symbol(symbol)
        engine.active_signals = sigs
        engine.mark_and_manage()
    else:
        engine.scan_all()
    print(render_text(engine))


def run_loop(engine: Engine, use_dashboard: bool = True) -> None:
    interval = ENGINE_CONFIG["scan_interval"]
    if use_dashboard:
        try:
            from rich.live import Live
            from rich.console import Console
        except ImportError:
            use_dashboard = False
    if use_dashboard:
        console = Console()
        with Live(render_dashboard(engine), console=console, refresh_per_second=2,
                  screen=True) as live:
            while True:
                try:
                    engine.scan_all()
                    live.update(render_dashboard(engine))
                    time.sleep(interval)
                except KeyboardInterrupt:
                    break
                except Exception as exc:
                    engine.last_error = str(exc)
                    live.update(render_dashboard(engine))
                    time.sleep(interval)
    else:
        while True:
            try:
                engine.scan_all()
                print(render_text(engine), flush=True)
                time.sleep(interval)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                print(f"[error] {exc}", flush=True)
                time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="Options day-trading strategy engine")
    ap.add_argument("--once", action="store_true", help="run a single scan and exit")
    ap.add_argument("--symbol", help="scan only this symbol (implies --once)")
    ap.add_argument("--no-dashboard", action="store_true", help="loop without the rich TUI")
    args = ap.parse_args()

    engine = Engine()
    try:
        if args.symbol or args.once:
            run_once(engine, args.symbol)
        else:
            run_loop(engine, use_dashboard=not args.no_dashboard)
    finally:
        engine.storage.close()


if __name__ == "__main__":
    main()
