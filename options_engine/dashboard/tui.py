"""
tui.py — rich terminal dashboard.

Four panels:
  * header   — clock, market open/closed, scan count, daily P&L vs limit, HALT flag
  * signals  — active signals this scan (symbol, strategy, contract, prem, conf)
  * positions— open positions with mark, unrealized P&L, per-position Greeks
  * footer   — net book Greeks, win rate, watchlist metrics (IV rank, P/C, exp move)

render_dashboard(engine) returns a rich renderable; main.py drives it under Live.
If rich isn't installed, render_text() gives a plain-text fallback.
"""

from __future__ import annotations

from ..data import market_calendar as cal
from ..config import RISK_CONFIG

try:
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def _pnl_text(v: float) -> "Text":
    color = "green" if v > 0 else "red" if v < 0 else "white"
    arrow = "▲" if v > 0 else "▼" if v < 0 else "•"
    return Text(f"{arrow} ${v:,.2f}", style=color)


def _header(engine) -> "Panel":
    open_ = cal.is_market_open()
    status = Text("● OPEN", style="bold green") if open_ else Text("● CLOSED", style="bold red")
    day_pnl = engine.portfolio.realized_day_pnl + engine.portfolio.unrealized_pnl()
    limit = engine.risk.daily_loss_limit_dollars()
    halt = Text("  HALTED", style="bold white on red") if engine.risk.halted else Text("")
    last = engine.last_scan.strftime("%H:%M:%S ET") if engine.last_scan else "—"
    line = Text.assemble(
        ("OPTIONS ENGINE", "bold cyan"), "   ", status, "   ",
        (f"scan {last}", "dim"), "   day P&L ", _pnl_text(day_pnl),
        (f"  (loss limit ${limit:,.0f})", "dim"), halt,
    )
    return Panel(line, box=box.HEAVY, style="cyan")


def _signals_table(engine) -> "Table":
    t = Table(title="Active Signals", box=box.SIMPLE_HEAVY, expand=True, title_style="bold")
    for col in ("Sym", "Strategy", "Dir", "Contract", "Prem", "Risk$", "TP", "SL", "Qty", "Conf"):
        t.add_column(col, overflow="fold")
    if not engine.active_signals:
        t.add_row("—", "no signals this scan", "", "", "", "", "", "", "", "")
    for s in engine.active_signals:
        lg = s.legs[0]
        contract = f"{lg.expiration} {lg.strike:g}{lg.option_type[0].upper()}"
        if len(s.legs) > 1:
            contract += f"/{s.legs[1].strike:g}{s.legs[1].option_type[0].upper()}"
        dir_style = "green" if s.direction == "bullish" else "red"
        t.add_row(
            s.symbol, s.strategy.replace("_", " ")[:18], Text(s.direction[:4], style=dir_style),
            contract, f"{s.net_premium:.2f}", f"{s.max_risk*s.quantity:,.0f}",
            f"{s.profit_target:.2f}", f"{s.stop_loss:.2f}", str(s.quantity), f"{s.confidence:.2f}",
        )
    return t


def _positions_table(engine) -> "Table":
    t = Table(title="Open Positions", box=box.SIMPLE_HEAVY, expand=True, title_style="bold")
    for col in ("ID", "Sym", "Strat", "Entry", "Mark", "uP&L", "Δ", "Θ", "V"):
        t.add_column(col, overflow="fold")
    pos = engine.portfolio.open_positions()
    if not pos:
        t.add_row("—", "no open positions", "", "", "", "", "", "", "")
    for p in pos:
        t.add_row(
            p.position_id, p.symbol, p.strategy.split("+")[0][:10],
            f"{p.entry_premium:.2f}", f"{p.current_premium:.2f}",
            _pnl_text(p.unrealized_pnl),
            f"{p.net_delta:.0f}", f"{p.net_theta:.0f}", f"{p.net_vega:.0f}",
        )
    return t


def _footer(engine) -> "Panel":
    g = engine.portfolio.book_greeks()
    wr, wins, n = engine.portfolio.win_rate()
    rows = Table.grid(expand=True)
    rows.add_column()
    rows.add_column(justify="right")
    rows.add_row(
        Text.assemble(
            ("Net Greeks  ", "bold"),
            (f"Δ {g['delta']:.0f}  (cap ±{RISK_CONFIG['max_net_delta']:.0f})   ", "white"),
            (f"Θ {g['theta']:.0f}/day  (floor {RISK_CONFIG['max_net_theta']:.0f})   ", "white"),
            (f"V {g['vega']:.0f}  (cap ±{RISK_CONFIG['max_net_vega']:.0f})", "white"),
        ),
        Text(f"Win rate {wr*100:.0f}%  ({wins}/{n})", style="bold"),
    )
    # per-symbol metric strip
    strip = Table(box=box.MINIMAL, expand=True, show_header=True, title="Watchlist Metrics")
    for col in ("Sym", "Price", "RSI", "IV rank", "IV %", "Skew", "P/C vol", "Exp move"):
        strip.add_column(col)
    for sym, m in engine.metrics_by_symbol.items():
        em = m.get("expected_move") or {}
        empct = em.get("expected_move_pct")
        strip.add_row(
            sym,
            f"{m.get('price', 0):.2f}",
            f"{m.get('rsi', 0):.0f}",
            f"{m['iv_rank']:.0f}" if m.get("iv_rank") is not None else "—",
            f"{m['iv_pct']:.0f}" if m.get("iv_pct") is not None else "—",
            f"{m['skew']:+.3f}" if m.get("skew") is not None else "—",
            f"{m['pcr_volume']:.2f}" if m.get("pcr_volume") is not None else "—",
            f"{empct*100:.1f}%" if empct is not None else "—",
        )
    return Panel(Group(rows, strip), box=box.HEAVY, style="dim")


def render_dashboard(engine):
    if not _HAS_RICH:
        return render_text(engine)
    return Group(_header(engine), _signals_table(engine), _positions_table(engine), _footer(engine))


def render_text(engine) -> str:
    lines = ["=" * 70, "OPTIONS ENGINE", "=" * 70]
    lines.append(f"Market: {'OPEN' if cal.is_market_open() else 'CLOSED'}  "
                 f"Day P&L: ${engine.portfolio.realized_day_pnl + engine.portfolio.unrealized_pnl():,.2f}"
                 f"  {'[HALTED]' if engine.risk.halted else ''}")
    lines.append("-- Signals --")
    for s in engine.active_signals or []:
        lg = s.legs[0]
        lines.append(f"  {s.symbol:5} {s.strategy:22} {s.direction:8} "
                     f"{lg.expiration} {lg.strike:g}{lg.option_type[0].upper()} "
                     f"prem {s.net_premium:.2f} x{s.quantity} conf {s.confidence:.2f}")
    if not engine.active_signals:
        lines.append("  (none)")
    lines.append("-- Positions --")
    for p in engine.portfolio.open_positions():
        lines.append(f"  {p.position_id:10} {p.symbol:5} mark {p.current_premium:.2f} "
                     f"uPnL ${p.unrealized_pnl:,.2f}")
    g = engine.portfolio.book_greeks()
    wr, wins, n = engine.portfolio.win_rate()
    lines.append(f"Net Greeks Δ{g['delta']:.0f} Θ{g['theta']:.0f} V{g['vega']:.0f}  "
                 f"Win {wr*100:.0f}% ({wins}/{n})")
    return "\n".join(lines)


class Dashboard:
    """Thin holder so main.py can call .render(engine)."""
    @staticmethod
    def render(engine):
        return render_dashboard(engine)
