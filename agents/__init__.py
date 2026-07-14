"""
agents
======
Multi-agent runtime for the trading system.

Each module is an independently runnable agent (``python -m agents.<name>``):

- ``orchestrator`` — primary agent: session clock, job scheduler, health supervisor
- ``trader``       — live trading hot loop (decomposed from the old agent.py)
- ``scanner``      — sector scanner + dashboard ticker fetcher
- ``vetting``      — profit-vetting: backtest screen + live-accuracy blocklist
- ``strategy``     — market-regime detection -> parameter directives
- ``trainer``      — XGBoost model training / EOD incremental retraining

Agents coordinate exclusively through the Redis bus (``agents.bus``); all
authoritative state also lives in Redis keys or the existing files/SQLite so
any agent can restart and resynchronise.
"""
