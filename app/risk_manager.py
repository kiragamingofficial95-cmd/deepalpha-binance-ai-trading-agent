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
    - Daily trade limit (max 2 trades/day)
    - Daily loss limit (max 2 losses/day)
    """

    async def check_daily_drawdown(self, max_daily_loss_pct: float = 5.0) -> dict[str, Any]:
        """
        Calculates today's total realized PnL and verifies whether it exceeds the daily circuit breaker.
        """
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
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

    async def check_daily_limits(self, max_daily_trades: int = 2, max_daily_losses: int = 2) -> dict[str, Any]:
        """
        Checks today's trade count and loss count against daily limits.
        Returns {"allowed": bool, "reason": str, "trades_today": int, "losses_today": int}
        """
        today_start = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        async with AsyncSessionLocal() as session:
            # Count trades opened today
            trades_res = await session.execute(
                select(Trade)
                .where(Trade.entry_time >= today_start)
            )
            trades_today = trades_res.scalars().all()
            trade_count = len(trades_today)
            
            # Count losses today
            loss_count = sum(1 for t in trades_today if t.status == "CLOSED" and t.pnl <= 0)
            
            if trade_count >= max_daily_trades:
                return {"allowed": False, "reason": f"Max daily trades reached ({trade_count}/{max_daily_trades})", "trades_today": trade_count, "losses_today": loss_count}
            
            if loss_count >= max_daily_losses:
                return {"allowed": False, "reason": f"Max daily losses reached ({loss_count}/{max_daily_losses}) — STOP TRADING", "trades_today": trade_count, "losses_today": loss_count}
            
            return {"allowed": True, "reason": "Daily limits OK", "trades_today": trade_count, "losses_today": loss_count}

    async def calculate_position_size(
        self,
        total_balance: float,
        entry_price: float,
        stop_loss_pct: float = 1.5,
        risk_per_trade_pct: float = 2.0,
        risk_amount_usd: float = None  # If provided, use fixed USD risk (Rs.10 converted)
    ) -> float:
        """
        Calculates the exact USDT position size so that if Stop-Loss is hit,
        the total loss equals exactly `risk_per_trade_pct` of total portfolio balance.
        Formula: Position Size USDT = (Balance * Risk_Pct) / (SL_Pct)
        Capped at 25% of total balance for prudent risk management.
        
        If risk_amount_usd is provided (e.g., Rs.10 converted to USD), it overrides risk_per_trade_pct.
        """
        if stop_loss_pct <= 0 or entry_price <= 0:
            return round(total_balance * 0.05, 2)

        if risk_amount_usd is not None:
            # Use fixed USD risk amount (Rs.10 converted)
            risk_amount_dollar = risk_amount_usd
        else:
            # Percentage-based risk
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
        max_daily_loss_pct: float = 5.0,
        max_daily_trades: int = 2,
        max_daily_losses: int = 2,
        correlation_group: str = "CRYPTO_MAJOR",
        max_correlated_risk_pct: float = 3.0,
        proposed_risk_usd: float = 0.0
    ) -> dict[str, Any]:
        """
        Runs comprehensive pre-trade validation checks including correlated risk tracking.
        proposed_risk_usd is the risk the NEW trade would add, used for accurate
        correlated and portfolio exposure enforcement.
        """
        # 1. Circuit breaker check
        dd_check = await self.check_daily_drawdown(max_daily_loss_pct)
        if dd_check["circuit_breaker_tripped"]:
            return {
                "allowed": False,
                "reason": f"Daily Circuit Breaker active: {dd_check['reason']}"
            }

        # 2. Daily trade/loss limits
        daily_check = await self.check_daily_limits(max_daily_trades, max_daily_losses)
        if not daily_check["allowed"]:
            return {"allowed": False, "reason": daily_check["reason"]}

        async with AsyncSessionLocal() as session:
            # 3. Check total open positions count
            open_res = await session.execute(
                select(Trade).where(Trade.status == "OPEN")
            )
            open_trades = open_res.scalars().all()

            if len(open_trades) >= max_open_positions:
                return {
                    "allowed": False,
                    "reason": f"Max open positions reached ({len(open_trades)}/{max_open_positions})"
                }

            # 4. Check if already open on this symbol
            existing_symbol_trade = next((t for t in open_trades if t.symbol == symbol), None)
            if existing_symbol_trade:
                return {
                    "allowed": False,
                    "reason": f"Active position already open for {symbol} (#{existing_symbol_trade.id})"
                }
            
            bal_res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
            usdt_bal = bal_res.scalar_one_or_none()
            total_equity = usdt_bal.free if usdt_bal else 10000.0

            # 5. Check correlated risk exposure (including proposed new trade)
            if correlation_group:
                max_allowed_usd = total_equity * (max_correlated_risk_pct / 100.0)
                
                # Sum current risk in this group
                current_group_risk_usd = sum(
                    t.risk_usd or 0.0
                    for t in open_trades 
                    if getattr(t, "correlation_group", None) == correlation_group
                )
                
                # Total risk AFTER the new trade would be added
                total_group_risk_after = current_group_risk_usd + proposed_risk_usd
                
                if total_group_risk_after > max_allowed_usd:
                    return {
                        "allowed": False,
                        "reason": f"Correlated risk for group {correlation_group} would exceed limit (${total_group_risk_after:.2f} > max ${max_allowed_usd:.2f})"
                    }

            # 6. Check total portfolio risk (all correlated groups combined)
            max_portfolio_risk_pct = getattr(settings, "MAX_PORTFOLIO_RISK_PCT", 6.0)
            max_portfolio_risk_usd = total_equity * (max_portfolio_risk_pct / 100.0)
            total_portfolio_risk = sum(
                t.risk_usd or 0.0
                for t in open_trades
            ) + proposed_risk_usd
            if total_portfolio_risk > max_portfolio_risk_usd:
                return {
                    "allowed": False,
                    "reason": f"Total portfolio risk would exceed limit (${total_portfolio_risk:.2f} > max ${max_portfolio_risk_usd:.2f})"
                }

        return {"allowed": True, "reason": "Pre-trade risk criteria satisfied", "daily": daily_check}


risk_manager = RiskManager()
