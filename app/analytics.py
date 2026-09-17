import datetime
import math
from typing import Any, Optional, Tuple
import numpy as np
import pandas as pd
from sqlalchemy import select, desc, func
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
                "equity_curve": [{"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "equity": paper_usdt, "pnl": 0.0}],
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

        # --- NEW: Expectancy & R-Distribution ---
        expectancy_R, rr_buckets = await self._calc_expectancy(closed_trades)
        
        # --- NEW: MFE/MAE Statistics ---
        mfe_mae = await self._calc_mfe_mae(closed_trades)
        
        # --- NEW: LLM Impact Tracking ---
        llm_impact = await self._calc_llm_impact(closed_trades)
        
        # --- NEW: Rolling Windows ---
        rolling = await self._calc_rolling_stats(closed_trades)
        
        # --- NEW: Data Sufficiency Badges ---
        data_badges = self._calc_data_sufficiency(closed_trades)

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
            "symbol_breakdown": symbol_breakdown,
            # NEW METRICS
            "expectancy_R": round(expectancy_R, 4) if expectancy_R else 0.0,
            "rr_distribution": rr_buckets,
            "mfe_mae_stats": mfe_mae,
            "llm_impact": llm_impact,
            "rolling_stats": rolling,
            "data_sufficiency": data_badges
        }

    async def _calc_expectancy(self, closed_trades: list) -> Tuple[Optional[float], dict]:
        """Calculate expectancy in R-multiples and R-distribution buckets."""
        rr_values = [t.executable_rr for t in closed_trades if t.executable_rr and t.executable_rr > 0]
        r_multiples = [t.realized_r for t in closed_trades if t.realized_r is not None]
        
        if not r_multiples:
            return None, {"bins": {}, "total_trades": 0}
        
        # Expectancy in R
        expectancy_R = sum(r_multiples) / len(r_multiples)
        
        # R-distribution buckets: 0-1R, 1-2R, 2-3R, 3R+
        buckets = {"0-1R": 0, "1-2R": 0, "2-3R": 0, "3R+": 0, "negative": 0, "total_trades": len(r_multiples)}
        for r in r_multiples:
            if r < 0:
                buckets["negative"] += 1
            elif r < 1:
                buckets["0-1R"] += 1
            elif r < 2:
                buckets["1-2R"] += 1
            elif r < 3:
                buckets["2-3R"] += 1
            else:
                buckets["3R+"] += 1
        
        return expectancy_R, buckets

    async def _calc_mfe_mae(self, closed_trades: list) -> dict:
        """Calculate MFE/MAE statistics."""
        mfe_vals = [t.mfe_r for t in closed_trades if t.mfe_r is not None and t.mfe_r != 0]
        mae_vals = [t.mae_r for t in closed_trades if t.mae_r is not None and t.mae_r != 0]
        
        if not mfe_vals:
            return {"mfe_avg": 0.0, "mfe_max": 0.0, "mfe_min": 0.0, "mae_avg": 0.0, "mae_max": 0.0, "mae_min": 0.0, "count": 0}
        
        return {
            "mfe_avg": round(np.mean(mfe_vals), 4),
            "mfe_max": round(max(mfe_vals), 4),
            "mfe_min": round(min(mfe_vals), 4),
            "mae_avg": round(np.mean(mae_vals), 4) if mae_vals else 0.0,
            "mae_max": round(max(mae_vals), 4) if mae_vals else 0.0,
            "mae_min": round(min(mae_vals), 4) if mae_vals else 0.0,
            "count": len(mfe_vals),
            "mfe_capture_pct": round(np.mean(mfe_vals) / (np.mean(mfe_vals) + abs(np.mean(mae_vals))) * 100, 1) if mae_vals else 0.0,
            "mae_capture_pct": round(abs(np.mean(mae_vals)) / (np.mean(mfe_vals) + abs(np.mean(mae_vals))) * 100, 1) if mae_vals else 0.0
        }

    async def _calc_llm_impact(self, closed_trades: list) -> dict:
        """Calculate LLM impact statistics from actual closed trades.
        Tracks rule_engine_decision vs llm_decision correlation:
        - RULE PASS + LLM PASS
        - RULE PASS + LLM FAIL
        - RULE PASS + LLM WAIT
        """
        llm_trades = [t for t in closed_trades if t.llm_decision in ("PASS", "FAIL", "WAIT")]
        if not llm_trades:
            return {"total": 0, "pass_rate": 0.0, "pass_avg_r": 0.0, "fail_avg_r": 0.0, "waitt_rate": 0.0, "model_usage": {}, "rule_vs_llm": {}}
        
        pass_trades = [t for t in llm_trades if t.llm_decision == "PASS"]
        fail_trades = [t for t in llm_trades if t.llm_decision == "FAIL"]
        wait_trades = [t for t in llm_trades if t.llm_decision == "WAIT"]
        
        pass_r = [t.realized_r for t in pass_trades if t.realized_r is not None]
        fail_r = [t.realized_r for t in fail_trades if t.realized_r is not None]
        
        model_usage = {}
        for t in llm_trades:
            m = t.llm_model or "unknown"
            model_usage[m] = model_usage.get(m, 0) + 1
        
        # Rule engine vs LLM correlation tracking
        rule_pass_llm_pass = sum(1 for t in llm_trades if t.rule_engine_decision == "PASS" and t.llm_decision == "PASS")
        rule_pass_llm_fail = sum(1 for t in llm_trades if t.rule_engine_decision == "PASS" and t.llm_decision == "FAIL")
        rule_pass_llm_wait = sum(1 for t in llm_trades if t.rule_engine_decision == "PASS" and t.llm_decision == "WAIT")
        rule_fail_trades = sum(1 for t in llm_trades if t.rule_engine_decision == "FAIL")
        
        return {
            "total": len(llm_trades),
            "pass_count": len(pass_trades),
            "fail_count": len(fail_trades),
            "wait_count": len(wait_trades),
            "pass_rate": round(len(pass_trades) / len(llm_trades) * 100, 1) if llm_trades else 0.0,
            "pass_avg_r": round(sum(pass_r) / len(pass_r), 4) if pass_r else 0.0,
            "fail_avg_r": round(sum(fail_r) / len(fail_r), 4) if fail_r else 0.0,
            "waitt_avg_r": round(sum([t.realized_r for t in wait_trades if t.realized_r is not None]) / len(wait_trades), 4) if wait_trades else 0.0,
            "model_usage": model_usage,
            "rule_vs_llm": {
                "rule_pass_llm_pass": rule_pass_llm_pass,
                "rule_pass_llm_fail": rule_pass_llm_fail,
                "rule_pass_llm_wait": rule_pass_llm_wait,
                "rule_fail_total": rule_fail_trades,
                "llm_rejected_rule_pass": rule_pass_llm_fail + rule_pass_llm_wait,
            }
        }

    async def _calc_rolling_stats(self, closed_trades: list) -> dict:
        """Calculate rolling performance windows (50/100/250/500 trades)."""
        if not closed_trades:
            return {}
        
        pnls = [t.pnl for t in closed_trades]
        r_multiples = [t.realized_r for t in closed_trades if t.realized_r is not None]
        
        windows = {}
        for window_size in [50, 100, 250, 500]:
            if len(pnls) < window_size:
                windows[f"last_{window_size}"] = {"available": False, "trades": len(pnls)}
            else:
                w_pnl = pnls[-window_size:]
                w_r = r_multiples[-window_size:] if len(r_multiples) >= window_size else []
                windows[f"last_{window_size}"] = {
                    "available": True,
                    "trades": window_size,
                    "total_pnl": round(sum(w_pnl), 2),
                    "win_rate": round(sum(1 for p in w_pnl if p > 0) / len(w_pnl) * 100, 1),
                    "avg_r": round(sum(w_r) / len(w_r), 4) if w_r else 0.0
                }
        
        return windows

    def _calc_data_sufficiency(self, closed_trades: list) -> dict:
        """Calculate data sufficiency badges per spec: INSUFFICIENT, EARLY, DEVELOPING, SUFFICIENT."""
        count = len(closed_trades)
        if count < 10:
            level = "INSUFFICIENT"
        elif count < 30:
            level = "EARLY"
        elif count < 100:
            level = "DEVELOPING"
        else:
            level = "SUFFICIENT"
        return {
            "trade_count": count,
            "statistical_significance": level,
            "expectancy_reliable": count >= 250,
            "sharpe_reliable": count >= 30,
            "win_rate_reliable": count >= 100
        }

    async def get_trade_journal(self, mode: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
        """Get detailed trade journal with all analytics fields."""
        async with AsyncSessionLocal() as session:
            query = select(Trade).order_by(desc(Trade.entry_time))
            if mode and mode in ["PAPER", "REAL"]:
                query = query.where(Trade.mode == mode)
            
            res = await session.execute(query.limit(limit))
            trades = [t.to_dict() for t in res.scalars().all()]
            return {"trades": trades, "count": len(trades)}

    async def get_llm_validation_stats(self) -> dict[str, Any]:
        """Get LLM validation statistics across all trades."""
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(
                    func.count(Trade.id),
                    func.sum(func.cast(Trade.llm_confidence, __import__('sqlalchemy').Float)).label('total_conf'),
                    func.count(Trade.id).filter(Trade.llm_decision == 'PASS').label('pass_count'),
                    func.count(Trade.id).filter(Trade.llm_decision == 'FAIL').label('fail_count'),
                    func.count(Trade.id).filter(Trade.llm_decision == 'WAIT').label('wait_count'),
                    func.count(Trade.id).filter(Trade.llm_decision == 'PASS').filter(Trade.pnl > 0).label('pass_wins'),
                ).select_from(Trade)
            )
            row = res.first()
            if not row or row[0] == 0:
                return {"total": 0, "pass_rate": 0.0, "avg_confidence": 0.0, "pass_win_rate": 0.0}
            
            total = row[0]
            avg_conf = row[1] / total if total > 0 else 0.0
            pass_count = row[2] or 0
            fail_count = row[3] or 0
            wait_count = row[4] or 0
            pass_wins = row[5] or 0
            
            return {
                "total": total,
                "pass_rate": round(pass_count / total * 100, 1) if total > 0 else 0.0,
                "fail_rate": round(fail_count / total * 100, 1) if total > 0 else 0.0,
                "wait_rate": round(wait_count / total * 100, 1) if total > 0 else 0.0,
                "avg_confidence": round(avg_conf, 3),
                "pass_win_rate": round(pass_wins / pass_count * 100, 1) if pass_count > 0 else 0.0
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
