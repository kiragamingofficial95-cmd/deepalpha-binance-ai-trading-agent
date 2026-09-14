import datetime
import json
import logging
from typing import Any, Optional
from sqlalchemy import select, update
from app.database import AsyncSessionLocal, Trade, PaperBalance
from app.binance_client import binance_client

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
        technical_snapshot: Optional[dict[str, Any]] = None
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

            # Deduct USDT
            usdt_bal.free -= amount_usdt

            # Create trade record
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
                status="OPEN",
                strategy_name=strategy_name,
                strategy_version=strategy_version,
                entry_reason=entry_reason,
                technical_snapshot=json.dumps(technical_snapshot or {}),
                entry_time=datetime.datetime.utcnow()
            )
            session.add(trade)
            await session.commit()
            await session.refresh(trade)

            logger.info(f"[PAPER ENGINE] Opened {side} position for {symbol}: qty={quantity:.6f} @ ${fill_price:.4f} (Amount: ${amount_usdt:.2f})")
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

            # Update trade record
            trade.exit_price = fill_exit_price
            trade.exit_time = datetime.datetime.utcnow()
            trade.exit_reason = exit_reason
            trade.status = "CLOSED"
            trade.pnl = pnl
            trade.pnl_pct = pnl_pct
            trade.fees = total_fees

            # Credit returned USDT to balance
            usdt_res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
            usdt_bal = usdt_res.scalar_one_or_none()
            if usdt_bal:
                usdt_bal.free += net_returned_usdt
            else:
                session.add(PaperBalance(asset="USDT", free=10000.0 + pnl, locked=0.0))

            await session.commit()
            await session.refresh(trade)

            logger.info(f"[PAPER ENGINE] Closed trade #{trade.id} ({trade.symbol}): Exit=${fill_exit_price:.4f}, PnL=${pnl:.2f} ({pnl_pct:+.2f}%), Reason={exit_reason}")
            return {"success": True, "trade": trade.to_dict()}

    async def evaluate_open_positions_triggers(self) -> list[dict[str, Any]]:
        """
        Scans all open positions and triggers Stop Loss, Take Profit or Trailing Stop if hit.
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

                # Trailing stop update
                if trade.highest_price is None or curr_price > trade.highest_price:
                    trade.highest_price = curr_price
                    if trade.trailing_stop_pct and trade.side == "BUY":
                        new_sl = curr_price * (1 - trade.trailing_stop_pct / 100.0)
                        if trade.stop_loss is None or new_sl > trade.stop_loss:
                            trade.stop_loss = new_sl

                should_close = False
                close_reason = ""

                # Check SL/TP conditions
                if trade.side == "BUY":
                    if trade.stop_loss and curr_price <= trade.stop_loss:
                        should_close = True
                        close_reason = f"STOP_LOSS_HIT (${curr_price:.4f} <= ${trade.stop_loss:.4f})"
                    elif trade.take_profit and curr_price >= trade.take_profit:
                        should_close = True
                        close_reason = f"TAKE_PROFIT_HIT (${curr_price:.4f} >= ${trade.take_profit:.4f})"
                elif trade.side == "SELL":
                    if trade.stop_loss and curr_price >= trade.stop_loss:
                        should_close = True
                        close_reason = f"STOP_LOSS_HIT (${curr_price:.4f} >= ${trade.stop_loss:.4f})"
                    elif trade.take_profit and curr_price <= trade.take_profit:
                        should_close = True
                        close_reason = f"TAKE_PROFIT_HIT (${curr_price:.4f} <= ${trade.take_profit:.4f})"

                if should_close:
                    await session.commit()  # Save any highest_price changes before close
                    result = await self.close_position(trade.id, exit_reason=close_reason, custom_exit_price=curr_price)
                    if result.get("success"):
                        closed_trades.append(result["trade"])

        return closed_trades


paper_engine = PaperTradingEngine()
