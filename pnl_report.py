"""
pnl_report.py — Standalone P&L reporting script.

Usage:
    python pnl_report.py
    python pnl_report.py --db /path/to/trades.db
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import db


def format_table(trades: list[dict]) -> str:
    if not trades:
        return "  (no trades recorded)"

    # Column headers
    headers = [
        "ID", "Ticker", "Side", "Entry¢", "Exit¢",
        "Contracts", "P&L¢", "Entry Time", "Exit Time",
    ]

    rows = []
    for t in trades:
        pnl = t.get("pnl_cents")
        pnl_str = f"{pnl:+d}" if pnl is not None else "—"
        exit_p = str(t.get("exit_price", "")) if t.get("exit_price") is not None else "—"
        exit_ts = (t.get("exit_ts") or "—")[:19]
        entry_ts = (t.get("entry_ts") or "—")[:19]

        rows.append([
            str(t.get("id", "")),
            t.get("ticker", ""),
            t.get("side", ""),
            str(t.get("entry_price", "")),
            exit_p,
            str(t.get("contracts", "")),
            pnl_str,
            entry_ts,
            exit_ts,
        ])

    # Compute column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def fmt_row(r):
        return "  " + "  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(r))

    sep = "  " + "  ".join("-" * w for w in col_widths)

    lines = [fmt_row(headers), sep]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Kalshi bot P&L report")
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "trades.db"),
        help="Path to SQLite database (default: trades.db)",
    )
    args = parser.parse_args()

    db_path = args.db

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        sys.exit(1)

    trades = db.get_all_trades(db_path)
    summary = db.get_pnl_summary(db_path)

    print()
    print("=" * 70)
    print("  KALSHI TRADING BOT — FULL TRADE HISTORY")
    print("=" * 70)
    print()
    print(format_table(trades))
    print()
    print("=" * 70)

    total = summary.get("total_pnl_cents", 0)
    sign = "+" if total >= 0 else ""
    print(f"  Total trades  : {summary.get('total_trades', 0)}")
    print(f"  Closed trades : {summary.get('closed_trades', 0)}")
    print(f"  Open trades   : {summary.get('open_trades', 0)}")
    print(f"  Wins          : {summary.get('wins', 0)}")
    print(f"  Losses        : {summary.get('losses', 0)}")
    print(f"  Total P&L     : {sign}{total}¢  (${total / 100:.2f})")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
