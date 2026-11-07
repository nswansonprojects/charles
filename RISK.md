# Charles Risk Disclosure

Version 2.0, effective November 7, 2026

Trading and investing involve substantial risk, and automated trading adds risks of its own. Read this before you use Charles, and again before you turn on live trading. It's part of our [Terms of Service and License Agreement](https://charles-bot.xyz/terms.html). It can't list every risk.

## 1. You can lose money

You can lose money with Charles, including everything you put in. Only use money you can afford to lose. Charles doesn't know your finances, your goals, your age, your taxes or how much risk you can take, so its plan may not suit you. Nothing about Charles is a promise of gains, and past or simulated results don't predict future results.

## 2. Automated trading

Charles runs by itself on a server, so problems can go unnoticed until you look:

- the server, your connection, your broker or a data source can be down, slow or wrong, so trades can be late, missed, repeated or based on bad prices;
- bugs in Charles, in the software it depends on, or in an update can make it trade in ways you didn't expect;
- orders are placed at market prices, which can fill worse than the last price you saw, especially at the open, in fast markets or in thinly traded funds;
- markets can gap, halt or move faster than Charles checks them; it acts at its daily check, not continuously.

Watch your account. Pause on the dashboard stops new orders, and a daily loss limit stops new buys for the rest of a day that has lost too much, but neither one sells anything: Charles has no stop-loss on single positions.

## 3. The default plan, and the plans you choose

- **Mostly the stock market.** The default plan keeps most of its money in an S&P 500 fund, so it carries most of the stock market's risk. The S&P 500 has fallen by more than half before, and it took years to recover.
- **Momentum picks.** It also holds a few single stocks that rose the most recently. Single stocks can fall sharply or become worthless, and momentum can reverse suddenly. Re-picking them every month creates trades, costs and taxes.
- **The safety switch.** When the market's trend turns down, the plan moves to Treasury bill funds; when it turns up, it buys back. That means it sells after a fall has started and buys after a rise has started. In choppy markets it can sell low and buy high again and again, and it can trail the market for years.
- **Aggressive mode and leveraged funds.** In calm, rising markets the plan can hold a 2× fund (or 3×, if you choose it). Leveraged funds reset every day, so over longer periods their returns can be very different from 2× or 3× the index, especially when prices swing; they lose value faster than the market in a fall, and they can lose most of their value in a crash. Their issuers describe them as meant for investors who understand leverage and watch them closely.
- **Your own choices.** Presets, sliders, your own tickers or coins, and settings copied from the leaderboard are your decisions. Concentrated or unusual choices can make the risk much higher.

## 4. AI

Charles uses AI models that can be wrong. The news check can skip a good pick or miss bad news. The chat can give answers that are incorrect, out of date or made up; check anything important yourself, and remember that a settings change it proposes only happens if you press Apply. Self-tune changes settings because of simulations, and a change that looked better on past prices can do worse from now on.

## 5. Simulated, backtested and paper results

HYPOTHETICAL OR SIMULATED PERFORMANCE RESULTS HAVE CERTAIN LIMITATIONS. UNLIKE AN ACTUAL PERFORMANCE RECORD, SIMULATED RESULTS DO NOT REPRESENT ACTUAL TRADING. ALSO, SINCE THE TRADES HAVE NOT BEEN EXECUTED, THE RESULTS MAY HAVE UNDER- OR OVER-COMPENSATED FOR THE IMPACT, IF ANY, OF CERTAIN MARKET FACTORS, SUCH AS LACK OF LIQUIDITY. SIMULATED TRADING PROGRAMS IN GENERAL ARE ALSO SUBJECT TO THE FACT THAT THEY ARE DESIGNED WITH THE BENEFIT OF HINDSIGHT. NO REPRESENTATION IS BEING MADE THAT ANY ACCOUNT WILL OR IS LIKELY TO ACHIEVE PROFITS OR LOSSES SIMILAR TO THOSE SHOWN.

What that means for Charles:

- **Backtests** run today's rules on past prices, with assumed trading costs and without taxes. The rules were designed knowing how the past turned out, and a different period could look much worse.
- **Today's stock list.** A backtest that picks from today's companies only includes companies that survived and grew, so it flatters the plan. The dashboard labels it, and our marketing doesn't use it. Backtests on sector funds don't have that bias, but they're still hypothetical.
- **The leaderboard** runs shared settings forward on real prices from the day after they're submitted, but it places no real orders, and over the short periods it ranks, luck matters more than skill. Rankings change often.
- **Illustrations.** The animated chart on our home page shows how the plan's rules behave with made-up numbers. It isn't a result or a forecast, and sample dashboard pictures use made-up figures.
- **Paper trading** uses real prices but simulated fills. Live results can be worse because of slippage, partial fills, fees, delays and your broker's rules.

## 6. Your broker and the rules

Your broker's rules apply to everything Charles does in your account, including pattern-day-trading rules, settlement times, trading halts, order restrictions and account minimums. Charles buys only with cash and doesn't use margin, but your account settings are yours to manage. SIPC protection, where it applies, covers a broker's failure up to its limits, not investment losses, and doesn't cover cryptocurrency.

## 7. Crypto and prediction markets

In Charles these add-ons use practice money only, but their results can still mislead. Cryptocurrencies are extremely volatile, trade around the clock, and can be affected by exchange failures, hacks and regulatory changes; the practice prices come from Coinbase and can differ from other places. Kalshi event contracts can expire worthless, losing the whole amount paid. Evil Charles takes the other side of Charles's practice bets, and either one can lose.

## 8. Taxes

Trading creates tax events. Gains on investments held a year or less are usually taxed as ordinary income, frequent trading can create many taxable sales, and wash-sale rules can disallow some losses. Charles doesn't calculate or file taxes. Your broker sends tax forms, and you're responsible for your taxes; consider a tax professional.

## 9. Security

Your API keys live on your server. Anyone who gets them, or gets into your server or dashboard, may be able to trade your account. Keep the server updated, keep its login private, use strong passwords on your accounts, and revoke your keys at once if you suspect a problem.

## 10. Things change

Markets, laws, brokers, data sources and the services Charles depends on change. Charles may stop working, behave differently, or need updates, and we may change or stop the website, the leaderboard or the setup service.

## 11. Your confirmation

By using Charles, and especially by turning on live trading, you confirm that you've read and understood this Risk Disclosure, that you can afford the losses that may result, and that you make your own decisions about your money. If anything here is unclear, ask us at support@charles-bot.xyz or a licensed professional before you start.
