import json
import logging
from typing import Any, Optional
import pandas as pd
from sqlalchemy import select
from app.database import AsyncSessionLocal, StrategyConfig, StrategyMemory
from app.indicators import analyze_all_indicators

logger = logging.getLogger("strategy_engine")

class StrategyEngine:
    """
    Multi-strategy evaluation engine with dynamic indicator screening and AI heuristics.
    """

    async def get_active_strategy(self) -> Optional[StrategyConfig]:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(StrategyConfig).where(StrategyConfig.is_active == True)
            )
            strategy = res.scalars().first()
            if not strategy:
                # Fallback to first available
                res_all = await session.execute(select(StrategyConfig))
                strategy = res_all.scalars().first()
            return strategy

    async def evaluate_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategy: Optional[StrategyConfig] = None
    ) -> dict[str, Any]:
        """
        Calculates indicators and evaluates the active strategy logic on the symbol.
        Returns signal dictionary with action (BUY, SELL, HOLD), confidence, SL/TP, and reasoning.
        """
        if df.empty or len(df) < 30:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "Insufficient historical candle data",
                "indicators": {}
            }

        indicators = analyze_all_indicators(df)
        if not indicators:
            return {"symbol": symbol, "action": "HOLD", "confidence": 0.0, "reason": "Indicator computation error", "indicators": {}}

        if strategy is None:
            strategy = await self.get_active_strategy()

        strat_name = strategy.name if strategy else "ai_adaptive_momentum"
        params = json.loads(strategy.parameters) if strategy and strategy.parameters else {}
        risk = json.loads(strategy.risk_settings) if strategy and strategy.risk_settings else {}

        # Default risk values if not in strategy
        sl_pct = float(risk.get("stop_loss_pct", 1.5))
        tp_pct = float(risk.get("take_profit_pct", 3.0))
        trailing_sl_pct = float(risk.get("trailing_stop_pct", 1.0))

        # Query relevant AI learned memories for extra context
        ai_memories = await self.get_learned_memories()

        # Strategy 1: AI Adaptive Momentum & Trend
        if "momentum" in strat_name or "adaptive" in strat_name:
            return self._evaluate_adaptive_momentum(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct, ai_memories)

        # Strategy 2: Bollinger Band Mean Reversion
        elif "bollinger" in strat_name or "mean_reversion" in strat_name:
            return self._evaluate_bollinger_reversion(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct)

        # Strategy 3: Grid Scalper
        elif "grid" in strat_name:
            return self._evaluate_grid_scalper(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct)

        # Default fallback: Adaptive Momentum
        return self._evaluate_adaptive_momentum(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct, ai_memories)

    def _evaluate_adaptive_momentum(
        self,
        symbol: str,
        ind: dict[str, Any],
        params: dict[str, Any],
        sl_pct: float,
        tp_pct: float,
        trailing_sl_pct: float,
        memories: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rsi = ind.get("rsi", 50.0)
        rsi_prev = ind.get("rsi_prev", 50.0)
        macd = ind.get("macd", 0.0)
        macd_signal = ind.get("macd_signal", 0.0)
        macd_hist = ind.get("macd_hist", 0.0)
        macd_hist_prev = ind.get("macd_hist_prev", 0.0)
        trend = ind.get("trend_status", "RANGING")
        price = ind.get("price", 0.0)
        ema20 = ind.get("ema20", 0.0)
        ema50 = ind.get("ema50", 0.0)
        ema200 = ind.get("ema200", 0.0)
        vol_ratio = ind.get("volume_ratio", 1.0)
        stoch_k = ind.get("stoch_k", 50.0)
        stoch_d = ind.get("stoch_d", 50.0)

        rsi_oversold = params.get("rsi_oversold", 35)
        rsi_overbought = params.get("rsi_overbought", 68)

        # AI Memory Rule Check (e.g. 200 EMA bear filter)
        is_strictly_bearish_200 = (price < ema200) and (ema20 < ema50)

        # BUY Condition:
        # 1. Bullish momentum: RSI turning up from oversold OR bullish MACD crossover / expanding histogram
        # 2. Price above EMA 50 or fast EMA 20 crossing EMA 50
        # 3. Volume expansion
        bullish_macd = (macd > macd_signal) and (macd_hist > 0)
        bullish_macd_cross = (macd_hist > 0) and (macd_hist_prev <= 0)
        bullish_rsi = (rsi > 40 and rsi < rsi_overbought and rsi > rsi_prev) or (rsi_prev <= rsi_oversold and rsi > rsi_oversold)
        bullish_ema = (price > ema20) and (ema20 >= ema50)
        stoch_bull = (stoch_k > stoch_d) and (stoch_k < 80)

        # Calculate BUY score (0 to 100)
        buy_score = 0
        reasons = []

        if bullish_macd:
            buy_score += 25
            reasons.append("MACD Bullish Histogram")
        if bullish_macd_cross:
            buy_score += 15
            reasons.append("Fresh MACD Crossover")
        if bullish_rsi:
            buy_score += 20
            reasons.append(f"RSI Bullish Uptick ({rsi})")
        if bullish_ema:
            buy_score += 20
            reasons.append("Price Above 20/50 EMA")
        if stoch_bull:
            buy_score += 10
            reasons.append("Stoch RSI Bullish")
        if vol_ratio > 1.2:
            buy_score += 10
            reasons.append(f"Volume Surge ({vol_ratio}x avg)")

        # Penalize if under heavy 200 EMA resistance
        if is_strictly_bearish_200:
            buy_score -= 20
            reasons.append("Below 200 EMA (AI Filter penalty)")

        # SELL Condition: Overbought + Bearish Crossover
        bearish_macd_cross = (macd_hist < 0) and (macd_hist_prev >= 0)
        bearish_rsi = rsi >= rsi_overbought

        sell_score = 0
        sell_reasons = []
        if bearish_macd_cross:
            sell_score += 40
            sell_reasons.append("MACD Bearish Crossover")
        if bearish_rsi:
            sell_score += 40
            sell_reasons.append(f"RSI Overbought ({rsi})")
        if price < ema20 < ema50:
            sell_score += 20
            sell_reasons.append("Price below 20/50 EMA")

        if buy_score >= 65:
            confidence = min(round(buy_score / 100.0, 2), 0.95)
            return {
                "symbol": symbol,
                "action": "BUY",
                "confidence": confidence,
                "reason": " & ".join(reasons),
                "stop_loss_pct": sl_pct,
                "take_profit_pct": tp_pct,
                "trailing_stop_pct": trailing_sl_pct,
                "indicators": ind
            }
        elif sell_score >= 70:
            confidence = min(round(sell_score / 100.0, 2), 0.95)
            return {
                "symbol": symbol,
                "action": "SELL",
                "confidence": confidence,
                "reason": " & ".join(sell_reasons),
                "stop_loss_pct": sl_pct,
                "take_profit_pct": tp_pct,
                "trailing_stop_pct": trailing_sl_pct,
                "indicators": ind
            }

        return {
            "symbol": symbol,
            "action": "HOLD",
            "confidence": 0.5,
            "reason": f"No strong breakout confluence (BuyScore: {buy_score}/100, RSI: {rsi}, Trend: {trend})",
            "indicators": ind
        }

    def _evaluate_bollinger_reversion(
        self,
        symbol: str,
        ind: dict[str, Any],
        params: dict[str, Any],
        sl_pct: float,
        tp_pct: float,
        trailing_sl_pct: float
    ) -> dict[str, Any]:
        price = ind.get("price", 0.0)
        bb_lower = ind.get("bb_lower", 0.0)
        bb_upper = ind.get("bb_upper", 0.0)
        rsi = ind.get("rsi", 50.0)
        percent_b = ind.get("bb_percent_b", 0.5)

        if percent_b <= 0.05 or (price <= bb_lower and rsi <= 35):
            return {
                "symbol": symbol,
                "action": "BUY",
                "confidence": 0.82,
                "reason": f"Bollinger Lower Band Touch (${price:.4f} <= ${bb_lower:.4f}) with RSI Oversold ({rsi})",
                "stop_loss_pct": sl_pct,
                "take_profit_pct": tp_pct,
                "trailing_stop_pct": trailing_sl_pct,
                "indicators": ind
            }
        elif percent_b >= 0.95 or (price >= bb_upper and rsi >= 65):
            return {
                "symbol": symbol,
                "action": "SELL",
                "confidence": 0.80,
                "reason": f"Bollinger Upper Band Rejection (${price:.4f} >= ${bb_upper:.4f}) with RSI Overbought ({rsi})",
                "stop_loss_pct": sl_pct,
                "take_profit_pct": tp_pct,
                "trailing_stop_pct": trailing_sl_pct,
                "indicators": ind
            }

        return {
            "symbol": symbol,
            "action": "HOLD",
            "confidence": 0.5,
            "reason": f"Price inside Bollinger Bands (%B: {percent_b:.2f}, RSI: {rsi})",
            "indicators": ind
        }

    def _evaluate_grid_scalper(
        self,
        symbol: str,
        ind: dict[str, Any],
        params: dict[str, Any],
        sl_pct: float,
        tp_pct: float,
        trailing_sl_pct: float
    ) -> dict[str, Any]:
        rsi = ind.get("rsi", 50.0)
        stoch_k = ind.get("stoch_k", 50.0)

        if rsi < 42 and stoch_k < 30:
            return {
                "symbol": symbol,
                "action": "BUY",
                "confidence": 0.75,
                "reason": f"Grid Level Dip Trigger (RSI: {rsi}, Stoch: {stoch_k})",
                "stop_loss_pct": sl_pct,
                "take_profit_pct": tp_pct,
                "trailing_stop_pct": trailing_sl_pct,
                "indicators": ind
            }
        return {
            "symbol": symbol,
            "action": "HOLD",
            "confidence": 0.5,
            "reason": "Grid awaiting optimal deviation",
            "indicators": ind
        }

    async def get_learned_memories(self) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(StrategyMemory))
            memories = res.scalars().all()
            return [m.to_dict() for m in memories]


strategy_engine = StrategyEngine()
