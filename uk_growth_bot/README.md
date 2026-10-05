# UK Growth Bot — long-term, tax-aware investing

A standalone bot that invests a monthly contribution (default **£200**) into
London-listed growth funds and shares through an **Interactive Brokers General
Investment Account**, runs on the existing Hetzner server, and emails a
**weekly fund report**.

It is an *investor*, not a trader: it buys every month, rarely sells, and
treats each sale as a tax event to be planned. It shares no code with the
IN/US intraday agents in this repo.

> **Capital at risk.** Not financial or tax advice. It manages only your own
> money, which is personal use and not a regulated activity, so the bot must
> never be offered to anyone else. It starts in **paper mode** and stays there
> until you switch it.

## How it invests

| Sleeve | Default | What's in it |
|---|---|---|
| Core (75%) | VWRP 50% · VUAG 25% · CNX1 25% of the core | Accumulating global / S&P 500 / Nasdaq-100 ETFs. No stamp duty. |
| Satellite (25%) | up to 2 stocks, 12.5% each | Best-ranked UK growth names from a candidate list (AZN, LSEG, RELX, RR, 3i, Experian, Halma, Sage, …) |

**Every trading day at 10:30 UK time:**
1. **Research.** Each candidate gets a score in [-1, 1] that blends three signals:
   - **momentum** (60% weight): 3-, 6- and 12-month returns, adjusted for risk;
   - **ML** (25%): an XGBoost model's probability of beating the median over the next ~3 months;
   - **news sentiment** (15%): UK Yahoo / Google News headlines.
2. **Targets.** Signals tilt the core weights by up to ±30% of each ETF's base weight. The satellite picks need positive 6-month momentum, a price above the 200-day average, and no strongly negative news.
3. **Monthly contribution.** The cash goes into the *most underweight* holding, in orders of at least £100, so the £3 minimum commission stays small. Rebalancing through new money means it almost never has to sell.
4. **Selling** only happens when:
   - a satellite stock's thesis breaks: it is down more than 35% from its 1-year high *and* below its 200-day average;
   - a satellite stock drops below rank #5, after being held at least 180 days;
   - a quarterly rebalance finds a holding more than 10 percentage points over target;
   - the yearly CGT-allowance harvest runs (below).
5. **Bear market** (global equities below their 200-day average): with `pause`, new money is held in cash for up to 3 months, then invested anyway. With `derisk`, the satellite sleeve also moves into gilts.

**ML is held to a standard.** It is retrained every Sunday with walk-forward validation and a gap between training and test data. It only gets a vote if its out-of-sample AUC is at least 0.55; otherwise its weight is zero. The weekly report shows the AUC and accuracy.

## UK tax handling (GIA)

- Full HMRC share-matching: same-day, then 30-day ("bed and breakfast"), then the Section 104 pool. Commission and stamp duty count as allowable costs. See `tax.py` and the tests.
- **CGT allowance (£3,000):** discretionary sales are capped so realised gains stay inside the allowance. A rotation that would breach it is deferred, and a rebalance is trimmed to fit.
- **Allowance harvest (1 Feb – 5 Apr):** it sells enough of a core ETF to realise up to the unused allowance tax-free, then buys its *twin* (VWRP↔FWRG, VUAG↔CSP1, CNX1↔EQQQ). The twin keeps the same market exposure on a higher cost basis. Because the twin isn't the same security, the 30-day rule doesn't cancel the gain.
- **30-day guard:** it never re-buys a security sold in the last 30 days.
- **Stamp duty:** the 0.5% on UK shares is built into the costs, which is one reason the core is ETFs.
- The weekly report shows:
  - realised gains and losses against the allowance;
  - dividends against the £500 allowance;
  - estimated tax at your band (`UK_INCOME_TAX_BAND`);
  - whether you must file Self Assessment (gains over the allowance, or disposal proceeds over £50k);
  - a reminder to claim net losses so they can be carried forward.
- **Check each April:** the allowances and rates in `config.py → TAX_YEARS` change at Budgets.
- **Accumulating ETFs** still create taxable "excess reportable income" even though no cash is paid out. The report reminds you; take the figures from each fund's annual report.
- **ISA note:** a Stocks & Shares ISA would shelter all of this, but no UK ISA offers an API, so autonomy needs a GIA. At £2,400 a year of contributions plus the yearly harvest, CGT is unlikely to bite for several years. Once the pot is large, consider a "Bed & ISA" each April (done by hand).

## Weekly report (Friday 17:00)

The email contains:
- fund value and total contributed;
- growth in £ and %, and the week's change excluding new money;
- a **benchmark**: what you'd have if every contribution had gone into VWRP;
- holdings with weight versus target and gain;
- the week's trades and decisions, with reasons;
- the top research candidates, with their momentum, ML and news scores;
- ML model quality;
- the tax position;
- the next contribution date;
- a 10-year illustration at 5%, 7% and 9% a year.

A copy is saved to `data/uk/reports/`.

## Setup

1. **Open an IBKR UK account** (General Investment Account, cash, GBP base currency). Set up a **£200/month standing order** into it on `UK_CONTRIBUTION_DAY`. In live mode, cash that arrives is detected as a contribution automatically.
2. Copy the new `UK_*` / `IBEAM_*` keys from `.env.example` into the host's `.env`. Add `COMPOSE_PROFILES=uk`. Keep `UK_TRADING_MODE=paper`.
3. Deploy as usual (merge to `main`) or run `docker compose up -d --build` on the host. This starts `uk-ibeam` (it keeps the IBKR Client Portal Gateway logged in) and `uk-growth-bot`.
4. **2FA:** IBKR requires it for live accounts. Approve the IBeam login on IBKR Mobile; see the [IBeam docs](https://github.com/Voyz/ibeam) for automating it. You can also log IBeam into your **IBKR paper account** to test the real broker path.
5. Watch a few weekly reports in paper mode. Then set `UK_TRADING_MODE=live` and restart `uk-growth-bot`.

Before going live, also check:
- **every ticker** in `universe.py`: that it still exists, which share class it is, and that it has UK *reporting fund* status;
- your IBKR commission plan, against `UK_COMMISSION_MIN` / `UK_COMMISSION_PCT`.

**Kill switch:** `UK_NO_NEW_BUYS=true` stops all buying; selling stays tax-gated as normal.

## Running costs

There's nothing new to pay for: it runs on the existing server, the market data (yfinance) and news (RSS) are free, and IBKR API access is free. Trading costs are about one £3 order a month (≈£36 a year, ~1.5% of contributions while the pot is small; this shrinks as a share as the fund grows). Stamp duty applies only to the stock sleeve.

## Local use

```bash
pip install -r uk_growth_bot/requirements.txt pytest
python -m pytest uk_growth_bot/tests          # 25 tests, no network
UK_DATA_DIR=/tmp/ukbot python -m uk_growth_bot.main run-once   # one paper cycle (needs internet)
python -m uk_growth_bot.main report | train | loop
```

## Files

`config.py` settings · `universe.py` assets & twins · `tax.py` CGT engine ·
`market_data.py` GBP-normalised prices · `features.py` · `sentiment.py` ·
`ml_model.py` · `research.py` · `planner.py` decision logic (pure, tested) ·
`broker.py` paper + IBKR · `ledger.py` SQLite ledger · `report.py` · `main.py` scheduler.
