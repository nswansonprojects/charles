"""
tax_engine.py — Tax documentation engine.

Generates IRS-style capital gains reports from the bot's trade history.
All calculations follow US tax rules:
  - FIFO cost basis (default IRS method)
  - Short-term: held < 365 days → taxed as ordinary income
  - Long-term:  held >= 365 days → taxed at 0/15/20% preferential rate
  - Wash sale detection: sell at loss + rebuy same security within 30 days
  - Crypto treated same as property (IRS Notice 2014-21)

Outputs:
  - Form 8949-style trade-by-trade table
  - Schedule D summary (short vs long term totals)
  - CSV for TurboTax / H&R Block import
  - Date-range filtering
"""

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

log = logging.getLogger(__name__)

DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")

# ── US Capital gains tax rates 2025 (single filer, approximate) ──────────────
# Short-term: same as ordinary income — use worst-case 37% for display
SHORT_TERM_RATE = 0.37
# Long-term brackets (single filer 2025)
LONG_TERM_BRACKETS = [
    (0,       47_025,  0.00),
    (47_025,  518_900, 0.15),
    (518_900, float("inf"), 0.20),
]
NET_INVESTMENT_INCOME_SURTAX = 0.038   # 3.8% on high earners


def _load_trades() -> list[dict]:
    if not os.path.exists(TRADES_FILE):
        return []
    with open(TRADES_FILE) as f:
        return json.load(f)


def _parse_dt(ts: str) -> datetime:
    """Parse ISO timestamp to UTC datetime."""
    ts = ts[:19].replace(" ", "T")
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


# ── FIFO cost basis engine ────────────────────────────────────────────────────

def _build_tax_lots(trades: list[dict]) -> list[dict]:
    """
    Process all trades using FIFO to compute:
      - Realized gains/losses per sale
      - Hold period for each lot (short vs long term)
      - Wash sale detection
    Returns list of closed lot dicts.
    """
    # FIFO queues per symbol: [(buy_date, shares, cost_per_share, order_id)]
    queues: dict[str, list] = defaultdict(list)
    closed_lots = []

    for t in sorted(trades, key=lambda x: x.get("timestamp", "")):
        sym    = t.get("symbol", "")
        action = t.get("action", "")
        ts     = _parse_dt(t.get("timestamp", "2025-01-01"))
        price  = float(t.get("price", 0) or 0)
        value  = float(t.get("value", 0) or 0)

        # Compute shares from value/price
        if price > 0:
            shares = value / price
        else:
            shares = 0

        if action == "BUY":
            queues[sym].append({
                "date":   ts,
                "shares": shares,
                "cost_per_share": price,
                "order_id": t.get("order_id", ""),
            })

        elif action in ("SELL", "SHORT_COVER"):
            remaining_to_sell = shares
            while remaining_to_sell > 0.0001 and queues[sym]:
                lot = queues[sym][0]
                sell_shares = min(remaining_to_sell, lot["shares"])

                proceeds     = sell_shares * price
                cost_basis   = sell_shares * lot["cost_per_share"]
                gain_loss    = proceeds - cost_basis
                hold_days    = (ts - lot["date"]).days
                term         = "LONG" if hold_days >= 365 else "SHORT"

                closed_lots.append({
                    "symbol":          sym,
                    "buy_date":        lot["date"].strftime("%Y-%m-%d"),
                    "sell_date":       ts.strftime("%Y-%m-%d"),
                    "hold_days":       hold_days,
                    "term":            term,
                    "shares":          round(sell_shares, 6),
                    "cost_per_share":  round(lot["cost_per_share"], 4),
                    "sell_price":      round(price, 4),
                    "cost_basis":      round(cost_basis, 2),
                    "proceeds":        round(proceeds, 2),
                    "gain_loss":       round(gain_loss, 2),
                    "wash_sale":       False,   # checked below
                    "wash_sale_disallowed": 0.0,
                    "adjustable_basis": round(cost_basis, 2),
                })

                lot["shares"] -= sell_shares
                remaining_to_sell -= sell_shares
                if lot["shares"] < 0.0001:
                    queues[sym].pop(0)

        elif action == "SHORT":
            # Short sales: proceeds recorded at open, cost basis at close
            # Simplified: treat like a sell for reporting purposes
            queues[sym + "_SHORT"] = queues.get(sym + "_SHORT", [])
            queues[sym + "_SHORT"].append({
                "date": ts,
                "shares": shares,
                "cost_per_share": price,
                "is_short": True,
            })

    # ── Wash sale detection ──────────────────────────────────────────────────
    # A wash sale occurs when you sell at a loss AND buy the same security
    # within 30 days before OR after the sale.
    closed_lots = _detect_wash_sales(closed_lots, trades)

    return closed_lots


def _detect_wash_sales(lots: list[dict], all_trades: list[dict]) -> list[dict]:
    """
    Mark lots as wash sales if a loss is realized and the same security
    is repurchased within 30 calendar days before or after the sale.
    Disallowed loss gets added to the cost basis of the replacement shares.
    """
    # Build a set of (symbol, date) for all BUY trades
    buy_dates: dict[str, list[datetime]] = defaultdict(list)
    for t in all_trades:
        if t.get("action") == "BUY":
            buy_dates[t["symbol"]].append(_parse_dt(t["timestamp"]))

    for lot in lots:
        if lot["gain_loss"] >= 0:
            continue   # Only losses can be wash sales

        sell_dt  = datetime.strptime(lot["sell_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        sym      = lot["symbol"]
        window_start = sell_dt - timedelta(days=30)
        window_end   = sell_dt + timedelta(days=30)

        for buy_dt in buy_dates.get(sym, []):
            if window_start <= buy_dt <= window_end and buy_dt != sell_dt:
                lot["wash_sale"]            = True
                lot["wash_sale_disallowed"] = abs(lot["gain_loss"])
                lot["adjustable_basis"]     = lot["cost_basis"] + abs(lot["gain_loss"])
                break

    return lots


# ── Summary calculations ──────────────────────────────────────────────────────

def compute_summary(lots: list[dict]) -> dict:
    """Compute Schedule D style summary totals."""
    st_gains  = sum(l["gain_loss"] for l in lots if l["term"] == "SHORT" and not l["wash_sale"])
    lt_gains  = sum(l["gain_loss"] for l in lots if l["term"] == "LONG"  and not l["wash_sale"])
    wash_disallowed = sum(l["wash_sale_disallowed"] for l in lots)
    net_gain  = st_gains + lt_gains

    st_proceeds   = sum(l["proceeds"]   for l in lots if l["term"] == "SHORT")
    lt_proceeds   = sum(l["proceeds"]   for l in lots if l["term"] == "LONG")
    st_basis      = sum(l["cost_basis"] for l in lots if l["term"] == "SHORT")
    lt_basis      = sum(l["cost_basis"] for l in lots if l["term"] == "LONG")

    wins   = [l for l in lots if l["gain_loss"] > 0]
    losses = [l for l in lots if l["gain_loss"] < 0 and not l["wash_sale"]]

    # Estimated tax — short term at ordinary income rate
    st_tax = max(st_gains, 0) * SHORT_TERM_RATE
    lt_tax = max(lt_gains, 0) * 0.15   # assume 15% bracket for display

    return {
        "total_closed_lots":       len(lots),
        "short_term_gains":        round(st_gains, 2),
        "long_term_gains":         round(lt_gains, 2),
        "net_gain_loss":           round(net_gain, 2),
        "wash_sale_disallowed":    round(wash_disallowed, 2),
        "short_term_proceeds":     round(st_proceeds, 2),
        "short_term_basis":        round(st_basis, 2),
        "long_term_proceeds":      round(lt_proceeds, 2),
        "long_term_basis":         round(lt_basis, 2),
        "winning_trades":          len(wins),
        "losing_trades":           len(losses),
        "largest_gain":            round(max((l["gain_loss"] for l in wins),  default=0), 2),
        "largest_loss":            round(min((l["gain_loss"] for l in losses), default=0), 2),
        "estimated_st_tax":        round(st_tax, 2),
        "estimated_lt_tax":        round(lt_tax, 2),
        "estimated_total_tax":     round(st_tax + lt_tax, 2),
        "wash_sale_count":         sum(1 for l in lots if l["wash_sale"]),
    }


def get_open_positions_unrealized(summary: dict) -> list[dict]:
    """
    Return unrealized gain/loss on open positions from Alpaca summary.
    These are NOT taxable yet but shown for completeness.
    """
    rows = []
    for p in summary.get("positions", []):
        rows.append({
            "symbol":         p["symbol"],
            "shares":         p["shares"],
            "avg_price":      p["avg_price"],
            "current_price":  p["current_price"],
            "unrealized_pnl": p["unrealized_pnl"],
            "unrealized_pct": p["unrealized_pct"],
            "tax_status":     "Open — not yet taxable",
        })
    return rows


# ── Main API ──────────────────────────────────────────────────────────────────

def generate_report(
    start_date: str | None = None,
    end_date: str | None = None,
    trades: list[dict] | None = None,
) -> dict:
    """
    Full tax report for a date range.
    start_date / end_date: "YYYY-MM-DD" strings or None for all time.
    trades: use these instead of data/trades.json (e.g. sample data).
    Returns dict with lots, summary, and export-ready data.
    """
    if trades is None:
        trades = _load_trades()

    # Filter by date range
    if start_date or end_date:
        filtered = []
        for t in trades:
            ts = t.get("timestamp", "")[:10]
            if start_date and ts < start_date:
                continue
            if end_date and ts > end_date:
                continue
            filtered.append(t)
        # Include all BUY trades before start_date for correct FIFO basis
        # even though we only report sales in range
        pre_buys = [t for t in trades
                    if t.get("action") == "BUY"
                    and t.get("timestamp","")[:10] < (start_date or "")]
        all_for_fifo = pre_buys + filtered
    else:
        all_for_fifo = trades
        filtered     = trades

    lots    = _build_tax_lots(all_for_fifo)
    # Filter lots to only those sold within the date range
    if start_date or end_date:
        lots = [l for l in lots
                if (not start_date or l["sell_date"] >= start_date)
                and (not end_date   or l["sell_date"] <= end_date)]

    summary = compute_summary(lots)
    summary["report_start"] = start_date or (lots[0]["buy_date"] if lots else "—")
    summary["report_end"]   = end_date   or (lots[-1]["sell_date"] if lots else "—")

    return {
        "lots":    lots,
        "summary": summary,
        "trades_in_range": [t for t in filtered if t.get("action") in ("SELL","SHORT_COVER")],
    }


# ── CSV export (TurboTax / H&R Block compatible) ─────────────────────────────

def export_csv(lots: list[dict]) -> str:
    """
    Generate a CSV string in Form 8949 / TurboTax format.
    Columns match what TurboTax and H&R Block expect for import.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header matching TurboTax import format
    writer.writerow([
        "Description of Property",
        "Date Acquired",
        "Date Sold or Disposed",
        "Proceeds",
        "Cost or Other Basis",
        "Gain or (Loss)",
        "Short/Long Term",
        "Wash Sale Loss Disallowed",
        "Adjusted Gain or Loss",
        "Hold Days",
    ])

    for l in lots:
        adj_gain = l["gain_loss"] + l["wash_sale_disallowed"]
        writer.writerow([
            f"{l['shares']:.4f} shares {l['symbol']}",
            l["buy_date"],
            l["sell_date"],
            f"{l['proceeds']:.2f}",
            f"{l['cost_basis']:.2f}",
            f"{l['gain_loss']:.2f}",
            l["term"],
            f"{l['wash_sale_disallowed']:.2f}",
            f"{adj_gain:.2f}",
            l["hold_days"],
        ])

    return output.getvalue()


def export_schedule_d_csv(summary: dict) -> str:
    """Generate a Schedule D summary CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Schedule D Summary — Capital Gains and Losses"])
    writer.writerow(["Report Period", f"{summary['report_start']} to {summary['report_end']}"])
    writer.writerow([])
    writer.writerow(["PART I — SHORT-TERM CAPITAL GAINS AND LOSSES (assets held 1 year or less)"])
    writer.writerow(["", "Proceeds", "Cost Basis", "Net Gain/Loss"])
    writer.writerow(["Short-term totals",
                     f"${summary['short_term_proceeds']:,.2f}",
                     f"${summary['short_term_basis']:,.2f}",
                     f"${summary['short_term_gains']:+,.2f}"])
    writer.writerow([])
    writer.writerow(["PART II — LONG-TERM CAPITAL GAINS AND LOSSES (assets held more than 1 year)"])
    writer.writerow(["", "Proceeds", "Cost Basis", "Net Gain/Loss"])
    writer.writerow(["Long-term totals",
                     f"${summary['long_term_proceeds']:,.2f}",
                     f"${summary['long_term_basis']:,.2f}",
                     f"${summary['long_term_gains']:+,.2f}"])
    writer.writerow([])
    writer.writerow(["NET CAPITAL GAIN/LOSS", f"${summary['net_gain_loss']:+,.2f}"])
    writer.writerow(["Wash sale disallowed losses", f"${summary['wash_sale_disallowed']:,.2f}"])
    writer.writerow([])
    writer.writerow(["TAX ESTIMATES (consult a tax professional)"])
    writer.writerow(["Estimated short-term tax (37%)", f"${summary['estimated_st_tax']:,.2f}"])
    writer.writerow(["Estimated long-term tax (15%)", f"${summary['estimated_lt_tax']:,.2f}"])
    writer.writerow(["Estimated total tax liability",  f"${summary['estimated_total_tax']:,.2f}"])

    return output.getvalue()
