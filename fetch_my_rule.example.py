"""
fetch_my_rule.example.py — a starting point for your own Fetch rule. Practice money only.

To use it: copy this file to fetch_my_rule.py (that name is kept out of git, so an update never touches it), change entry(),
test it on past prices, then set "RULE": "mine" in fetch.json (the coin) or fetch-stocks.json (the stock) and restart Fetch:

    cp fetch_my_rule.example.py fetch_my_rule.py
    python fetch_test.py --market coin --rule mine          # how it would have done on the last 30 days, after fees
    sudo systemctl restart charles-fetch                     # or charles-fetch-stocks

Fetch calls entry() once a minute, when a one-minute bar closes, while it holds nothing. Return a dict of the numbers that made
it buy (they're kept with the trade), or None to wait. The buy fills at the next trade's price. Every trade then sells at
TARGET% gain, STOP% loss, or after HOLD minutes (settings in the same json file), whichever comes first.

    closes, highs, lows   lists of one-minute closes, highs and lows, oldest first; the last one is the bar that just closed
    s                     the settings (TARGET, STOP, HOLD, FEE, ...), read-only

NEED is how many minutes of bars entry() needs before it's asked. say() and watching() are optional: the sentences Fetch shows.
Keep entry() fast and simple: plain arithmetic on the lists, no files, no network.
"""
NEED = 30


def entry(closes, highs, lows, s):
    """Example: three closes in a row each higher than the last, and the price above its 30-minute average."""
    avg = sum(closes[-30:]) / 30
    rising = closes[-1] > closes[-2] > closes[-3] > closes[-4]
    if rising and closes[-1] > avg:
        return {"avg_30": avg, "gain_3min_pct": (closes[-1] / closes[-4] - 1) * 100}
    return None


def say(numbers):
    """The sentence when it buys (Fetch adds the price, target and stop)."""
    return f"Three rising minutes in a row, up {numbers['gain_3min_pct']:.2f}%, and the price is over its 30 minute average."


def watching(closes, highs, lows, price, s):
    """The sentence between trades, refreshed every few seconds."""
    avg = sum(closes[-30:]) / 30
    side = "over" if price > avg else "under"
    return f"Price is {side} its 30 minute average of {avg:,.2f}. I am waiting for three rising minutes in a row above it."
