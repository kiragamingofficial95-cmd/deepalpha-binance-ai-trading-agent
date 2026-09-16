import datetime
import math
from typing import Any, Optional
import numpy as np
import pandas as pd
from sqlalchemy import select, desc
from app.database import AsyncSessionLocal, Trade, PaperBalance
from app.binance_client import binance_client

class AnalyticsEngine:
    async def get_performance_summary(self, mode: Optional[str] = None) -> dict[str, Any]:
        """
        Calculates full quantitative performance metrics including realized and live unrealized PnL.
        """
        async with AsyncSessionLocal() as session:
            query = select(Trade).order_by(Trade.entry_time.asc())
            if mode and mode in ["PAPER", "REAL"]:
                query = query.where(Trade.mode == mode)
            
            res = await session.execute(query)
            all_trades = res.scalars().all()

            # Balances
            bal_res = await session.execute(select(PaperBalance))
            balances = [b.to_dict() for b in bal_res.scalars().all()]
            paper_usdt = next((b["free"] for b in balances if b["asset"] == "USDT"), 10000.0)

        closed_trades = [t for t in all_trades if t.status == "CLOSED"]
        open_trades = [t for t in all_trades if t.status == "OPEN"]

        total_trades_count = len(all_trades)
        closed_count = len(closed_trades)
        open_count = len(open_trades)

        # Compute live unrealized PnL across open positions
        total_unrealized_pnl = 0.0
        open_positions_with_live = []

        for ot in open_trades:
            ticker = await binance_client.fetch_ticker(ot.symbol)
            curr_p = ticker.get("price", ot.entry_price)
            if ot.side == "BUY":
                u_pnl = (curr_p - ot.entry_price) * ot.quantity - ot.fees
                u_pct = ((curr_p - ot.entry_price) / ot.entry_price) * 100.0
            else:
                u_pnl = (ot.entry_price - curr_p) * ot.quantity - ot.fees
                u_pct = ((ot.entry_price - curr_p) / ot.entry_price) * 100.0
            
            total_unrealized_pnl += u_pnl
            ot_dict = ot.to_dict()
            ot_dict["current_price"] = round(curr_p, 4)
            ot_dict["live_pnl"] = round(u_pnl, 2)
            ot_dict["live_pnl_pct"] = round(u_pct, 2)
            open_positions_with_live.append(ot_dict)

        if not closed_trades:
            return {
                "mode": mode or "ALL",
                "paper_balance_usdt": round(paper_usdt, 2),
                "total_trades": total_trades_count,
                "open_trades_count": open_count,
                "closed_trades_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate_pct": 0.0,
                "total_realized_pnl": 0.0,
                "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                "total_fees": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_dollar": 0.0,
                "avg_win_dollar": 0.0,
                "avg_loss_dollar": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "avg_duration_minutes": 0.0,
                "equity_curve": [{"time": datetime.datetime.utcnow().isoformat(), "equity": paper_usdt, "pnl": 0.0}],
                "open_positions": open_positions_with_live,
                "symbol_breakdown": {}
            }

        # Calculate metrics
        pnls = [t.pnl for t in closed_trades]
        winning_trades = [p for p in pnls if p > 0]
        losing_trades = [p for p in pnls if p <= 0]

        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / closed_count * 100.0) if closed_count > 0 else 0.0

        gross_profit = sum(winning_trades)
        gross_loss = abs(sum(losing_trades))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)

        total_pnl = sum(pnls)
        total_fees = sum(t.fees for t in all_trades)

        avg_win = round(sum(winning_trades) / win_count, 2) if win_count > 0 else 0.0
        avg_loss = round(sum(losing_trades) / loss_count, 2) if loss_count > 0 else 0.0
        largest_win = round(max(winning_trades), 2) if winning_trades else 0.0
        largest_loss = round(min(losing_trades), 2) if losing_trades else 0.0

        # Holding durations
        durations = [
            (t.exit_time - t.entry_time).total_seconds() / 60.0
            for t in closed_trades if t.exit_time and t.entry_time
        ]
        avg_duration = round(sum(durations) / len(durations), 1) if durations else 0.0

        # Equity Curve & Max Drawdown
        start_equity = 10000.0
        running_equity = start_equity
        equity_curve = [{"time": (closed_trades[0].entry_time - datetime.timedelta(minutes=1)).isoformat(), "equity": start_equity, "pnl": 0.0}]
        
        peak = start_equity
        max_dd_dollar = 0.0
        max_dd_pct = 0.0
        returns = []

        for t in closed_trades:
            running_equity += t.pnl
            if running_equity > peak:
                peak = running_equity
            dd_dollar = peak - running_equity
            dd_pct = (dd_dollar / peak * 100.0) if peak > 0 else 0.0
            if dd_dollar > max_dd_dollar:
                max_dd_dollar = dd_dollar
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

            returns.append(t.pnl / start_equity)
            equity_curve.append({
                "time": t.exit_time.isoformat() if t.exit_time else t.entry_time.isoformat(),
                "equity": round(running_equity, 2),
                "pnl": round(t.pnl, 2),
                "symbol": t.symbol
            })

        # Sharpe Ratio
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = round((np.mean(returns) / np.std(returns)) * math.sqrt(365 * 24), 2)
        else:
            sharpe = 0.0

        # Symbol breakdown
        symbol_breakdown = {}
        for t in closed_trades:
            if t.symbol not in symbol_breakdown:
                symbol_breakdown[t.symbol] = {"trades": 0, "wins": 0, "pnl": 0.0}
            symbol_breakdown[t.symbol]["trades"] += 1
            if t.pnl > 0:
                symbol_breakdown[t.symbol]["wins"] += 1
            symbol_breakdown[t.symbol]["pnl"] = round(symbol_breakdown[t.symbol]["pnl"] + t.pnl, 2)

        for s, d in symbol_breakdown.items():
            d["win_rate"] = round((d["wins"] / d["trades"] * 100.0), 1)

        return {
            "mode": mode or "ALL",
            "paper_balance_usdt": round(paper_usdt, 2),
            "total_trades": total_trades_count,
            "open_trades_count": open_count,
            "closed_trades_count": closed_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate_pct": round(win_rate, 2),
            "total_realized_pnl": round(total_pnl, 2),
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_fees": round(total_fees, 2),
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_dollar": round(max_dd_dollar, 2),
            "avg_win_dollar": avg_win,
            "avg_loss_dollar": avg_loss,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "avg_duration_minutes": avg_duration,
            "equity_curve": equity_curve,
            "open_positions": open_positions_with_live,
            "symbol_breakdown": symbol_breakdown
        }

    async def get_trades_list(
        self,
        mode: Optional[str] = None,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            query = select(Trade).order_by(desc(Trade.entry_time))
            if mode:
                query = query.where(Trade.mode == mode)
            if status:
                query = query.where(Trade.status == status)
            if symbol:
                query = query.where(Trade.symbol == symbol)

            res = await session.execute(query.limit(limit).offset(offset))
            trades = [t.to_dict() for t in res.scalars().all()]

            # Dynamically compute live PnL for open positions
            for t in trades:
                if t["status"] == "OPEN":
                    ticker = await binance_client.fetch_ticker(t["symbol"])
                    curr_p = ticker.get("price", t["entry_price"])
                    t["current_price"] = round(curr_p, 4)
                    if t["side"] == "BUY":
                        u_pnl = (curr_p - t["entry_price"]) * t["quantity"] - t["fees"]
                        u_pct = ((curr_p - t["entry_price"]) / t["entry_price"]) * 100.0
                    else:
                        u_pnl = (t["entry_price"] - curr_p) * t["quantity"] - t["fees"]
                        u_pct = ((t["entry_price"] - curr_p) / t["entry_price"]) * 100.0
                    t["live_pnl"] = round(u_pnl, 2)
                    t["live_pnl_pct"] = round(u_pct, 2)

            return {"trades": trades, "count": len(trades)}


analytics_engine = AnalyticsEngine()
