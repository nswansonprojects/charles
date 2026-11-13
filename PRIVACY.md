# Charles Privacy Policy

Version 2.2, effective November 13, 2026

This policy explains what information Charles LLC, a Colorado limited liability company ("we", "us", "our") collects through the website charles-bot.xyz, the leaderboard, the paid "Set it up for me" service and emails with us, what we do with it, and your choices. It's part of our [Terms of Service and License Agreement](https://charles-bot.xyz/terms.html).

**The short version.**

- Charles runs on your own server. Your keys, trades, account details and the bot's records stay there, and we never receive them.
- We have no analytics, no advertising and no tracking cookies, and we don't sell or share your information for advertising.
- When you buy the setup service, Stripe handles your payment and keeps your email for the receipt. We never store your DigitalOcean token, your SSH key or your email address.
- The leaderboard only knows what you choose to submit.

## 1. The Charles software on your server

The bot, its dashboard and its records run on a server you control. We don't receive your API keys, orders, positions, balances, settings or anything else stored there, and the software has no analytics or "phone home" reporting. Your server talks directly to the services you set it up with, under their own privacy policies:

- your broker (Alpaca), for your account, orders and market data;
- public price and news sources, such as Yahoo Finance, Google News and Coinbase's public prices;
- Groq, an AI provider, only if you add your own Groq key: it receives news headlines about a stock before Charles first buys it, to check them for serious bad news;
- for the Kalshi add-on, Kalshi's public market data and public weather and sports data sources;
- GitHub, to check for and download updates; and
- our leaderboard, as described in section 3.

If you use an AI tool on your server yourself, such as Claude Code, what it reads and sends is between you and that tool's provider; we don't receive it.

## 2. Visiting the website

Our web server keeps standard access logs: your IP address, the date and time, the page or file requested, the response, the referring page and your browser's user agent. We use them only to run and secure the site, and they're deleted after 14 days.

The website sets no cookies and loads nothing from advertising, analytics or social media companies. Its fonts are served from our own server. Choices you make on a page, such as light or dark mode, are saved in your own browser's storage and never sent to us. Because we don't track you, "Do Not Track" and Global Privacy Control signals don't change anything here, but we respect their intent.

## 3. The leaderboard and challenges

The leaderboard only receives what you choose to send from your dashboard: the settings you submit or enter in a challenge, a display name if you pick one (or none, to stay anonymous), and your install's public key, a random identifier your server creates so that only it can change or withdraw its entries. It doesn't receive your account, keys, trades or results; it runs your settings on market prices itself.

What you submit is public: your settings, your display name, their simulated results, dates and how often they've been copied. For challenges we also keep a one-way, salted fingerprint of your IP address (not the address itself) to limit how many entries come from one place. When your dashboard shows the leaderboard, your server reads the public list from us, and our web server logs see its IP address as in section 2.

Entries stay on the leaderboard while it runs; withdrawn entries stay as retired so nobody can hide a bad run. We'll remove your display name from your entries on request.

## 4. The paid setup service

When you buy "Set it up for me":

- **Your email address** goes to Stripe, which takes the payment and keeps your email with it for your receipt. When we email you about your setup, we get your address from Stripe at that moment and hand it to our email provider to deliver the message. We don't save it in our own database, files or logs.
- **Your payment details** go straight to Stripe. We never see your full card number.
- **Your DigitalOcean API token** is used once, in memory, to create your server, and is sent only to DigitalOcean. It's never written to our disks, database or logs, and it's discarded as soon as your server exists. Please delete it in DigitalOcean when you're done.
- **Your SSH public key**, if you give one, goes only to DigitalOcean, to add it to your account. We don't keep it.
- **Your broker keys** never pass through us. You type them on a page served by your own new server, and they go from your browser straight to that server.
- **Your order record** holds no contact details: a random order number, its status, the version of our terms you accepted and when, the price and amount paid, Stripe's references for the checkout and the payment, when it was paid, finished and refunded, the server size you chose, and your new server's region, DigitalOcean ID, IP address and dashboard link. We delete the server details 24 hours after your setup finishes (we email them to you first), and the rest of the record after 2 years. We keep it that long to handle refunds, disputes and our accounts.
- **Your IP address** is used only as a one-way, salted fingerprint, held in memory for up to an hour to limit repeated attempts. Our web server doesn't log requests to the setup service.

Stripe keeps payment records as its own [privacy policy](https://stripe.com/privacy) and the law require.

## 5. Emails with us

The emails we send are about your purchase or setup: payment received, server ready, setup problems and refunds. We don't send marketing emails. If you write to us, we keep the conversation in our mailbox as long as we need it to help you, and for up to 3 years for our records.

## 6. How we use information

Only to: provide the website, the leaderboard and the setup service; take payments and give refunds; send the emails above and answer you; keep our services secure and prevent abuse and fraud; and meet legal, tax and accounting obligations. We don't use it for advertising, we don't build profiles, and we don't make automated decisions that have legal or similarly significant effects on you.

## 7. Who we share it with

We don't sell your personal information, and we don't share it for cross-context behavioral advertising. We share it only with the service providers that run parts of Charles for us, under their own privacy and security commitments, and only what each needs:

- **Stripe**: payments, receipts, refunds and fraud prevention;
- **Mailgun (Sinch)**: delivering our emails; it keeps delivery logs for a limited time;
- **DigitalOcean**: hosting our website, leaderboard and setup service; and, on your instruction, creating your server in your own account;
- **GitHub**: hosting the Charles source code and releases you download; and
- **our email provider**: holding the emails you send us.

We may also disclose information if the law requires it, to protect anyone's rights, safety or property, or to a business that takes over Charles, which would remain bound by this policy.

## 8. Security

We collect as little as we can, and we design the setup service so there's nothing sensitive to steal: tokens are never stored, contact details stay with Stripe, and server details are deleted within 24 hours. Everything travels over HTTPS, and our servers are locked down and kept updated. No system is perfectly secure; if a breach affected your information, we would tell you as the law requires.

## 9. Your choices and rights

You can ask us to tell you what personal information we hold about you, to correct it, or to delete it, by emailing support@charles-bot.xyz. We'll answer within 45 days and won't treat you differently for asking. Usually there's very little: the website logs are gone after 14 days, and the setup service keeps no contact details. Stripe's records of your payment are kept as the law requires; we'll pass on a deletion request to Stripe where we can. On the leaderboard, you can withdraw an entry from your dashboard, and ask us to remove your display name.

If you're a California resident: we don't sell or share personal information, we don't use sensitive personal information except to do what you asked (using your DigitalOcean token once to create your server), and we don't disclose personal information to third parties for their own direct marketing. Residents of other states with privacy laws have the same choices described here.

## 10. Children

Charles is only for adults. We don't knowingly collect information from anyone under 18, and the website isn't directed to children under 13. If you think a child has given us information, email us and we'll delete it.

## 11. Where your information is

We're based in the United States, and our servers and service providers process information in the United States. The setup service is offered only to people in the United States.

## 12. Changes to this policy

When we change this policy, we'll update the version and effective date at the top and, for a material change, give notice on the website and in the release notes before it takes effect.

## 13. Contact

- **Charles LLC**, a Colorado limited liability company
- Mailing address: 7575 E Peakview Ave, Centennial, CO 80111
- Email: [support@charles-bot.xyz](mailto:support@charles-bot.xyz)
- Website: https://charles-bot.xyz
