# Charles Terms of Service and License Agreement

Version 2.0, effective November 7, 2026

These terms are a contract between you and the people who develop Charles and run charles-bot.xyz ("Charles", "we", "us"). They cover the Charles software (the trading bot, its dashboard, installer, commands and add-ons), the website charles-bot.xyz, the leaderboard and its challenges, the paid "Set it up for me" service, and any help we give you. Our [Privacy Policy](https://charles-bot.xyz/privacy.html), [Risk Disclosure](https://charles-bot.xyz/risk.html) and [Refund Policy](https://charles-bot.xyz/refunds.html) are part of these terms.

You accept these terms when you install Charles, when you buy the setup service, when you turn on live trading, or when you otherwise use Charles. If you don't agree, don't install or use Charles.

**Please read section 18. It says that disputes are settled by binding individual arbitration instead of in court or in a class action, and how you can opt out within 30 days.**

**The short version.** The full terms below are what count.

- You must be 18 or older (or the age of majority where you live) and allowed to trade where you live.
- Charles is free software. You run it on your own server, with your own accounts and keys. We never hold your money or your keys.
- It trades paper (practice) money by default. Live trading happens only if you turn it on yourself.
- Charles is a tool, not investment advice. It promises no gains, and you can lose money, including everything you put in.
- You're responsible for your accounts, your trades and your taxes. The software comes as is, with no warranty.
- Disputes go to individual arbitration, not class actions (section 18), unless you opt out within 30 days.

## 1. Who may use Charles

You may use Charles only if all of these are true:

- you are at least 18 years old, or the age of majority where you live if that is older;
- you can form a binding contract, and no law or court order stops you from using Charles;
- using an automated trading tool, and the brokerage, exchange, data and AI services you connect to it, is legal for you where you live;
- every account you connect is in your own name, or you are authorized to trade it; and
- you are not in a country or region under comprehensive U.S. sanctions, and you are not on a U.S. government list of restricted persons.

The paid setup service is only for people in the United States. You are responsible for following the laws, and your broker's and exchanges' rules, that apply to you. If you use Charles for a company or other organization, you confirm you're authorized to accept these terms for it.

## 2. What Charles is, and what it isn't

Charles is software that follows rules: its default plan, a preset, or settings you choose. It doesn't know your finances or goals, and it doesn't tailor advice to you. It runs on a server you control and trades through a brokerage account you opened yourself.

We are not a broker-dealer, investment adviser, commodity trading advisor, financial planner or fiduciary, and using Charles doesn't make us one. We don't hold or move your money or investments. We have no access to your brokerage or other accounts, we can't place, change or cancel orders in them, and we have no authority to trade for you. We're paid only for software services (such as the one-time setup fee), never based on your trades, the size of your account or its results.

You decide whether to use Charles, which settings to use, whether to copy anyone's settings, and whether to turn on live trading.

## 3. Not investment, financial, legal or tax advice

Nothing in Charles is investment, financial, legal, tax or accounting advice, or an offer, solicitation or recommendation to buy, sell or hold any security, fund, cryptocurrency, contract or other asset, or to follow any strategy. That includes the default plan and presets, the dashboard and its chat and AI answers, alerts, lessons, reports and findings, the leaderboard and challenges, simulations and backtests, the website, and anything we say in emails or support. All of it is general information about how the software works, and it's the same for everyone.

Simulated, backtested and paper results are hypothetical, have real limits, and don't reliably predict future results (see the [Risk Disclosure](https://charles-bot.xyz/risk.html)). Before you trade real money, consider asking a licensed financial professional whether a strategy fits your goals, your finances and how much risk you can take.

## 4. Your license to the software

We grant you a free, non-exclusive, worldwide license to install, run, copy and modify the Charles software, and to share copies of its source code, as long as every copy you share includes these terms and anyone who uses it accepts them.

You may not:

- remove or hide these terms or our copyright notices;
- present Charles, or a version of it, as investment advice, a managed account or a signal service, or as something that promises returns;
- use our names, logos or the charles-bot.xyz domain in a way that suggests your version, service or product comes from us or is endorsed by us (see section 13); or
- use Charles in any way section 12 forbids.

Parts of Charles come from others under their own licenses (for example the Python packages it installs). Those licenses apply to those parts, and nothing here limits the rights they give you. This license ends automatically if you break these terms. When it ends, stop using Charles and delete it.

## 5. Paper trading and live trading

Charles starts in paper trading: it places practice orders in your broker's paper account, at real prices with simulated fills. Paper results aren't real results, and live trading can do worse.

Live trading (real money) is available only for the stock bot, only through your own Alpaca account, and only after you confirm you meet section 1, accept these terms, add your live keys on your own server, and turn it on yourself (`charles-live on`). While live trading is on:

- every order Charles places is your order, in your account, and you are responsible for it;
- you can lose money, including all the money in the account, and leveraged funds (such as 2× and 3× index funds) can fall much faster than the market;
- you are responsible for fees, taxes, your broker's rules (including pattern-day-trading, settlement and margin rules), and for watching what Charles does;
- you can stop it at any time: Pause on the dashboard stops new orders, and `charles-live off` goes back to paper. Neither sells what the account already holds.

The crypto and Kalshi add-ons trade practice money only. If we ever offer real-money trading for them, it will need your separate, explicit consent.

## 6. Your server, accounts, keys and security

You choose and pay for your own server, brokerage account and any other service you connect (for example DigitalOcean, Alpaca, Anthropic, Groq, Kalshi or Coinbase), under their own terms. Your keys stay on your server, and we never receive them. Keep your server, its dashboard login and your keys secure, install updates, and don't share access: you're responsible for anything done with your server and your accounts. If you think someone else got access, revoke your keys with the provider and run `charles-login --logout-all`.

## 7. Other people's services and data

Charles depends on services we don't control: your broker, your server host, market data and news sources, and AI models. They can be late, wrong, unavailable, changed or discontinued, they have their own terms and fees, and we aren't responsible for them. Mentioning or linking to another company isn't an endorsement of it, and none of them endorses Charles. Alpaca, DigitalOcean, Stripe, Anthropic, Claude, Groq, Kalshi, Coinbase and other names are trademarks of their owners.

## 8. The software has limits

Charles can have bugs, stop running, use late or wrong market data, misread the news, or act on an AI model's mistake. Markets can move faster than it reacts, and orders can fill at worse prices than expected. Self-tune can change your settings based on simulations that may not hold up. Updates can change how Charles trades: when an update changes the trading rules it says so and asks before installing, and you decide when to update. We may change, pause or stop any part of Charles, including the website, the leaderboard and the setup service, at any time; the copy on your server keeps running as it is. Watch your account, and don't put in money you can't afford to lose.

## 9. The leaderboard, challenges and sharing

The leaderboard and challenges are optional. When you submit settings you choose a display name or stay anonymous, and what you submit is public: your settings, your display name, their simulated results and when you joined. Results are simulated by our server on market prices from the day after you submit. They aren't taken from your account and aren't anyone's real trading. A high rank, especially over a short time, is often luck, and it isn't a recommendation or an endorsement by us. If you copy someone's settings, that's your own decision.

You give us a free, worldwide, perpetual license to store, run, display and share the settings and name you submit, and to let others copy those settings, as part of the leaderboard and challenges. Withdrawn entries keep their record, marked retired, so nobody can hide a bad run. Don't submit anything false, misleading or offensive, or anything that infringes someone else's rights, and don't use a name that pretends to be someone else. Nobody is paid for entries, and nobody may pay or be paid to promote settings. We may remove entries or names, or block access, at any time.

## 10. The paid setup service ("Set it up for me")

**What you buy.** A one-time, automated setup: we create one new server (a "droplet") in your own DigitalOcean account and install the same free Charles software that anyone can install. The price is shown on the setup page before you pay, in US dollars, plus any sales tax shown at checkout. It's a single payment, not a subscription, and nothing renews.

**What it doesn't include.** The server itself, which DigitalOcean bills to you directly (a few dollars a month at its current prices, until you delete it); your brokerage account and keys; data, AI or other services you add; running, monitoring or managing Charles after setup; and any trading results.

**What you give us, and what we do with it.** Your email address, for your receipt and the setup emails. A DigitalOcean API token, which we use once to create your server (and, if you give us one, to add your SSH public key to your account) and then discard: we never store it. You authorize us to use that token only to create that one server and add that one key, nothing else. Please delete the token when your setup is done. Our [Privacy Policy](https://charles-bot.xyz/privacy.html) explains exactly what we keep.

**Payment.** Payments are processed by Stripe, and we never see your full card number. You authorize the charge when you pay, and you confirm you're allowed to use that payment method.

**Delivery.** Setup usually finishes within 15 minutes after you connect DigitalOcean. It's complete when your server's dashboard answers, and we email you when it does.

**Refunds.** Our [Refund Policy](https://charles-bot.xyz/refunds.html) gives you an automatic full refund when your setup can't be finished, and a full refund on request for a period after you pay. Charges from DigitalOcean or other services aren't ours to refund.

**Your acceptance.** You accept these terms, and confirm that you've read the Privacy Policy and the Risk Disclosure, before you pay. We record which version you accepted and when, with your order and your payment.

## 11. Fees and taxes

The Charles software is free. Prices for paid services are in US dollars and shown before you pay. We may change them for future purchases, never for one you've already made. You're responsible for taxes on your purchases (we collect sales tax where we're required to, and show it at checkout), and for the fees of the services you use with Charles.

## 12. Acceptable use

Don't use Charles, the leaderboard or the setup service to:

- break any law or regulation, or your broker's or an exchange's rules;
- manipulate any market, including by spoofing, layering, wash trading, pump-and-dump schemes, or spreading false or misleading information about a security or other asset;
- trade an account you aren't authorized to trade, or use a DigitalOcean token, payment card or account that isn't yours;
- attack, overload, probe or get around the limits or security of our website, leaderboard or setup service, or interfere with other people's use of them;
- collect other users' information, or scrape our services beyond normal personal use; or
- offer Charles to others as investment advice, a managed account or a signal service, or with promises of returns.

## 13. Our names and content

The names "Charles", "Evil Charles" and "charles-bot.xyz", our logos and terrier character, and the website's text, pictures and design (apart from the software code licensed in section 4) belong to us. You may say truthfully that your project is based on or works with Charles, but don't use our names or logos in a way that could make people think something comes from us or is endorsed by us. If you send us ideas or feedback, we may use them freely, without owing you anything.

## 14. Privacy

Our [Privacy Policy](https://charles-bot.xyz/privacy.html) explains what we collect, how we use it and the choices you have. In short: the Charles software runs on your server and sends us nothing unless you use the leaderboard, and our setup service never stores your DigitalOcean token, your SSH key or your email address.

## 15. No warranty

CHARLES, THE WEBSITE, THE LEADERBOARD AND THE SETUP SERVICE ARE PROVIDED "AS IS" AND "AS AVAILABLE", WITH ALL FAULTS AND WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED OR STATUTORY, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT AND ACCURACY, AND ANY WARRANTY THAT CHARLES WILL BE UNINTERRUPTED, SECURE OR ERROR-FREE, OR THAT IT WILL MAKE OR SAVE YOU MONEY, TO THE FULLEST EXTENT THE LAW ALLOWS. Some places don't allow some of these exclusions, so some of them may not apply to you.

## 16. Limitation of liability

TO THE FULLEST EXTENT THE LAW ALLOWS: (A) WE ARE NOT LIABLE FOR ANY TRADING OR INVESTMENT LOSSES, LOST PROFITS OR OPPORTUNITIES, LOST DATA, OR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY OR PUNITIVE DAMAGES ARISING FROM CHARLES OR THESE TERMS, EVEN IF WE WERE TOLD THEY WERE POSSIBLE; AND (B) OUR TOTAL LIABILITY FOR ALL CLAIMS ABOUT CHARLES OR THESE TERMS IS LIMITED TO THE GREATER OF WHAT YOU PAID US IN THE 12 MONTHS BEFORE THE CLAIM AROSE, OR US$50.

These limits apply whatever the legal theory (contract, tort including negligence, or anything else), and they're part of why the software can be free and the setup inexpensive. They don't limit liability that the law doesn't allow to be limited, such as for fraud. Some places don't allow these limits, so they apply only as far as the law allows.

## 17. Your responsibility to us

To the extent the law allows, you agree to defend and indemnify us against third-party claims, losses and costs (including reasonable legal fees) that arise from your use of Charles, your trades and accounts, the settings or names you share, or your breaking these terms or the law. We'll tell you about any such claim, and we may take part in our own defense.

## 18. Disputes: talk to us first, then individual arbitration

**Talk to us first.** Most problems can be fixed quickly. Before starting a claim, email support@charles-bot.xyz with your name, how to reach you, what happened and what you'd like us to do. We'll do the same before starting any claim against you. Both sides will try in good faith to resolve it within 60 days.

**Arbitration.** If it isn't resolved, you and we agree that any dispute, claim or controversy arising from or relating to Charles or these terms, including whether this section applies or can be enforced, will be settled by binding individual arbitration instead of in court, except as allowed below. The American Arbitration Association (AAA) runs it under its Consumer Arbitration Rules, before one arbitrator. Unless both sides agree otherwise, it's held by video, by phone, on written submissions, or in person in the county where you live. If your claim is for US$10,000 or less, we will pay all AAA filing, administration and arbitrator fees, unless the arbitrator finds your claim frivolous. The arbitrator can award the same individual relief a court could, but only to you individually. The Federal Arbitration Act governs this section.

**Exceptions.** Either side may bring an individual claim in small claims court if it qualifies, and either side may ask a court to stop the misuse of its intellectual property.

**No class actions and no juries.** YOU AND WE EACH GIVE UP THE RIGHT TO A JURY TRIAL AND THE RIGHT TO TAKE PART IN A CLASS, COLLECTIVE OR REPRESENTATIVE ACTION OR ARBITRATION. Claims may be brought only individually. If this waiver can't be enforced for a particular claim, that claim (and only that claim) goes to court instead of arbitration, and the court decides it after any individual arbitrations are finished.

**Opting out.** You can opt out of this section 18 within 30 days after you first accept these terms, by emailing support@charles-bot.xyz with the subject "Arbitration opt-out", your name, and the email address you used with Charles, if any. Opting out doesn't change anything else in these terms.

**Time limit.** To the extent the law allows, any claim must be started within one year after it arose, or it's permanently barred.

**Changes to this section.** If we change this section, the change won't apply to a dispute we already knew about, and you can reject it by emailing us within 30 days after it takes effect.

## 19. Governing law and courts

These terms are governed by the laws of the U.S. state where our business is organized, without regard to conflict-of-laws rules, and by U.S. federal law; the Federal Arbitration Act governs section 18. For anything that goes to court instead of arbitration, you and we agree to the exclusive jurisdiction of the state and federal courts where our business is organized, except that you may always bring a claim in small claims court where you live. Nothing in these terms takes away a consumer protection right that the law where you live doesn't allow a contract to take away.

## 20. Changes to these terms

We may update these terms. The version and effective date are at the top, and the release notes of any update that changes them say so. For a material change we'll give reasonable notice before it takes effect, on the website and in the release notes. Using Charles after a change takes effect means you accept it, and live trading pauses until you accept the new version (`charles-terms`). A purchase stays under the version you accepted when you made it.

## 21. Ending

You can stop using Charles at any time: stop it and delete it from your server, and delete the server if you no longer need it. We may suspend or end your access to the leaderboard or the setup service if you break these terms, or if we stop offering them. Sections 3, 9 (the license you give us), 13 and 15 to 23 continue after these terms end.

## 22. Export controls and sanctions

Charles is subject to U.S. export control and sanctions laws. Don't use, export or re-export it in violation of them, including to a country or region under comprehensive U.S. sanctions or to anyone on a U.S. government list of restricted persons.

## 23. General

These terms, with the Privacy Policy, the Risk Disclosure and the Refund Policy, are the whole agreement between you and us about Charles, and they replace any earlier version. If a court or arbitrator finds part of them unenforceable, that part is changed only as much as needed, or removed, and the rest stays in effect. If we don't enforce a right, we haven't given it up. You may not transfer these terms without our consent; we may transfer them to a business that takes over Charles. Neither side is responsible for a failure caused by events beyond its reasonable control. Nothing here creates a partnership, employment or agency relationship, and nobody else has rights under these terms. You agree that we may give you notices electronically (by email, on the website or in the software), and that clicking, ticking or typing to accept, and electronic records, count the same as a signature on paper. Headings are only for convenience.

## 24. Contact

- **Charles**
- Email: [support@charles-bot.xyz](mailto:support@charles-bot.xyz)
- Website: https://charles-bot.xyz

Questions about the software can also go to GitHub at https://github.com/nswansonprojects/charles/issues. That page is public, so never post keys or personal information there.
