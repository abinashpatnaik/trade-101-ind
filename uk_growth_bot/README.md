# UK Growth Bot — long-term, tax-free investing

A standalone bot that invests a monthly contribution (default **£200**) into
London-listed growth funds and shares inside a **Trading 212 Stocks & Shares
ISA**. It runs unattended on the existing Hetzner server and emails a
**weekly fund report**.

It is an *investor*, not a trader: it buys every month and rarely sells. It
shares no code with the IN/US intraday agents in this repo.

> **Capital at risk.** Not financial or tax advice. It manages only your own
> money, which is personal use and not a regulated activity, so the bot must
> never be offered to anyone else. It starts in **paper mode** (Trading 212's
> practice account, fake money) and stays there until you switch it.

## Why Trading 212 + ISA

| | Trading 212 Stocks & Shares ISA (default) | IBKR GIA (optional) |
|---|---|---|
| Login for a bot | API key + secret, fully unattended | Session resets daily; each login needs a 2FA tap on your phone |
| Tax | **None.** Gains and dividends are tax-free; nothing to report | CGT and dividend tax; the built-in CGT engine steers sales |
| Commission | £0 | ~£3 per order |
| Fractional shares | Yes, so every pound is invested | No |

Trading 212's API is still labelled **beta** (v0) and may change. Orders can only be placed in the account's main currency (GBP); all assets here are London lines priced in GBP.

## How it invests

| Sleeve | Default | What's in it |
|---|---|---|
| Core (75%) | 3 ETFs, weighted 45% / 30% / 25% of the core by rank | Chosen by the bot from a vetted pool of growth ETFs (below). No stamp duty. |
| Satellite (25%) | up to 2 stocks, 12.5% each | Best-ranked UK growth names from a candidate list (AZN, LSEG, RELX, RR, 3i, Experian, Halma, Sage, …). 0.5% stamp duty on buys, even in an ISA. |

**Core fund pool.** The bot holds the 3 best-ranked funds, at most one per group,
so it never doubles up on the same market:

| Group | Fund |
|---|---|
| global | VWRP Vanguard FTSE All-World |
| us_large | VUAG Vanguard S&P 500 |
| tech | CNX1 iShares Nasdaq-100 · SMGB VanEck Semiconductor |
| emerging | EMIM iShares Core MSCI EM IMI |
| small_cap | WLDS iShares MSCI World Small Cap |
| quality | IWFQ iShares MSCI World Quality Factor |
| uk_mid | VMID Vanguard FTSE 250 |

A held fund is kept until it falls below rank #5 (`UK_CORE_EXIT_RANK`), and never
in its first 90 days (`UK_CORE_MIN_HOLD_DAYS`), so the core changes rarely. Each
run, funds that aren't priced in GBP or aren't offered by Trading 212 are
skipped (and logged); funds already held stay. `UK_CORE_FUNDS` sets how many are held.

**Every trading day at 10:30 UK time:**
1. **Research.** Each candidate gets a score in [-1, 1] that blends three signals:
   - **momentum** (60% weight): 3-, 6- and 12-month returns, adjusted for risk;
   - **ML** (25%): an XGBoost model's probability of beating the median over the next ~3 months;
   - **news sentiment** (15%): UK Yahoo / Google News headlines.
2. **Targets.** The core is the top 3 pool funds as above, and its weights are tilted by up to ±30% by score. The satellite picks need positive 6-month momentum, a price above the 200-day average, and no strongly negative news.
3. **Monthly contribution.** New money is spread across whichever holdings are *below* target, in fractional shares, so the portfolio rebalances itself without selling.
4. **Selling** only happens when:
   - a satellite stock's thesis breaks: it is down more than 35% from its 1-year high *and* below its 200-day average;
   - a satellite stock drops below rank #5, after being held at least 180 days;
   - a core fund drops below rank #5, after being held at least 90 days; the money moves to the fund replacing it;
   - a quarterly rebalance finds a holding more than 10 percentage points over target.
5. **Bear market** (global equities below their 200-day average): with `pause`, new money is held in cash for up to 3 months, then invested anyway. With `derisk`, the satellite sleeve also moves into gilts.

**ML is held to a standard.** It is retrained every Sunday with walk-forward validation and a gap between training and test data. It only gets a vote if its out-of-sample AUC is at least 0.55; otherwise its weight is zero. The weekly report shows the AUC and accuracy.

## Tax

**ISA (default):** no CGT, no dividend tax, nothing to report. The report tracks this tax year's subscriptions against the **£20,000 ISA allowance** (far above £2,400 a year) and warns when it is 90% used. Trading 212 itself enforces the limit on deposits.

**GIA (`UK_ACCOUNT_TYPE=gia`):** the full UK CGT engine switches on:
- HMRC share-matching: same-day, then 30-day ("bed and breakfast"), then the Section 104 pool;
- discretionary sales capped to stay inside the £3,000 CGT allowance;
- a February–5 April allowance harvest into *twin* funds (VWRP↔FWRG, VUAG↔CSP1, CNX1↔EQQQ);
- a 30-day re-buy guard;
- estimated tax at `UK_INCOME_TAX_BAND`, plus Self Assessment and loss carry-forward reminders.

Check `config.py → TAX_YEARS` each April; allowances and rates change at Budgets.

## Weekly report (Friday 17:00)

The email contains:
- fund value, total contributed, growth in £ and %, and the week's change excluding new money;
- a **benchmark**: what you'd have if every contribution had gone into VWRP;
- holdings with weight versus target and gain;
- the week's trades and decisions, with reasons;
- the top research candidates, with momentum, ML and news scores;
- ML model quality;
- the tax/ISA position and the next contribution date;
- a 10-year illustration at 5%, 7% and 9% a year.

A copy is saved to `data/uk/reports/`.

## Setup

`UK_TRADING_MODE` picks one of three modes:

| Mode | Orders go to | Money |
|---|---|---|
| `sim` | nowhere: the bot's own simulated ledger | pretend |
| `paper` (default) | Trading 212's **practice account**, through the real API | fake (practice) |
| `live` | Trading 212 for real; also needs `T212_ENV=live` | real |

### Stage 1 — paper: Trading 212 practice account (real API, fake money)

Orders appear in the Trading 212 app's **Practice** account. Paper mode is
always locked to Trading 212's demo environment, whatever `T212_ENV` says. The
practice account comes pre-loaded with virtual cash; the bot ignores that pile,
credits itself a pretend £200 on `UK_CONTRIBUTION_DAY` each month, and spends
only that.

1. In the Trading 212 app, switch to the **Practice** account and create an API key (Settings → API). Grant only:
   - account/portfolio **read**;
   - history **read** (orders, transactions, dividends);
   - **orders: execute**.

   If the app offers IP restrictions, limit the key to the server's IP.
2. Put the settings in place, using **either** of these:
   - **GitHub (recommended):** under repo Settings → Secrets and variables → Actions:
     - add *Secrets* `T212_API_KEY` and `T212_API_SECRET`;
     - add *Variables* `COMPOSE_PROFILES=uk` and `UK_TRADING_MODE=paper` (optionally `UK_REPORT_RECIPIENT`).

     Every deploy writes them into the server's `.env`: values go over SSH stdin, the file is made owner-only, and settings left unset in GitHub don't touch the server. The deploy then runs the read-only `check` and prints it in the Actions log. Re-run it any time from the Actions tab ("Run workflow"); this redeploys the whole stack.
   - **By hand on the host:** put the same lines in `~/trading-agent/.env`.
3. The deploy's read-only `check` should show every ticker `OK`. Fix any `MISS` with `UK_T212_TICKERS=VWRP.L=VWRPl_EQ,...`.
4. The bot invests at 10:30 UK on trading days. Watch the orders appear in the app's Practice account.

`sim` does the same without any broker. It's useful only when you have no API key.

### Stage 3 — real money

1. Open the **Stocks & Shares ISA** in Trading 212 and create a separate API key for it, with the same permissions.
2. Set up a **£200/month standing order** from your bank into the ISA, landing on or just before `UK_CONTRIBUTION_DAY`. The bot reads deposits from Trading 212's transaction history and invests them; it never moves money in or out. Cash already in the ISA on its first run is treated as an opening balance and invested too.
3. Set `UK_TRADING_MODE=live`, `T212_ENV=live` and the new key/secret, then redeploy.

**Safety rails:**
- Real money needs *both* `UK_TRADING_MODE=live` and `T212_ENV=live`.
- Purchases are capped at the lower of the ledger's cash and Trading 212's available cash.
- Trading 212 says market orders are not idempotent, so an order whose outcome is unknown (timeout, dropped connection) is **never resent**. The bot looks for it in Trading 212's records instead.
- Manual trades are flagged in the report but not managed.
- **Kill switch:** `UK_NO_NEW_BUYS=true` stops all buying.

Before going live, also check every ticker in `universe.py`: that it still exists, which share class it is, and that ETFs have UK *reporting fund* status (it matters if you ever use a GIA).

### Optional: IBKR GIA instead

Set `UK_BROKER=ibkr`, `UK_ACCOUNT_TYPE=gia`, `COMPOSE_PROFILES=uk,uk-ibkr` and `IBEAM_ACCOUNT`/`IBEAM_PASSWORD`. IBKR sessions reset daily and every login needs a 2FA approval on IBKR Mobile, so this route is semi-automatic at best.

## Running costs

There's nothing new to pay for: it runs on the existing server, and the market data (yfinance), news (RSS) and Trading 212 API are free. There's no commission; the only trading cost is 0.5% stamp duty on purchases of UK shares (the satellite sleeve), plus market spreads.

## Local use

```bash
pip install -r uk_growth_bot/requirements.txt pytest
python -m pytest uk_growth_bot/tests          # 75 tests, no network
UK_TRADING_MODE=sim UK_DATA_DIR=/tmp/ukbot python -m uk_growth_bot.main run-once   # one simulated cycle (needs internet)
python -m uk_growth_bot.main check | plan | status | report | train | loop
```

## Diagnostics on the server

GitHub → Actions → **UK bot ops (read-only)** → Run workflow, then pick one:
- `status`: the container's recent log, holdings, cash and the last week's decisions;
- `plan`: a dry run of today's cycle (research, targets and the orders it *would* place) against a throwaway copy of the ledger;
- `check`: the Trading 212 connection test.

None of them places orders or restarts anything.

## Files

`config.py` settings · `universe.py` core fund pool, stocks & twins · `tax.py` CGT engine (GIA) ·
`market_data.py` GBP-normalised prices · `features.py` · `sentiment.py` ·
`ml_model.py` · `research.py` · `planner.py` decision logic (pure, tested) ·
`broker.py` simulation + Trading 212 + IBKR · `ledger.py` SQLite ledger · `report.py` · `main.py` scheduler.
