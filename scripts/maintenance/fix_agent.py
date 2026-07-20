import os

with open('agent.py', 'r') as f:
    content = f.read()

# Fix agent.py nav logger args
old1 = """                    "Portfolio: nav=%s%.2f cash=%s%.2f positions=%d daily_pnl=%s%.2f (%.3f%%)",
                    summary["portfolio_value"],
                    summary["cash"],
                    summary["open_positions_count"],
                    summary["daily_pnl"],
                    summary["daily_loss_pct"],"""

new1 = """                    "Portfolio: nav=%s%.2f cash=%s%.2f positions=%d daily_pnl=%s%.2f (%.3f%%)",
                    CUR_SYM, summary["portfolio_value"],
                    CUR_SYM, summary["cash"],
                    summary["open_positions_count"],
                    CUR_SYM, summary["daily_pnl"],
                    summary["daily_loss_pct"],"""

content = content.replace(old1, new1)

old2 = """                "Session performance: trades=%d win_rate=%.1f%% "
                "total_pnl=%s%.2f best=%s%.2f worst=%s%.2f",
                perf["num_trades"],
                perf["win_rate"],
                perf["total_pnl"],
                perf["best_trade"],
                perf["worst_trade"],"""

new2 = """                "Session performance: trades=%d win_rate=%.1f%% "
                "total_pnl=%s%.2f best=%s%.2f worst=%s%.2f",
                perf["num_trades"],
                perf["win_rate"],
                CUR_SYM, perf["total_pnl"],
                CUR_SYM, perf["best_trade"],
                CUR_SYM, perf["worst_trade"],"""

content = content.replace(old2, new2)

with open('agent.py', 'w') as f:
    f.write(content)

print("Agent fixed")
