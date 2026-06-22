import os

def fix_file(fpath):
    with open(fpath, "r") as f:
        content = f.read()
    
    # 1. Replace remaining "₹%.2f" etc with f-string or %s
    # Actually, let's just make it very safe. We replace all "₹%.2f" with "%s%.2f" and carefully add CUR_SYM to args.
    
    # learning_engine.py
    if fpath == "learning_engine.py":
        old = '"Learning update: %s %s ₹%.2f — updated %d keyword weights",\n            symbol, outcome, pnl, n_updated,'
        new = '"Learning update: %s %s %s%.2f — updated %d keyword weights",\n            symbol, outcome, CUR_SYM, pnl, n_updated,'
        content = content.replace(old, new)
        
    elif fpath == "portfolio_tracker.py":
        old = 'daily spend cap: ₹%.2f reinvest: %s",\n            self.daily_spend_cap, self.reinvest_profits'
        new = 'daily spend cap: %s%.2f reinvest: %s",\n            CUR_SYM, self.daily_spend_cap, self.reinvest_profits'
        content = content.replace(old, new)
        
        old = '"Session start NAV recorded: ₹%.2f", self._session_start_nav'
        new = '"Session start NAV recorded: %s%.2f", CUR_SYM, self._session_start_nav'
        content = content.replace(old, new)
        
        old = '"Portfolio updated: nav=₹%.2f cash=₹%.2f "\n                "open_positions=%d daily_pnl=₹%.2f",\n                self.portfolio_value, self.cash, len(self.open_positions), self.daily_pnl'
        new = '"Portfolio updated: nav=%s%.2f cash=%s%.2f "\n                "open_positions=%d daily_pnl=%s%.2f",\n                CUR_SYM, self.portfolio_value, CUR_SYM, self.cash, len(self.open_positions), CUR_SYM, self.daily_pnl'
        content = content.replace(old, new)
        
        old = '"Max daily loss %.1f%% exceeded "\n                "(start=₹%.2f, current=₹%.2f).",\n                loss_pct * 100, self._session_start_nav, self.portfolio_value'
        new = '"Max daily loss %.1f%% exceeded "\n                "(start=%s%.2f, current=%s%.2f).",\n                loss_pct * 100, CUR_SYM, self._session_start_nav, CUR_SYM, self.portfolio_value'
        content = content.replace(old, new)
        
        old = '"Wallet | daily_spent=₹%.2f / cap=₹%.2f (%.1f%% used)",\n                self.daily_spent, self.daily_spend_cap, (self.daily_spent / self.daily_spend_cap) * 100'
        new = '"Wallet | daily_spent=%s%.2f / cap=%s%.2f (%.1f%% used)",\n                CUR_SYM, self.daily_spent, CUR_SYM, self.daily_spend_cap, (self.daily_spent / self.daily_spend_cap) * 100'
        content = content.replace(old, new)
        
        old = '"Wallet | profit ₹%.2f from %s added to reinvestment pool — "\n                        "total reinvestable today: ₹%.2f",\n                        profit, symbol, self.daily_realised_profit'
        new = '"Wallet | profit %s%.2f from %s added to reinvestment pool — "\n                        "total reinvestable today: %s%.2f",\n                        CUR_SYM, profit, symbol, CUR_SYM, self.daily_realised_profit'
        content = content.replace(old, new)
        
        old = '"Trade recorded: %s %s %d @ ₹%.4f notional=₹%.2f pnl=%s reason=%s",\n            action, symbol, quantity, price,\n            trade.notional,\n            f"{CUR_SYM}{pnl:.2f}" if pnl is not None else "N/A",\n            exit_reason or "N/A",'
        new = '"Trade recorded: %s %s %d @ %s%.4f notional=%s%.2f pnl=%s reason=%s",\n            action, symbol, quantity, CUR_SYM, price,\n            CUR_SYM, trade.notional,\n            f"{CUR_SYM}{pnl:.2f}" if pnl is not None else "N/A",\n            exit_reason or "N/A",'
        content = content.replace(old, new)
        
    with open(fpath, "w") as f:
        f.write(content)

fix_file("learning_engine.py")
fix_file("portfolio_tracker.py")
print("Others fixed")
