import os

def fix_portfolio_tracker():
    with open('portfolio_tracker.py', 'r') as f:
        content = f.read()

    # 1. Add pending_reasons to __init__
    if 'self.pending_reasons' not in content:
        content = content.replace(
            'self.open_positions: Dict[str, Dict] = {}',
            'self.open_positions: Dict[str, Dict] = {}\n        self.pending_reasons: Dict[str, str] = {}'
        )

    # 2. Add set_pending_reason and is_simulated property
    if 'def set_pending_reason' not in content:
        content = content.replace(
            '    def _persist_trade(self, trade: TradeRecord) -> None:',
            '''    @property
    def is_simulated(self) -> bool:
        return os.getenv("PAPER_TRADING_ENABLED", "false").lower() == "true" and os.getenv("TRADING_MARKET", "IN").upper() == "IN"

    def set_pending_reason(self, symbol: str, reason: str) -> None:
        self.pending_reasons[symbol] = reason

    def _persist_trade(self, trade: TradeRecord) -> None:'''
        )

    # 3. Replace the update method sync logic
    old_update_logic = '''            if positions is not None:
                # Detect natively closed positions (e.g., via Alpaca Trailing Stop)
                for symbol, old_pos in self.open_positions.items():
                    if symbol not in positions:
                        current_price = ibkr_connector.get_current_price(symbol)
                        if current_price is None:
                            current_price = float(old_pos.get("avg_cost", 0.0))
                        
                        qty = float(old_pos.get("quantity", 0))
                        avg_cost = float(old_pos.get("avg_cost", 0.0))
                        pnl = (current_price - avg_cost) * qty
                        
                        logger.info("Broker natively closed position for %s (e.g. Trailing Stop). Recording SELL.", symbol)
                        self.record_trade(
                            symbol=symbol,
                            action="SELL",
                            quantity=qty,
                            price=current_price,
                            pnl=pnl,
                            exit_reason="NATIVE_TRAILING_STOP"
                        )
                self.open_positions = positions'''

    new_update_logic = '''            if positions is not None:
                # Detect natively closed positions (or fulfilled agent sell orders)
                for symbol, old_pos in list(self.open_positions.items()):
                    if symbol not in positions:
                        current_price = ibkr_connector.get_current_price(symbol)
                        if current_price is None:
                            current_price = float(old_pos.get("avg_cost", 0.0))
                        
                        qty = float(old_pos.get("quantity", 0))
                        avg_cost = float(old_pos.get("avg_cost", 0.0))
                        pnl = (current_price - avg_cost) * qty
                        
                        reason = self.pending_reasons.pop(symbol, "NATIVE_TRAILING_STOP")
                        logger.info("Broker position closed for %s. Recording SELL (reason=%s).", symbol, reason)
                        self.record_trade(
                            symbol=symbol,
                            action="SELL",
                            quantity=qty,
                            price=current_price,
                            pnl=pnl,
                            exit_reason=reason
                        )
                
                # Detect natively opened positions (or fulfilled agent buy orders)
                for symbol, pos in list(positions.items()):
                    if symbol not in self.open_positions:
                        reason = self.pending_reasons.pop(symbol, None)
                        current_price = float(pos.get("avg_cost", 0.0))
                        qty = float(pos.get("quantity", 0))
                        logger.info("Broker new position detected for %s. Recording BUY.", symbol)
                        self.record_trade(
                            symbol=symbol,
                            action="BUY",
                            quantity=qty,
                            price=current_price,
                            pnl=None,
                            exit_reason=reason
                        )

                self.open_positions = positions'''
                
    content = content.replace(old_update_logic, new_update_logic)

    with open('portfolio_tracker.py', 'w') as f:
        f.write(content)
        
def fix_agent():
    with open('agent.py', 'r') as f:
        content = f.read()
        
    old_instant_exit = '''                            if exit_trigger == "TRAILING_STOP" and pnl < 0:
                                pnl = 0.0
                            self.portfolio.record_trade(
                                symbol=symbol,
                                action="SELL",
                                quantity=qty,
                                price=price,
                                pnl=pnl,
                                exit_reason=exit_trigger,
                            )'''
                            
    new_instant_exit = '''                            if exit_trigger == "TRAILING_STOP" and pnl < 0:
                                pnl = 0.0
                            if self.portfolio.is_simulated:
                                self.portfolio.record_trade(
                                    symbol=symbol,
                                    action="SELL",
                                    quantity=qty,
                                    price=price,
                                    pnl=pnl,
                                    exit_reason=exit_trigger,
                                )
                                self.portfolio.open_positions.pop(symbol, None)
                            else:
                                self.portfolio.set_pending_reason(symbol, exit_trigger)'''
    content = content.replace(old_instant_exit, new_instant_exit)
    
    old_exec_buy_sell = '''                        self.portfolio.record_trade(
                            symbol=symbol,
                            action=decision.action,
                            quantity=decision.quantity,
                            price=current_price,
                            pnl=pnl,
                            exit_reason=exit_reason,
                        )'''
    new_exec_buy_sell = '''                        if self.portfolio.is_simulated:
                            self.portfolio.record_trade(
                                symbol=symbol,
                                action=decision.action,
                                quantity=decision.quantity,
                                price=current_price,
                                pnl=pnl,
                                exit_reason=exit_reason,
                            )
                            if decision.action == "SELL":
                                self.portfolio.open_positions.pop(symbol, None)
                        else:
                            self.portfolio.set_pending_reason(symbol, exit_reason or "BUY")'''
    content = content.replace(old_exec_buy_sell, new_exec_buy_sell)

    old_intraday_stop = '''                        if exit_trigger == "TRAILING_STOP" and pnl < 0:
                            pnl = 0.0
                        self.portfolio.record_trade(
                            symbol=symbol,
                            action="SELL",
                            quantity=qty,
                            price=current_price,
                            pnl=pnl,
                            exit_reason=exit_trigger,
                        )'''
    new_intraday_stop = '''                        if exit_trigger == "TRAILING_STOP" and pnl < 0:
                            pnl = 0.0
                        if self.portfolio.is_simulated:
                            self.portfolio.record_trade(
                                symbol=symbol,
                                action="SELL",
                                quantity=qty,
                                price=current_price,
                                pnl=pnl,
                                exit_reason=exit_trigger,
                            )
                            self.portfolio.open_positions.pop(symbol, None)
                        else:
                            self.portfolio.set_pending_reason(symbol, exit_trigger)'''
    content = content.replace(old_intraday_stop, new_intraday_stop)
    
    old_morning_gap = '''                            pnl = (current_price - avg_cost) * qty
                            self.portfolio.record_trade(
                                symbol=symbol,
                                action="SELL",
                                quantity=qty,
                                price=current_price,
                                pnl=pnl,
                                exit_reason="MORNING_GAP_STOP",
                            )'''
    new_morning_gap = '''                            pnl = (current_price - avg_cost) * qty
                            if self.portfolio.is_simulated:
                                self.portfolio.record_trade(
                                    symbol=symbol,
                                    action="SELL",
                                    quantity=qty,
                                    price=current_price,
                                    pnl=pnl,
                                    exit_reason="MORNING_GAP_STOP",
                                )
                                self.portfolio.open_positions.pop(symbol, None)
                            else:
                                self.portfolio.set_pending_reason(symbol, "MORNING_GAP_STOP")'''
    content = content.replace(old_morning_gap, new_morning_gap)
    
    old_eod = '''                    pnl = (current_price - avg_cost) * qty if current_price > 0 else None
                    self.portfolio.record_trade(
                        symbol=symbol,
                        action="SELL",
                        quantity=qty,
                        price=current_price,
                        pnl=pnl,
                        exit_reason=reason,
                    )'''
    new_eod = '''                    pnl = (current_price - avg_cost) * qty if current_price > 0 else None
                    if self.portfolio.is_simulated:
                        self.portfolio.record_trade(
                            symbol=symbol,
                            action="SELL",
                            quantity=qty,
                            price=current_price,
                            pnl=pnl,
                            exit_reason=reason,
                        )
                        self.portfolio.open_positions.pop(symbol, None)
                    else:
                        self.portfolio.set_pending_reason(symbol, reason)'''
    content = content.replace(old_eod, new_eod)

    old_premarket_eod = '''                                                        pnl = (current_price - avg_cost) * qty if current_price > 0 else None
                                                        self.portfolio.record_trade(
                                                            symbol=symbol,
                                                            action="SELL",
                                                            quantity=qty,
                                                            price=current_price,
                                                            pnl=pnl,
                                                            exit_reason="PREMARKET_DUMP",
                                                        )'''
    new_premarket_eod = '''                                                        pnl = (current_price - avg_cost) * qty if current_price > 0 else None
                                                        if self.portfolio.is_simulated:
                                                            self.portfolio.record_trade(
                                                                symbol=symbol,
                                                                action="SELL",
                                                                quantity=qty,
                                                                price=current_price,
                                                                pnl=pnl,
                                                                exit_reason="PREMARKET_DUMP",
                                                            )
                                                            self.portfolio.open_positions.pop(symbol, None)
                                                        else:
                                                            self.portfolio.set_pending_reason(symbol, "PREMARKET_DUMP")'''
    content = content.replace(old_premarket_eod, new_premarket_eod)

    with open('agent.py', 'w') as f:
        f.write(content)

fix_portfolio_tracker()
fix_agent()
