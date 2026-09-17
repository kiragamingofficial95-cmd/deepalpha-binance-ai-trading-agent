import datetime
import json
import logging
from typing import Any, Optional
from sqlalchemy import select, update
from app.database import AsyncSessionLocal, Trade, PaperBalance
from app.binance_client import binance_client
from app.config import settings

logger = logging.getLogger("paper_engine")

class PaperTradingEngine:
    """
    High-fidelity simulated trading engine replicating real exchange mechanics:
    - Real-time market price fills with realistic slippage (0.02% default)
    - Realistic exchange fee deduction (0.075% standard Binance VIP0 maker/taker)
    - Balance state machine with free & locked USDT and asset allocations
    - Automated Stop-Loss & Take-Profit management
    """
    FEE_RATE = 0.00075  # 0.075% fee
    SLIPPAGE_RATE = 0.0002  # 0.02% slippage simulation
    
    # Minimum executable RR - trades below this are rejected post-fill
    MIN_EXECUTABLE_RR = 2.0

    async def get_balance(self, asset: str = "USDT") -> float:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(PaperBalance).where(PaperBalance.asset == asset))
            bal = res.scalar_one_or_none()
            if bal:
                return bal.free
            return 0.0

    async def get_all_balances(self) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(PaperBalance))
            balances = res.scalars().all()
            return [b.to_dict() for b in balances]

    async def reset_balance(self, initial_usdt: float = 10000.0) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            # Delete all non-USDT balances or set to 0
            res = await session.execute(select(PaperBalance))
            for b in res.scalars().all():
                if b.asset == "USDT":
                    b.free = initial_usdt
                    b.locked = 0.0
                else:
                    await session.delete(b)
            await session.commit()
            return {"success": True, "message": f"Paper balance reset to {initial_usdt} USDT"}

    def _calculate_executable_rr(self, side: str, entry_price: float, stop_loss: float, take_profit: float) -> float:
        """Calculate executable RR from actual prices."""
        if side == "BUY":
            risk_dist = entry_price - stop_loss
            reward_dist = take_profit - entry_price
        else:
            risk_dist = stop_loss - entry_price
            reward_dist = entry_price - take_profit
        
        if risk_dist <= 0:
            return 0.0
        return round(reward_dist / risk_dist, 2)

    async def open_position(
        self,
        symbol: str,
        side: str,  # BUY or SELL
        amount_usdt: float,
        strategy_name: str = "AI_Adaptive",
        strategy_version: int = 1,
        entry_reason: str = "",
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        technical_snapshot: Optional[dict[str, Any]] = None,
        # New fields for RR validation and tracking
        structural_rr: Optional[float] = None,
        estimated_executable_rr: Optional[float] = None,
        setup_id: Optional[str] = None,
        correlation_group: Optional[str] = None,
        initial_risk_usd: Optional[float] = None,
        fees_estimate: Optional[float] = None,
        slippage_estimate: Optional[float] = None,
        llm_decision: Optional[str] = None,
        llm_confidence: Optional[float] = None,
        llm_model: Optional[str] = None,
        llm_reasoning: Optional[str] = None,
        llm_prompt_version: Optional[str] = None,
        rule_engine_decision: Optional[str] = None,
        market_state_snapshot: Optional[dict[str, Any]] = None,
        confirmation_candle_timestamp: Optional[datetime.datetime] = None,
    ) -> dict[str, Any]:
        symbol = symbol.upper().replace("/", "")
        side = side.upper()

        # Fetch current live price from Binance
        ticker = await binance_client.fetch_ticker(symbol)
        base_price = ticker.get("price", 0.0)
        if base_price <= 0:
            return {"success": False, "error": f"Invalid market price for {symbol}"}

        # Apply simulated slippage
        if side == "BUY":
            fill_price = base_price * (1 + self.SLIPPAGE_RATE)
        else:
            fill_price = base_price * (1 - self.SLIPPAGE_RATE)

        # Calculate quantity and fee
        fee_usdt = amount_usdt * self.FEE_RATE
        net_amount_usdt = amount_usdt - fee_usdt
        quantity = net_amount_usdt / fill_price

        # Calculate stop loss and take profit absolute prices
        stop_loss_price = None
        take_profit_price = None

        if side == "BUY":
            if stop_loss_pct:
                stop_loss_price = fill_price * (1 - stop_loss_pct / 100.0)
            if take_profit_pct:
                take_profit_price = fill_price * (1 + take_profit_pct / 100.0)
        else:
            if stop_loss_pct:
                stop_loss_price = fill_price * (1 + stop_loss_pct / 100.0)
            if take_profit_pct:
                take_profit_price = fill_price * (1 - take_profit_pct / 100.0)

        # PRE-FILL RR CHECK: Validate estimated executable RR
        if stop_loss_price and take_profit_price:
            est_rr = self._calculate_executable_rr(side, fill_price, stop_loss_price, take_profit_price)
            if est_rr < self.MIN_EXECUTABLE_RR:
                logger.warning(f"Pre-fill RR check failed for {symbol}: estimated executable RR = {est_rr} < {self.MIN_EXECUTABLE_RR}")
                return {"success": False, "error": f"Estimated executable RR {est_rr} below minimum {self.MIN_EXECUTABLE_RR}"}

        async with AsyncSessionLocal() as session:
            # Check USDT balance
            usdt_res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
            usdt_bal = usdt_res.scalar_one_or_none()
            if not usdt_bal or usdt_bal.free < amount_usdt:
                available = usdt_bal.free if usdt_bal else 0.0
                return {
                    "success": False,
                    "error": f"Insufficient paper USDT balance. Required: ${amount_usdt:.2f}, Available: ${available:.2f}"
                }

            # Deduct USDT (fees deducted from balance immediately)
            usdt_bal.free -= amount_usdt
            fee_already_deducted = fee_usdt

            # Create trade record with all new fields
            trade = Trade(
                symbol=symbol,
                side=side,
                order_type="MARKET",
                mode="PAPER",
                quantity=quantity,
                entry_price=fill_price,
                amount_usdt=amount_usdt,
                fees=fee_usdt,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                trailing_stop_pct=trailing_stop_pct,
                highest_price=fill_price,
                lowest_price=fill_price,
                status="OPEN",
                strategy_name=strategy_name,
                strategy_version=strategy_version,
                entry_reason=entry_reason,
                technical_snapshot=json.dumps(technical_snapshot or {}),
                entry_time=datetime.datetime.now(datetime.timezone.utc),
                # New tracking fields
                structural_rr=structural_rr,
                estimated_executable_rr=estimated_executable_rr,
                executable_rr=None,  # Will be set after fill confirmation
                setup_id=setup_id,
                correlation_group=correlation_group,
                initial_risk_usd=initial_risk_usd,
                risk_usd=initial_risk_usd,
                fees_estimate=fees_estimate,
                slippage_estimate=slippage_estimate,
                llm_decision=llm_decision,
                llm_confidence=llm_confidence,
                llm_model=llm_model,
                llm_reasoning=llm_reasoning,
                llm_prompt_version=llm_prompt_version,
                rule_engine_decision=rule_engine_decision,
                market_state_snapshot=json.dumps(market_state_snapshot) if market_state_snapshot else None,
                confirmation_candle_timestamp=confirmation_candle_timestamp,
            )
            session.add(trade)
            await session.commit()
            await session.refresh(trade)

            # POST-FILL RR CHECK: Recalculate with actual fill price
            if stop_loss_price and take_profit_price:
                actual_rr = self._calculate_executable_rr(side, fill_price, stop_loss_price, take_profit_price)
                trade.executable_rr = actual_rr
                
                if actual_rr < self.MIN_EXECUTABLE_RR:
                    # Reject the trade post-fill - close immediately
                    logger.warning(f"Post-fill RR check failed for {symbol}: actual executable RR = {actual_rr} < {self.MIN_EXECUTABLE_RR}")
                    trade.status = "CANCELLED"
                    trade.exit_reason = f"REJECTED_RR: executable RR {actual_rr} below minimum {self.MIN_EXECUTABLE_RR}"
                    trade.exit_reason_enum = "REJECTED_RR"
                    trade.exit_time = datetime.datetime.now(datetime.timezone.utc)
                    trade.exit_price = fill_price
                    
                    # Return the USDT including fees already deducted
                    usdt_bal.free += amount_usdt
                    
                    await session.commit()
                    await session.refresh(trade)
                    return {"success": False, "error": f"Post-fill executable RR {actual_rr} below minimum {self.MIN_EXECUTABLE_RR}", "trade": trade.to_dict()}

            logger.info(f"[PAPER ENGINE] Opened {side} position for {symbol}: qty={quantity:.6f} @ ${fill_price:.4f} (Amount: ${amount_usdt:.2f}, RR: {trade.executable_rr})")
            return {"success": True, "trade": trade.to_dict()}

    async def close_position(
        self,
        trade_id: int,
        exit_reason: str = "MANUAL_CLOSE",
        custom_exit_price: Optional[float] = None
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Trade).where(Trade.id == trade_id, Trade.status == "OPEN"))
            trade = res.scalar_one_or_none()
            if not trade:
                return {"success": False, "error": f"Open trade #{trade_id} not found"}

            if custom_exit_price:
                fill_exit_price = custom_exit_price
            else:
                ticker = await binance_client.fetch_ticker(trade.symbol)
                current_price = ticker.get("price", trade.entry_price)
                if trade.side == "BUY":
                    fill_exit_price = current_price * (1 - self.SLIPPAGE_RATE)
                else:
                    fill_exit_price = current_price * (1 + self.SLIPPAGE_RATE)

            # Calculate gross returned USDT and PnL
            gross_exit_amount = trade.quantity * fill_exit_price
            exit_fee = gross_exit_amount * self.FEE_RATE
            net_returned_usdt = gross_exit_amount - exit_fee
            total_fees = trade.fees + exit_fee

            if trade.side == "BUY":
                pnl = (fill_exit_price - trade.entry_price) * trade.quantity - total_fees
                pnl_pct = ((fill_exit_price - trade.entry_price) / trade.entry_price) * 100.0
            else:
                pnl = (trade.entry_price - fill_exit_price) * trade.quantity - total_fees
                pnl_pct = ((trade.entry_price - fill_exit_price) / trade.entry_price) * 100.0

# Calculate realized R
            realized_r = None
            if trade.initial_risk_usd and trade.initial_risk_usd > 0:
                realized_r = round(pnl / trade.initial_risk_usd, 2)

            # Ensure executable_rr is set (calculate from fill prices if missing)
            if trade.executable_rr is None and trade.stop_loss and trade.take_profit:
                trade.executable_rr = self._calculate_executable_rr(trade.side, trade.entry_price, trade.stop_loss, trade.take_profit)

            # Map exit reason to standardized enum
            reason_enum = exit_reason.split("(")[0].strip().upper() if exit_reason else "MANUAL_CLOSE"
            if reason_enum not in ("SL", "TP", "MANUAL_CLOSE", "REJECTED_RR", "CANCELLED"):
                reason_enum = "MANUAL_CLOSE"

            # Update trade record
            trade.exit_price = fill_exit_price
            trade.exit_time = datetime.datetime.now(datetime.timezone.utc)
            trade.exit_reason = exit_reason
            trade.exit_reason_enum = reason_enum
            trade.status = "CLOSED"
            trade.pnl = pnl
            trade.pnl_pct = pnl_pct
            trade.fees = total_fees
            trade.realized_r = realized_r

            # Credit returned USDT to balance
            usdt_res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
            usdt_bal = usdt_res.scalar_one_or_none()
            if usdt_bal:
                usdt_bal.free += net_returned_usdt
            else:
                session.add(PaperBalance(asset="USDT", free=10000.0 + pnl, locked=0.0))

            await session.commit()
            await session.refresh(trade)

            logger.info(f"[PAPER ENGINE] Closed trade #{trade.id} ({trade.symbol}): Exit=${fill_exit_price:.4f}, PnL=${pnl:.2f} ({pnl_pct:+.2f}%), R={realized_r}, Reason={exit_reason}")
            return {"success": True, "trade": trade.to_dict()}

    async def evaluate_open_positions_triggers(self) -> list[dict[str, Any]]:
        """
        Scans all open positions and triggers Stop Loss, Take Profit or Trailing Stop if hit.
        Also tracks MFE/MAE for open positions.
        """
        closed_trades = []
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Trade).where(Trade.status == "OPEN", Trade.mode == "PAPER"))
            open_trades = res.scalars().all()

            for trade in open_trades:
                ticker = await binance_client.fetch_ticker(trade.symbol)
                curr_price = ticker.get("price", 0.0)
                if curr_price <= 0:
                    continue

                # Update MFE/MAE
                if trade.side == "BUY":
                    favorable = curr_price - trade.entry_price
                    adverse = trade.entry_price - curr_price
                else:
                    favorable = trade.entry_price - curr_price
                    adverse = curr_price - trade.entry_price
                
                if favorable > 0:
                    mfe_usd = favorable * trade.quantity
                    if mfe_usd > trade.mfe:
                        trade.mfe = mfe_usd
                        if trade.initial_risk_usd and trade.initial_risk_usd > 0:
                            trade.mfe_r = round(mfe_usd / trade.initial_risk_usd, 2)
                
                if adverse > 0:
                    mae_usd = adverse * trade.quantity
                    if mae_usd > trade.mae:
                        trade.mae = mae_usd
                        if trade.initial_risk_usd and trade.initial_risk_usd > 0:
                            trade.mae_r = round(mae_usd / trade.initial_risk_usd, 2)

                # Trailing stop update
                if trade.highest_price is None or curr_price > trade.highest_price:
                    trade.highest_price = curr_price
                    if trade.trailing_stop_pct and trade.side == "BUY":
                        new_sl = curr_price * (1 - trade.trailing_stop_pct / 100.0)
                        if trade.stop_loss is None or new_sl > trade.stop_loss:
                            trade.stop_loss = new_sl
                elif trade.lowest_price is None or curr_price < trade.lowest_price:
                    trade.lowest_price = curr_price
                    if trade.trailing_stop_pct and trade.side == "SELL":
                        new_sl = curr_price * (1 + trade.trailing_stop_pct / 100.0)
                        if trade.stop_loss is None or new_sl < trade.stop_loss:
                            trade.stop_loss = new_sl

                should_close = False
                close_reason = ""
                close_reason_enum = ""

                # Check SL/TP conditions with deterministic candle-resolution policy.
                # When both SL and TP are touched in the same candle (gap scenario),
                # SL takes priority as it represents the trade invalidation.
                # This is documented as: SL > TP ordering on ambiguous candles.
                should_close = False
                close_reason = ""
                close_reason_enum = ""

                if trade.side == "BUY":
                    if trade.stop_loss and curr_price <= trade.stop_loss:
                        should_close = True
                        close_reason = f"STOP_LOSS_HIT (${curr_price:.4f} <= ${trade.stop_loss:.4f})"
                        close_reason_enum = "SL"
                    elif trade.take_profit and curr_price >= trade.take_profit:
                        should_close = True
                        close_reason = f"TAKE_PROFIT_HIT (${curr_price:.4f} >= ${trade.take_profit:.4f})"
                        close_reason_enum = "TP"
                elif trade.side == "SELL":
                    if trade.stop_loss and curr_price >= trade.stop_loss:
                        should_close = True
                        close_reason = f"STOP_LOSS_HIT (${curr_price:.4f} >= ${trade.stop_loss:.4f})"
                        close_reason_enum = "SL"
                    elif trade.take_profit and curr_price <= trade.take_profit:
                        should_close = True
                        close_reason = f"TAKE_PROFIT_HIT (${curr_price:.4f} <= ${trade.take_profit:.4f})"
                        close_reason_enum = "TP"

                if should_close:
                    await session.commit()
                    result = await self.close_position(trade.id, exit_reason=close_reason, custom_exit_price=curr_price)
                    if result.get("success"):
                        closed_trades.append(result["trade"])

        return closed_trades


paper_engine = PaperTradingEngine()
