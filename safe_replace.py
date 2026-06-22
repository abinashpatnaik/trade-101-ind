import os

def apply_replaces(fpath, replacements):
    with open(fpath, "r") as f:
        content = f.read()
    
    # Import CUR_SYM if not present
    if "from config import config" in content and "CUR_SYM" not in content:
        content = content.replace("from config import config", "from config import config, CUR_SYM")

    for old, new in replacements:
        content = content.replace(old, new)
        
    with open(fpath, "w") as f:
        f.write(content)

agent_replacements = [
    ('nav=₹%.2f cash=₹%.2f positions=%d daily_pnl=₹%.2f', 'nav=%s%.2f cash=%s%.2f positions=%d daily_pnl=%s%.2f'),
    ('total_pnl=₹%.2f best=₹%.2f worst=₹%.2f', 'total_pnl=%s%.2f best=%s%.2f worst=%s%.2f'),
    # Note: agent.py logger calls need args appended.
    ('logger.info(\n                    "Portfolio: nav=₹%.2f cash=₹%.2f positions=%d daily_pnl=₹%.2f (%.3f%%)",\n                    portfolio_state.get("portfolio_value", 0),\n                    portfolio_state.get("available_funds", 0),\n                    len(portfolio_state.get("open_positions", {})),\n                    daily_pnl,\n                    daily_loss_pct * 100\n                )', 
     'logger.info(\n                    "Portfolio: nav=%s%.2f cash=%s%.2f positions=%d daily_pnl=%s%.2f (%.3f%%)",\n                    CUR_SYM, portfolio_state.get("portfolio_value", 0),\n                    CUR_SYM, portfolio_state.get("available_funds", 0),\n                    len(portfolio_state.get("open_positions", {})),\n                    CUR_SYM, daily_pnl,\n                    daily_loss_pct * 100\n                )'),
    ('logger.info(\n                "Session stats: trades=%d win_rate=%.1f%% "\n                "total_pnl=₹%.2f best=₹%.2f worst=₹%.2f",\n                num_trades, win_rate, total_pnl, best_trade, worst_trade\n            )',
     'logger.info(\n                "Session stats: trades=%d win_rate=%.1f%% "\n                "total_pnl=%s%.2f best=%s%.2f worst=%s%.2f",\n                num_trades, win_rate, CUR_SYM, total_pnl, CUR_SYM, best_trade, CUR_SYM, worst_trade\n            )')
]

learning_engine_replacements = [
    ('"Learning update: %s %s ₹%.2f — updated %d keyword weights",\n            action, symbol, pnl, count',
     '"Learning update: %s %s %s%.2f — updated %d keyword weights",\n            action, symbol, CUR_SYM, pnl, count')
]

portfolio_tracker_replacements = [
    ('daily spend cap: ₹%.2f reinvest: %s",\n            self.daily_spend_cap, self.reinvest_profits',
     'daily spend cap: %s%.2f reinvest: %s",\n            CUR_SYM, self.daily_spend_cap, self.reinvest_profits'),
    ('"Session start NAV recorded: ₹%.2f", self._session_start_nav',
     '"Session start NAV recorded: %s%.2f", CUR_SYM, self._session_start_nav'),
    ('nav=₹%.2f cash=₹%.2f "\n                "open_positions=%d daily_pnl=₹%.2f",\n                self.portfolio_value, self.cash, len(self.open_positions), self.daily_pnl',
     'nav=%s%.2f cash=%s%.2f "\n                "open_positions=%d daily_pnl=%s%.2f",\n                CUR_SYM, self.portfolio_value, CUR_SYM, self.cash, len(self.open_positions), CUR_SYM, self.daily_pnl'),
    ('"(start=₹%.2f, current=₹%.2f).",\n                loss_pct * 100, self._session_start_nav, self.portfolio_value',
     '"(start=%s%.2f, current=%s%.2f).",\n                loss_pct * 100, CUR_SYM, self._session_start_nav, CUR_SYM, self.portfolio_value'),
    ('"Wallet | daily_spent=₹%.2f / cap=₹%.2f (%.1f%% used)",\n                self.daily_spent, self.daily_spend_cap, (self.daily_spent / self.daily_spend_cap) * 100',
     '"Wallet | daily_spent=%s%.2f / cap=%s%.2f (%.1f%% used)",\n                CUR_SYM, self.daily_spent, CUR_SYM, self.daily_spend_cap, (self.daily_spent / self.daily_spend_cap) * 100'),
    ('"Wallet | profit ₹%.2f from %s added to reinvestment pool — "\n                        "total reinvestable today: ₹%.2f",\n                        profit, symbol, self.daily_realised_profit',
     '"Wallet | profit %s%.2f from %s added to reinvestment pool — "\n                        "total reinvestable today: %s%.2f",\n                        CUR_SYM, profit, symbol, CUR_SYM, self.daily_realised_profit'),
    ('"Trade recorded: %s %s %d @ ₹%.4f notional=₹%.2f pnl=%s reason=%s",\n            action, symbol, quantity, price, notional_value, pnl_str, exit_reason',
     '"Trade recorded: %s %s %d @ %s%.4f notional=%s%.2f pnl=%s reason=%s",\n            action, symbol, quantity, CUR_SYM, price, CUR_SYM, notional_value, pnl_str, exit_reason'),
    ('f"₹{pnl:.2f}"', 'f"{CUR_SYM}{pnl:.2f}"'),
    ('f"nav=₹{self.portfolio_value:.2f} "\n            f"cash=₹{self.cash:.2f} "\n            f"positions={len(self.open_positions)} "\n            f"daily_pnl=₹{self.daily_pnl:.2f}>"',
     'f"nav={CUR_SYM}{self.portfolio_value:.2f} "\n            f"cash={CUR_SYM}{self.cash:.2f} "\n            f"positions={len(self.open_positions)} "\n            f"daily_pnl={CUR_SYM}{self.daily_pnl:.2f}>"')
]

report_replacements = [
    ('Rupees (`₹`)', 'configured currency'),
    ('f"₹{pnl_val:+.2f}"', 'f"{CUR_SYM}{pnl_val:+.2f}"'),
    ("f\"<td style='{_TD};text-align:right;'>₹{self._safe_float(t.get('price',0)):.2f}</td>\"", "f\"<td style='{_TD};text-align:right;'>{CUR_SYM}{self._safe_float(t.get('price',0)):.2f}</td>\""),
    ("f\"<td style='{_TD};text-align:right;'>₹{avg_cost:.2f}</td>\"", "f\"<td style='{_TD};text-align:right;'>{CUR_SYM}{avg_cost:.2f}</td>\""),
    ("f\"<td style='{_TD};text-align:right;'>₹{market_value:.2f}</td>\"", "f\"<td style='{_TD};text-align:right;'>{CUR_SYM}{market_value:.2f}</td>\""),
    ("f\"<td style='{_TD};text-align:right;font-weight:600;color:{u_colour};'>₹{unrealised:+.2f}</td>\"", "f\"<td style='{_TD};text-align:right;font-weight:600;color:{u_colour};'>{CUR_SYM}{unrealised:+.2f}</td>\""),
    (">₹{total_pnl:+.2f}</p>", ">{CUR_SYM}{total_pnl:+.2f}</p>"),
    (">₹{portfolio_value:,.2f}</p>", ">{CUR_SYM}{portfolio_value:,.2f}</p>"),
    ('f"  Session P&L      : ₹{total_pnl:+,.2f}",', 'f"  Session P&L      : {CUR_SYM}{total_pnl:+,.2f}",'),
    ('f"  Portfolio Value  : ₹{portfolio_value:,.2f}",', 'f"  Portfolio Value  : {CUR_SYM}{portfolio_value:,.2f}",'),
    ('f"₹{price_val:>9.2f} "', 'f"{CUR_SYM}{price_val:>9.2f} "'),
    ('f"{sym:<8} {str(qty):>6} ₹{avg_cost:>9.2f} "\n                    f"₹{market_value:>11.2f} ₹{unrealised:>+15.2f}"',
     'f"{sym:<8} {str(qty):>6} {CUR_SYM}{avg_cost:>9.2f} "\n                    f"{CUR_SYM}{market_value:>11.2f} {CUR_SYM}{unrealised:>+15.2f}"'),
    ('pnl=₹%.2f win_rate=%.1f%%",\n            session_date,\n            num_trades,\n            total_pnl,\n            win_rate',
     'pnl=%s%.2f win_rate=%.1f%%",\n            session_date,\n            num_trades,\n            CUR_SYM, total_pnl,\n            win_rate')
]

apply_replaces("agent.py", agent_replacements)
apply_replaces("learning_engine.py", learning_engine_replacements)
apply_replaces("portfolio_tracker.py", portfolio_tracker_replacements)
apply_replaces("report_generator.py", report_replacements)

# Remove the .env.example one manually
with open(".env.example", "r") as f:
    c = f.read().replace('₹', 'currency')
with open(".env.example", "w") as f:
    f.write(c)

print("Done exact replace")
