# India Growth Bot — long-term investing on Zerodha

A standalone bot that invests a monthly contribution (default **₹25,000**) in
NSE-listed shares through your Zerodha account. It runs on the same Hetzner
server and emails a **weekly fund report**. It's the India counterpart of
`uk_growth_bot`: it buys every month and rarely sells.

> **Capital at risk.** Not financial or tax advice. It starts in **simulation**
> (real prices, pretend money, no orders), because Zerodha has no paper
> account. Real orders need `IN_TRADING_MODE=live`.

## Why shares, not ETFs

You're a UK resident, so the UK taxes these gains as well as India:

| | Direct shares (default) | Indian ETFs / mutual funds |
|---|---|---|
| UK tax on gains | **Capital gains tax** (18% / 24%, £3,000 allowance) | Usually **income tax** (up to 45%): Indian funds aren't HMRC "reporting funds" |
| Indian tax | 20% if held ≤12 months; 12.5% above ₹1.25 lakh a year if longer | same |

Indian tax paid is credited against the UK tax on the same gain (India–UK
DTAA). ETFs are available with `IN_INCLUDE_ETFS=true`, but take advice
first.

## How it invests

- **The pool.** 31 large NSE companies across 11 sectors: banks, finance, IT, energy, telecom, consumer, industrials, autos, pharma and materials (`universe.py`).
- **Research, every trading day at 10:15 IST.** Each share gets a score blending:
  - momentum (60%);
  - a validated XGBoost model (25%). It's only used if its out-of-sample AUC is at least 0.55;
  - news sentiment from Indian Yahoo and Google News feeds (15%).
- **The portfolio.** It holds the best-ranked **8 shares**, at most **2 per sector**. Weights start equal and are tilted ±30% by score. A share is only added if its 6-month momentum is positive, it's above its 200-day average, and the news isn't strongly negative.
- **New money.** The month's ₹25k goes to the most underweight holdings, in whole shares and orders of at least ₹10,000.
- **Selling** happens only when:
  - **crash rule:** the share is more than 35% below its 1-year high *and* below its 200-day average. It's sold even at a loss;
  - **rotation:** it falls below rank #15 *after* being held **365 days**, so the gain is long-term;
  - **quarterly rebalance:** a holding is more than 10 points over target;
  - **tax harvest, February–March:** it sells up to ₹1.25 lakh of long-term gains (tax-free) and buys the same shares back the next trading day. That raises their cost base for later. India has no wash-sale rule.
- **Profit check.** Apart from the crash rule, nothing is sold below its average cost plus 1%. A holding under water is kept, gets no new money, and is sold once it recovers.
- **Tax check.** A sale whose Indian tax would exceed 2% of the proceeds (e.g. mostly short-term shares) is deferred.
- **Bear market.** If the Nifty 50 is below its 200-day average, new money waits in cash for up to 3 months.

## Costs (Zerodha NRO non-PIS)

| Charge | Rate |
|---|---|
| Brokerage | 0.5% of the order, capped at ₹50 (so orders of ₹10k or more) |
| STT | 0.1% on buys and sells |
| Stamp duty | 0.015% on buys |
| Exchange, SEBI and GST | small |
| DP charge | about ₹15 on each day a share is sold |

A ₹25k month costs about ₹90 (≈0.35%). The Kite Connect "Personal" API (orders and portfolio) is free; prices come from Yahoo.

## Sharing the account with the intraday agent

Zerodha allows one account per PAN, and `agent-in` already trades it. The
two bots are kept apart:

- **Login:** this bot reuses `agent-in`'s cached Kite token (`data/kite_access_token.txt`). If the token has expired, it logs in itself with the same `KITE_*` credentials and shares the new token. **Note:** Zerodha and the exchanges ask for a manual daily login and advise against automating it; this reuses the automated login you already run.
- **Ring-fence:** after every run in live mode, the bot writes its uninvested cash and its share quantities to `data/in_growth_reserved.json`. `zerodha_connector.py` (used by `agent-in`) subtracts both, so the intraday agent never spends that cash or adopts or sells those shares.
- **Its own records:** this bot only ever sells shares its own ledger bought, and never more than Zerodha shows. Orders are CNC delivery LIMIT orders tagged `ingrowth`, a little through the last price.
- **Never resent:** if an order's outcome is unknown, the bot looks it up in the order book instead of sending it again.

## Setup

1. **Simulation (default).** Nothing to configure. After a deploy it runs on real prices with a pretend ₹25k each month. Check it with GitHub → Actions → **Growth bots ops** → bot `in` → `plan` or `status`.
2. **Before going live:**
   - run the ops `check` with bot `in`. It logs in, shows cash and holdings, and checks each share is quotable, without placing orders;
   - make sure DDPI is active (Zerodha needs it to sell delivery shares through the API);
   - make sure the server's static IP is whitelisted on your Kite Connect app.
3. **Live.** Set the GitHub Actions variable `IN_TRADING_MODE=live` and redeploy, then transfer ₹25,000 into Zerodha each month by `IN_CONTRIBUTION_DAY`. Zerodha has no deposit-history API, so the bot books the contribution itself on that day, but it never spends more than Zerodha shows as available. Kill switch: `IN_NO_NEW_BUYS=true`.

## Weekly report (Friday 18:00 IST)

The email contains:
- fund value in ₹ (≈ £), contributions, growth, and the week's change;
- a benchmark: what you'd have if every contribution had gone into NIFTYBEES;
- holdings against targets;
- the week's trades and decisions;
- the top-ranked shares;
- ML model quality;
- **Indian tax**: STCG and LTCG, the exemption left, and estimated tax. As an NRI, tax is deducted at source when you sell;
- **UK tax**: the same gains in GBP at each trade's exchange rate, using HMRC matching rules, against the £3,000 allowance. It also shows estimated dividends, which Zerodha pays to your bank, not the trading account, and a Self Assessment note;
- a 10-year illustration.

## Local use

```bash
pip install -r in_growth_bot/requirements.txt pytest
python -m pytest in_growth_bot/tests
IN_DATA_DIR=/tmp/inbot python -m in_growth_bot.main plan
python -m in_growth_bot.main check | plan | status | report | train | run-once | loop
```

## Files

| File | What it does |
|---|---|
| `config.py` | settings, Indian and UK tax rates |
| `universe.py` | the share pool and sectors |
| `tax.py` | Indian FIFO STCG/LTCG |
| `uk_tax.py` | HMRC matching, in GBP |
| `planner.py` | decisions and charges |
| `broker.py` | simulation and Kite |
| `ledger.py` | SQLite ledger |
| `report.py` | the weekly email |
| `main.py` | the scheduler |
