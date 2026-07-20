# Architecture

A multi-agent, multi-market automated trading system. Two independent markets —
**India (NSE via Zerodha Kite)** and **US (NASDAQ via Alpaca)** — run the same
codebase, selected at process start by the `TRADING_MARKET` environment variable
(`IN` or `US`). Each market runs its own full set of agents against a shared
Redis bus.

## Agent stack

Each agent is an independently runnable module (`python -m agents.<name>`), one
process per container. They coordinate **only** through the Redis bus
(`agents.bus`); all authoritative state also lives in Redis keys or SQLite, so
any agent can restart and resynchronise.

| Agent | Role |
|-------|------|
| `orchestrator` | Primary supervisor: session clock, job scheduler, health/restart supervision of the other agents. |
| `trader` | The live hot loop — scans vetted targets, makes decisions, places/manages orders, handles EOD flat-close. |
| `scanner` | Sector scanner → nominates the day's tradeable universe (`data/daily_targets_{MARKET}.json`); also hosts the dashboard ticker feed. |
| `vetting` | Profit-vetting: a backtest screen (`agents/backtest_sim.py`, replays recent bars through the live entry + exit logic, net of friction) plus a live-accuracy blocklist. |
| `strategy` | Market-regime detection → publishes parameter directives (e.g. threshold deltas) the trader honors. |
| `trainer` | XGBoost model training and EOD incremental retraining; writes the ML models and per-symbol thresholds. |

## Decision flow (one symbol, one tick)

```
price_feed ──OHLCV──► trend_engine ──TrendSignal──►┐
sentiment_engine ──score──►                        │
ai_validator (XGBoost day/swing) ──ml_confidence──►│
                                                   ▼
                                         decision_engine.make_decision
                                          (ML-driven when AI_PRIMARY_DRIVER)
                                                   │  BUY / SELL / HOLD
                                                   ▼
                                            order_executor ──► broker connector
                                                   │            (zerodha / alpaca)
                                                   ▼
                                         portfolio_tracker ──► trades DB + state
```

Key gates inside `decision_engine`, in order: ML buy threshold (a **floor** over
the trainer's dynamic per-symbol / `_GLOBAL_` threshold), overbought/blow-off
guard (RSI, IN only), position-sizing, and a cost gate (expected move must clear
`min_edge_multiple` × round-trip friction). The trader adds a no-new-entry
window in the final `no_entry_buffer_minutes` before close.

## Core modules (repo root — flat by design)

| Module | Purpose |
|--------|---------|
| `config.py` | Central config; market profiles (`get_india_config` / `get_us_config`) selected by `TRADING_MARKET`. |
| `trend_engine.py` | Technical indicators → `TrendSignal` (RSI, EMA, MACD, ATR, VWAP, ADX, BB, volume ratio). |
| `sentiment_engine.py` | RSS/news sentiment scoring. |
| `ai_validator.py` | Loads the XGBoost day/swing models; produces ML confidence. |
| `decision_engine.py` | Turns signals + ML + sentiment into BUY/SELL/HOLD with all risk gates. |
| `order_executor.py` | Places/tracks orders, software trailing stops, order-intent logging. |
| `portfolio_tracker.py` | Positions, capital, trailing stops, trade recording. |
| `price_feed.py` | OHLCV (yfinance for IN, Alpaca for US). |
| `trading_costs.py` | Market-aware round-trip cost model (fees + slippage). |
| `market_session.py` | Session hours / near-close logic per market. |
| `market_screener.py`, `sector_scanner.py`, `ticker_fetcher.py` | Dynamic universe selection + nominations. |
| `ml_trainer.py`, `learning_engine.py`, `continuous_learning.py` | Model training + online learning. |
| `db.py` | SQLite persistence (`trading_{MARKET}.db`: trades, nav_history, signals, ml_validations). |
| `zerodha_connector.py`, `alpaca_connector.py`, `ibkr_connector.py` | Broker clients. |
| `report_generator.py`, `report_sender.py` | EOD reports + email. |

> Core modules are imported flatly (`from config import config`) and the
> Dockerfile copies them with `COPY *.py .`. Keep them at the repo root — moving
> them into a package would require changing every import, the Dockerfile,
> `docker-compose.yml`, and the deploy pipeline together.

## Data & state

- **Redis** (`t101-redis`): the live bus + ephemeral state (heartbeats, vetted
  targets, blocklist, regime directives).
- **SQLite** (`data/trading_{MARKET}.db`): durable trades, NAV history, signals,
  ML-validation log.
- **Files** (`data/`): `daily_targets_{MARKET}.json`, `ml_validator_model_*`,
  `ml_thresholds_*`, `order_intents_{MARKET}.csv` (signal price per order, for
  slippage analysis), `vetting_report_{MARKET}.json`.

Directories: `agents/` (the runtime), `dashboard/` (web UI), `mobile/` (app),
`tests/` (pytest suite), `scripts/` (one-off/dev utilities — see
`scripts/README.md`), `docs/` (this).
