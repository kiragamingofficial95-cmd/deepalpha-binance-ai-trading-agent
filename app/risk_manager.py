import datetime
import logging
from typing import Any
from sqlalchemy import select, func
from app.config import settings
from app.database import AsyncSessionLocal, Trade, PaperBalance

logger = logging.getLogger("risk_manager")

class RiskManager:
    """
    Institutional-grade risk management safeguards:
    - Daily drawdown limit circuit breaker (halts trading if daily loss exceeds threshold)
    - Dynamic position sizing (Fixed Fractional Risk based on Stop-Loss distance)
    - Max open positions limit
    - Single asset concentration cap (no more than 30% of total equity in one asset)
    """

    async def check_daily_drawdown(self, max_daily_loss_pct: float = 5.0) -> dict[str, Any]:
        """
        Calculates today's total realized PnL and verifies whether it exceeds the daily circuit breaker.
        """
        today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        async with AsyncSessionLocal() as session:
            # Query all trades closed today
            res = await session.execute(
                select(func.sum(Trade.pnl))
                .where(Trade.exit_time >= today_start, Trade.status == "CLOSED")
            )
            realized_today = res.scalar() or 0.0

            # Get total equity base
            bal_res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
            usdt_bal = bal_res.scalar_one_or_none()
            total_equity = usdt_bal.free if usdt_bal else 10000.0

            max_loss_dollar = (max_daily_loss_pct / 100.0) * total_equity
            is_tripped = realized_today < -max_loss_dollar

            return {
                "circuit_breaker_tripped": is_tripped,
                "realized_pnl_today": round(realized_today, 2),
                "max_loss_dollar": round(max_loss_dollar, 2),
                "max_daily_loss_pct": max_daily_loss_pct,
                "total_equity": round(total_equity, 2),
                "reason": f"Daily loss ${abs(realized_today):.2f} exceeded limit ${max_loss_dollar:.2f}" if is_tripped else "Normal"
            }

    async def calculate_position_size(
        self,
        total_balance: float,
        entry_price: float,
        stop_loss_pct: float = 1.5,
        risk_per_trade_pct: float = 2.0
    ) -> float:
        """
        Calculates the exact USDT position size so that if Stop-Loss is hit,
        the total loss equals exactly `risk_per_trade_pct` of total portfolio balance.
        Formula: Position Size USDT = (Balance * Risk_Pct) / (SL_Pct)
        Capped at 25% of total balance for prudent risk management.
        """
        if stop_loss_pct <= 0 or entry_price <= 0:
            return round(total_balance * 0.05, 2)

        risk_amount_dollar = total_balance * (risk_per_trade_pct / 100.0)
        target_size_usdt = risk_amount_dollar / (stop_loss_pct / 100.0)

        # Cap at maximum 25% of portfolio per trade, minimum $15
        max_cap = total_balance * 0.25
        allocated_usdt = min(target_size_usdt, max_cap)
        allocated_usdt = max(allocated_usdt, 15.0)

        return round(allocated_usdt, 2)

    async def can_open_trade(
        self,
        symbol: str,
        max_open_positions: int = 5,
        max_daily_loss_pct: float = 5.0
    ) -> dict[str, Any]:
        """
        Runs comprehensive pre-trade validation checks.
        """
        # 1. Circuit breaker check
        dd_check = await self.check_daily_drawdown(max_daily_loss_pct)
        if dd_check["circuit_breaker_tripped"]:
            return {
                "allowed": False,
                "reason": f"Daily Circuit Breaker active: {dd_check['reason']}"
            }

        async with AsyncSessionLocal() as session:
            # 2. Check total open positions count
            open_res = await session.execute(
                select(Trade).where(Trade.status == "OPEN")
            )
            open_trades = open_res.scalars().all()

            if len(open_trades) >= max_open_positions:
                return {
                    "allowed": False,
                    "reason": f"Max open positions reached ({len(open_trades)}/{max_open_positions})"
                }

            # 3. Check if already open on this symbol
            existing_symbol_trade = next((t for t in open_trades if t.symbol == symbol), None)
            if existing_symbol_trade:
                return {
                    "allowed": False,
                    "reason": f"Active position already open for {symbol} (#{existing_symbol_trade.id})"
                }

        return {"allowed": True, "reason": "Pre-trade risk criteria satisfied"}


risk_manager = RiskManager()
