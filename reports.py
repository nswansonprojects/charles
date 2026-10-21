"""
reports.py — Weekly, monthly, and yearly report generator.
Produces formatted summaries sent via Telegram and saved as CSV files.
Compares performance against SPY buy-and-hold benchmark.
"""

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import requests

log = logging.getLogger(__name__)

DATA_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def _load_trades() -> list:
    p = os.path.join(DATA_DIR, "trades.json")
    if not os.path.exists(p): return []
    with open(p) as f: return json.load(f)


def _load_equity() -> list:
    p = os.path.join(DATA_DIR, "equity_curve.json")
    if not os.path.exists(p): return []
    with open(p) as f: return json.load(f)


def _spy_return(start_date: str, end_date: str) -> float:
    """Get SPY return from start_date through end_date (both inclusive) via yfinance."""
    try:
        import yfinance as yf
        spy  = yf.Ticker("SPY")
        # yfinance's end is exclusive: without the +1 day the last day is dropped, and
        # start == end asks for nothing (logged as "$SPY: possibly delisted").
        end_excl = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")
        hist = spy.history(start=start_date, end=end_excl)
        if hist.empty or len(hist) < 2: return 0.0
        return round((hist["Close"].iloc[-1] - hist["Close"].iloc[0])
                     / hist["Close"].iloc[0] * 100, 2)
    except Exception:
        return 0.0


def _filter_by_period(trades: list, equity: list,
                       start_dt: datetime, end_dt: datetime) -> tuple:
    """Filter trades and equity curve to a date range."""
    t_filtered = []
    for t in trades:
        ts = datetime.fromisoformat(t["timestamp"][:19]).replace(tzinfo=timezone.utc)
        if start_dt <= ts <= end_dt:
            t_filtered.append(t)

    e_filtered = []
    for e in equity:
        ts = datetime.fromisoformat(e["ts"][:19]).replace(tzinfo=timezone.utc)
        if start_dt <= ts <= end_dt:
            e_filtered.append(e)

    return t_filtered, e_filtered


def _compute_stats(trades: list, equity: list,
                   start_value: float = 1000.0) -> dict:
    """Compute performance statistics for a period."""
    sells  = [t for t in trades if t["action"] == "SELL" and "pnl" in t]
    buys   = [t for t in trades if t["action"] == "BUY"]
    wins   = [t for t in sells if t["pnl"] > 0]
    losses = [t for t in sells if t["pnl"] <= 0]

    total_pnl   = sum(t["pnl"] for t in sells)
    start_val   = equity[0]["value"] if equity else start_value
    end_val     = equity[-1]["value"] if equity else start_value
    period_ret  = (end_val - start_val) / start_val * 100 if start_val else 0

    # Max drawdown
    peak, max_dd = start_val, 0
    for e in equity:
        v = e["value"]
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    # Sharpe (simplified — annualized from daily returns)
    if len(equity) > 1:
        daily_rets = []
        for i in range(1, len(equity)):
            r = (equity[i]["value"] - equity[i-1]["value"]) / equity[i-1]["value"]
            daily_rets.append(r)
        import statistics
        if len(daily_rets) > 1 and statistics.stdev(daily_rets) > 0:
            sharpe = (statistics.mean(daily_rets) / statistics.stdev(daily_rets)) * (252 ** 0.5)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Best/worst trade
    best  = max(sells, key=lambda t: t["pnl"]) if sells else None
    worst = min(sells, key=lambda t: t["pnl"]) if sells else None

    # By symbol
    by_sym = defaultdict(list)
    for t in sells:
        by_sym[t["symbol"]].append(t["pnl"])
    sym_pnl = {s: round(sum(v), 2) for s, v in by_sym.items()}
    best_sym  = max(sym_pnl, key=sym_pnl.get) if sym_pnl else "—"
    worst_sym = min(sym_pnl, key=sym_pnl.get) if sym_pnl else "—"

    return {
        "start_value":   round(start_val, 2),
        "end_value":     round(end_val, 2),
        "period_return": round(period_ret, 2),
        "total_pnl":     round(total_pnl, 2),
        "total_trades":  len(trades),
        "closed_trades": len(sells),
        "buy_trades":    len(buys),
        "win_count":     len(wins),
        "loss_count":    len(losses),
        "win_rate":      round(len(wins)/len(sells)*100, 1) if sells else 0,
        "avg_win":       round(sum(t["pnl"] for t in wins)/len(wins), 2) if wins else 0,
        "avg_loss":      round(sum(t["pnl"] for t in losses)/len(losses), 2) if losses else 0,
        "max_drawdown":  round(max_dd, 2),
        "sharpe_ratio":  round(sharpe, 3),
        "best_trade":    {"symbol": best["symbol"], "pnl": best["pnl"]} if best else None,
        "worst_trade":   {"symbol": worst["symbol"], "pnl": worst["pnl"]} if worst else None,
        "best_symbol":   best_sym,
        "worst_symbol":  worst_sym,
        "symbol_pnl":    sym_pnl,
    }


# ── Report generators ─────────────────────────────────────────────────────────

def report_window(period: str, now: datetime, previous: bool = False,
                  custom_start: str = None, custom_end: str = None) -> tuple:
    """(start_dt, end_dt, label, fname) for a report period.
    previous=True gives the last complete month/year (the scheduled reports run on
    the 1st, when "this month so far" is a single day); otherwise it is to date."""
    if period == "weekly":
        start_dt, end_dt = now - timedelta(days=7), now
        label    = f"Week of {start_dt.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"
        fname    = f"weekly_{now.strftime('%Y_%m_%d')}"
    elif period == "monthly":
        this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if previous:
            end_dt   = this_month - timedelta(seconds=1)
            start_dt = end_dt.replace(day=1, hour=0, minute=0, second=0)
        else:
            start_dt, end_dt = this_month, now
        label    = start_dt.strftime("%B %Y")
        fname    = f"monthly_{start_dt.strftime('%Y_%m')}"
    elif period == "yearly":
        this_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if previous:
            end_dt   = this_year - timedelta(seconds=1)
            start_dt = end_dt.replace(month=1, day=1, hour=0, minute=0, second=0)
        else:
            start_dt, end_dt = this_year, now
        label    = str(start_dt.year)
        fname    = f"yearly_{start_dt.year}"
    elif period == "custom" and custom_start and custom_end:
        start_dt = datetime.fromisoformat(custom_start).replace(tzinfo=timezone.utc)
        end_dt   = datetime.fromisoformat(custom_end).replace(tzinfo=timezone.utc)
        label    = f"{custom_start} to {custom_end}"
        fname    = f"custom_{custom_start}_to_{custom_end}"
    else:
        raise ValueError(f"Unknown period: {period}")
    return start_dt, end_dt, label, fname


def generate_report(period: str, custom_start: str = None,
                    custom_end: str = None, previous: bool = False) -> dict:
    """
    Generate a report for 'weekly', 'monthly', 'yearly', or 'custom'.
    previous=True: the last complete month/year (used by the scheduled reports).
    Returns stats dict + saves CSV files to reports/ folder.
    """
    now = datetime.now(timezone.utc)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    start_dt, end_dt, label, fname = report_window(period, now, previous,
                                                   custom_start, custom_end)

    trades, equity = _filter_by_period(_load_trades(), _load_equity(),
                                        start_dt, end_dt)
    stats = _compute_stats(trades, equity)

    # SPY benchmark
    spy_ret = _spy_return(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
    stats["spy_return"]   = spy_ret
    stats["alpha"]        = round(stats["period_return"] - spy_ret, 2)
    stats["period"]       = period
    stats["label"]        = label
    stats["generated_at"] = now.isoformat()

    # Save CSV files
    _save_trade_csv(trades, fname)
    _save_summary_csv(stats, fname)

    log.info(f"📊 {period.capitalize()} report generated: {label}")
    return stats


def _save_trade_csv(trades: list, fname: str):
    """Save trade-by-trade CSV."""
    path = os.path.join(REPORTS_DIR, f"{fname}_trades.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "Symbol", "Action", "Price", "Value",
                     "P&L", "P&L %", "Confidence", "Reasoning", "Source"])
        for t in trades:
            w.writerow([
                t.get("timestamp","")[:19],
                t.get("symbol",""),
                t.get("action",""),
                t.get("price",""),
                t.get("value",""),
                t.get("pnl",""),
                t.get("pnl_pct",""),
                t.get("confidence",""),
                t.get("reasoning",""),
                t.get("source",""),
            ])
    log.info(f"  💾 Trade CSV: {path}")


def _save_summary_csv(stats: dict, fname: str):
    """Save summary statistics CSV."""
    path = os.path.join(REPORTS_DIR, f"{fname}_summary.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Metric", "Value"])
        rows = [
            ("Period",          stats.get("label","")),
            ("Start Value",     f"${stats.get('start_value',0):,.2f}"),
            ("End Value",       f"${stats.get('end_value',0):,.2f}"),
            ("Total P&L",       f"${stats.get('total_pnl',0):+,.2f}"),
            ("Period Return",   f"{stats.get('period_return',0):+.2f}%"),
            ("SPY Return",      f"{stats.get('spy_return',0):+.2f}%"),
            ("Alpha vs SPY",    f"{stats.get('alpha',0):+.2f}%"),
            ("Total Trades",    stats.get("total_trades",0)),
            ("Closed Trades",   stats.get("closed_trades",0)),
            ("Win Rate",        f"{stats.get('win_rate',0):.1f}%"),
            ("Avg Win",         f"${stats.get('avg_win',0):,.2f}"),
            ("Avg Loss",        f"${stats.get('avg_loss',0):,.2f}"),
            ("Max Drawdown",    f"-{stats.get('max_drawdown',0):.2f}%"),
            ("Sharpe Ratio",    stats.get("sharpe_ratio",0)),
            ("Best Trade",      str(stats.get("best_trade",""))),
            ("Worst Trade",     str(stats.get("worst_trade",""))),
            ("Best Symbol",     stats.get("best_symbol","—")),
            ("Worst Symbol",    stats.get("worst_symbol","—")),
        ]
        w.writerows(rows)
    log.info(f"  💾 Summary CSV: {path}")


# ── Telegram report sender ────────────────────────────────────────────────────

def send_report_telegram(stats: dict):
    """Send a formatted report to Telegram."""
    try:
        import alerts
        period  = stats.get("period","").capitalize()
        label   = stats.get("label","")
        ret     = stats.get("period_return", 0)
        pnl     = stats.get("total_pnl", 0)
        spy     = stats.get("spy_return", 0)
        alpha   = stats.get("alpha", 0)
        wins    = stats.get("win_rate", 0)
        sharpe  = stats.get("sharpe_ratio", 0)
        dd      = stats.get("max_drawdown", 0)
        trades  = stats.get("closed_trades", 0)
        sign    = "+" if ret >= 0 else ""
        icon    = "📈" if ret >= 0 else "📉"
        alpha_icon = "✅" if alpha >= 0 else "⚠️"

        msg = (
            f"{icon} {period} Report — {label}\n"
            f"{'─'*30}\n"
            f"Return:       {sign}{ret:.2f}%  (${pnl:+,.2f})\n"
            f"vs SPY:       {spy:+.2f}%  {alpha_icon} Alpha: {alpha:+.2f}%\n"
            f"Win rate:     {wins:.1f}%  ({trades} closed trades)\n"
            f"Sharpe:       {sharpe:.3f}\n"
            f"Max drawdown: -{dd:.2f}%\n"
            f"Best sym:     {stats.get('best_symbol','—')}\n"
            f"Worst sym:    {stats.get('worst_symbol','—')}"
        )
        alerts._send(msg)
        log.info(f"📱 {period} report sent to Telegram")
    except Exception as e:
        log.warning(f"Report Telegram send failed: {e}")


# ── Sample data generators ────────────────────────────────────────────────────

def generate_sample_report_csv(period: str = "monthly") -> str:
    """
    Generate a realistic sample/example CSV showing what a report looks like.
    Used for dashboard preview when no real data exists yet.
    """
    import random
    random.seed(42)

    os.makedirs(REPORTS_DIR, exist_ok=True)

    if period == "weekly":
        label = "Week of May 26 – Jun 1, 2026 (SAMPLE)"
    elif period == "monthly":
        label = "May 2026 (SAMPLE)"
    else:
        label = "2026 Year-to-Date (SAMPLE)"

    # Generate sample trades
    symbols = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL"]
    trades  = []
    base_dt = datetime(2026, 5, 1, 10, 30, tzinfo=timezone.utc)
    for i in range(20):
        sym    = random.choice(symbols)
        action = random.choice(["BUY", "SELL"])
        price  = round(random.uniform(150, 500), 2)
        value  = round(random.uniform(50, 150), 2)
        pnl    = round(random.uniform(-15, 25), 2) if action == "SELL" else ""
        pnl_pct= round(pnl / value * 100, 2) if pnl != "" else ""
        trades.append({
            "timestamp":  (base_dt + timedelta(hours=i*8)).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":     sym,
            "action":     action,
            "price":      price,
            "value":      value,
            "pnl":        pnl,
            "pnl_pct":    pnl_pct,
            "confidence": round(random.uniform(0.65, 0.95), 2),
            "reasoning":  f"RSI oversold + MACD bullish crossover + positive FinBERT on {sym}",
            "source":     "llm:groq",
        })

    fname = f"sample_{period}"
    _save_trade_csv(trades, fname)

    # Sample summary
    sample_stats = {
        "label":          label,
        "start_value":    1000.00,
        "end_value":      1087.34,
        "total_pnl":      87.34,
        "period_return":  8.73,
        "spy_return":     3.21,
        "alpha":          5.52,
        "closed_trades":  10,
        "win_rate":       70.0,
        "avg_win":        18.42,
        "avg_loss":       -8.14,
        "max_drawdown":   4.21,
        "sharpe_ratio":   1.847,
        "best_trade":     {"symbol": "NVDA", "pnl": 31.20},
        "worst_trade":    {"symbol": "TSLA", "pnl": -12.40},
        "best_symbol":    "NVDA",
        "worst_symbol":   "TSLA",
    }
    _save_summary_csv(sample_stats, fname)

    return os.path.join(REPORTS_DIR, f"{fname}_summary.csv")
