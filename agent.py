"""
agent.py — DEPRECATED shim.

The monolithic TradingAgent has been decomposed into the multi-agent
runtime under agents/ (see agents/__init__.py). This shim keeps
`python agent.py` working as a standalone trader (rollback safety):
without Redis it behaves like the old monolith on file fallbacks.
"""

from agents.trader import main

if __name__ == "__main__":
    main()
