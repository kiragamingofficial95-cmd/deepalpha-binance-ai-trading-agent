import json
import logging
from typing import Any, Optional
import pandas as pd
from sqlalchemy import select
from app.database import AsyncSessionLocal, StrategyConfig, StrategyMemory
from app.indicators import analyze_all_indicators, calculate_atr, calculate_ema

logger = logging.getLogger("strategy_engine")


def _find_swing_highs_lows(df: pd.DataFrame, lookback: int = 5) -> dict[str, Any]:
    """Detect swing highs and swing lows from OHLCV data."""
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(df) - lookback):
        if all(highs[i] >= highs[i - j] for j in range(1, lookback + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, lookback + 1)
        ):
            swing_highs.append({"index": i, "price": float(highs[i]), "time": int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i})
        if all(lows[i] <= lows[i - j] for j in range(1, lookback + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, lookback + 1)
        ):
            swing_lows.append({"index": i, "price": float(lows[i]), "time": int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i})
    return {"swing_highs": swing_highs, "swing_lows": swing_lows}


def _find_order_blocks(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Detect bullish and bearish order blocks (last opposing candle before a strong move)."""
    order_blocks = []
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    for i in range(2, len(df)):
        body = abs(closes[i] - opens[i])
        if body == 0:
            continue
        prev_body = abs(closes[i - 1] - opens[i - 1])
        if body > prev_body * 1.5:
            if closes[i] > opens[i]:
                ob_high = max(opens[i - 1], closes[i - 1])
                ob_low = min(opens[i - 1], closes[i - 1])
                order_blocks.append({"type": "bullish_ob", "high": float(ob_high), "low": float(ob_low), "mid": float((ob_high + ob_low) / 2), "index": i - 1})
            elif closes[i] < opens[i]:
                ob_high = max(opens[i - 1], closes[i - 1])
                ob_low = min(opens[i - 1], closes[i - 1])
                order_blocks.append({"type": "bearish_ob", "high": float(ob_high), "low": float(ob_low), "mid": float((ob_high + ob_low) / 2), "index": i - 1})
    return order_blocks[-5:]


def _find_fvgs(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Detect Fair Value Gaps (3-candle imbalance gaps)."""
    fvgs = []
    highs = df["high"].values
    lows = df["low"].values
    for i in range(2, len(df)):
        if lows[i] > highs[i - 2]:
            fvgs.append({"type": "bullish_fvg", "top": float(lows[i]), "bottom": float(highs[i - 2]), "mid": float((lows[i] + highs[i - 2]) / 2), "index": i})
        elif highs[i] < lows[i - 2]:
            fvgs.append({"type": "bearish_fvg", "top": float(lows[i - 2]), "bottom": float(highs[i]), "mid": float((lows[i - 2] + highs[i]) / 2), "index": i})
    return fvgs[-5:]


def _volume_profile(df: pd.DataFrame, bins: int = 15) -> dict[str, Any]:
    """Approximate Volume Profile: HVN, LVN, POC."""
    price_range = df["high"].max() - df["low"].min()
    if price_range <= 0:
        return {"poc": float(df["close"].iloc[-1]), "hvn": float(df["close"].iloc[-1]), "lvn": float(df["close"].iloc[-1])}
    bin_size = price_range / bins
    vol_at_price = {}
    for _, row in df.iterrows():
        mid = (row["high"] + row["low"]) / 2
        bucket = int((mid - df["low"].min()) / bin_size)
        bucket = min(bucket, bins - 1)
        vol_at_price[bucket] = vol_at_price.get(bucket, 0) + row["volume"]
    if not vol_at_price:
        return {"poc": float(df["close"].iloc[-1]), "hvn": float(df["close"].iloc[-1]), "lvn": float(df["close"].iloc[-1])}
    poc_bucket = max(vol_at_price, key=vol_at_price.get)
    sorted_bucks = sorted(vol_at_price.items(), key=lambda x: x[1], reverse=True)
    hvn_bucket = sorted_bucks[0][0]
    lvn_bucket = sorted_bucks[-1][0]
    min_p = float(df["low"].min())
    poc = min_p + (poc_bucket + 0.5) * bin_size
    hvn = min_p + (hvn_bucket + 0.5) * bin_size
    lvn = min_p + (lvn_bucket + 0.5) * bin_size
    return {"poc": round(poc, 4), "hvn": round(hvn, 4), "lvn": round(lvn, 4), "levels": {round(min_p + (k + 0.5) * bin_size, 4): round(v, 2) for k, v in vol_at_price.items()}}


def _detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 10) -> dict[str, Any]:
    """Detect if the most recent candles have swept a recent swing high or low."""
    if len(df) < lookback + 3:
        return {"swept": False, "direction": None}
    recent = df.tail(lookback)
    swing = _find_swing_highs_lows(df.iloc[:-lookback], lookback=3)
    current_low = float(recent["low"].min())
    current_high = float(recent["high"].max())
    for sl in swing.get("swing_lows", []):
        if current_low < sl["price"]:
            return {"swept": True, "direction": "swept_low", "level": sl["price"]}
    for sh in swing.get("swing_highs", []):
        if current_high > sh["price"]:
            return {"swept": True, "direction": "swept_high", "level": sh["price"]}
    return {"swept": False, "direction": None}


def _detect_market_structure_shift(df: pd.DataFrame) -> dict[str, Any]:
    """Detect break of structure (BOS) in the most recent candles."""
    if len(df) < 10:
        return {"shift": False, "direction": None}
    swing = _find_swing_highs_lows(df, lookback=3)
    shs = swing.get("swing_highs", [])
    sls = swing.get("swing_lows", [])
    current_close = float(df["close"].iloc[-1])
    if shs and current_close > shs[-1]["price"]:
        return {"shift": True, "direction": "bullish_bos", "level": shs[-1]["price"]}
    if sls and current_close < sls[-1]["price"]:
        return {"shift": True, "direction": "bearish_bos", "level": sls[-1]["price"]}
    return {"shift": False, "direction": None}

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

    async def get_active_strategies(self) -> list[StrategyConfig]:
        """Return all active strategies (supports running multiple strategies in parallel)."""
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(StrategyConfig).where(StrategyConfig.is_active == True)
            )
            strategies = list(res.scalars().all())
            if not strategies:
                res_all = await session.execute(select(StrategyConfig))
                all_strats = list(res_all.scalars().all())
                return [all_strats[0]] if all_strats else []
            return strategies

    @staticmethod
    def strategy_symbols(strategy: Optional[StrategyConfig]) -> list[str]:
        """Returns the list of target symbols configured for a strategy ([] = all watchlist)."""
        if not strategy or not getattr(strategy, "symbols", None):
            return []
        try:
            syms = json.loads(strategy.symbols)
            return [s.strip().upper().replace("/", "") for s in syms if isinstance(s, str) and s.strip()]
        except Exception:
            return []

    @staticmethod
    def strategy_applies_to(strategy: Optional[StrategyConfig], symbol: str, watchlist: list[str]) -> bool:
        """True if the strategy should evaluate/trade this symbol."""
        syms = StrategyEngine.strategy_symbols(strategy)
        if not syms:
            return symbol in watchlist
        return symbol.upper().replace("/", "") in syms

    async def evaluate_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategy: Optional[StrategyConfig] = None
    ) -> dict[str, Any]:
        if df.empty or len(df) < 30:
            return {"symbol": symbol, "action": "HOLD", "confidence": 0.0, "reason": "Insufficient historical candle data", "indicators": {}}

        indicators = analyze_all_indicators(df)
        if not indicators:
            return {"symbol": symbol, "action": "HOLD", "confidence": 0.0, "reason": "Indicator computation error", "indicators": {}}

        if strategy is None:
            strategy = await self.get_active_strategy()

        strat_name = strategy.name if strategy else "ai_adaptive_momentum"
        params = json.loads(strategy.parameters) if strategy and strategy.parameters else {}
        risk = json.loads(strategy.risk_settings) if strategy and strategy.risk_settings else {}
        sl_pct = float(risk.get("stop_loss_pct", 1.5))
        tp_pct = float(risk.get("take_profit_pct", 3.0))
        trailing_sl_pct = float(risk.get("trailing_stop_pct", 1.0))
        ai_memories = await self.get_learned_memories()

        if "ict" in strat_name or "market_mechanics" in strat_name:
            return self._evaluate_ict(symbol, df, indicators, params, sl_pct, tp_pct, trailing_sl_pct)

        if "momentum" in strat_name or "adaptive" in strat_name:
            return self._evaluate_adaptive_momentum(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct, ai_memories)
        elif "bollinger" in strat_name or "mean_reversion" in strat_name:
            return self._evaluate_bollinger_reversion(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct)
        elif "grid" in strat_name:
            return self._evaluate_grid_scalper(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct)

        return self._evaluate_adaptive_momentum(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct, ai_memories)

    def _evaluate_ict(
        self,
        symbol: str,
        df: pd.DataFrame,
        ind: dict[str, Any],
        params: dict[str, Any],
        sl_pct: float,
        tp_pct: float,
        trailing_sl_pct: float
    ) -> dict[str, Any]:
        """ICT / Market Mechanics Scalping Framework evaluation."""
        price = ind.get("price", 0.0)
        atr = ind.get("atr", 0.0)

        sw = _find_swing_highs_lows(df, lookback=3)
        swing_highs = sw.get("swing_highs", [])
        swing_lows = sw.get("swing_lows", [])

        if not swing_highs or not swing_lows:
            return {"symbol": symbol, "action": "HOLD", "confidence": 0.0, "reason": "Insufficient swing structure for ICT analysis", "indicators": ind, "ict": {}}

        recent_high = swing_highs[-1]["price"]
        recent_low = swing_lows[-1]["price"]
        eq = (recent_high + recent_low) / 2
        in_discount = price < eq
        in_premium = price > eq

        vp = _volume_profile(df)
        ob_list = _find_order_blocks(df)
        fvg_list = _find_fvgs(df)
        sweep = _detect_liquidity_sweep(df, lookback=8)
        mss = _detect_market_structure_shift(df)
        trend = ind.get("trend_status", "RANGING")

        poi = None
        direction = None
        for ob in reversed(ob_list):
            if "bullish" in ob["type"] and in_discount and price >= ob["low"] * 0.998 and price <= ob["high"] * 1.002:
                poi = ob
                direction = "BUY"
                break
            if "bearish" in ob["type"] and in_premium and price >= ob["low"] * 0.998 and price <= ob["high"] * 1.002:
                poi = ob
                direction = "SELL"
                break
        if not poi:
            for fvg in reversed(fvg_list):
                if "bullish" in fvg["type"] and in_discount and price >= fvg["bottom"] * 0.998 and price <= fvg["top"] * 1.002:
                    poi = fvg
                    direction = "BUY"
                    break
                if "bearish" in fvg["type"] and in_premium and price >= fvg["bottom"] * 0.998 and price <= fvg["top"] * 1.002:
                    poi = fvg
                    direction = "SELL"
                    break

        checks_passed = 0
        reasons = []

        if direction == "BUY":
            if in_discount:
                checks_passed += 1
                reasons.append("Price in discount zone")
            if sweep.get("swept") and sweep.get("direction") == "swept_low":
                checks_passed += 1
                reasons.append(f"Liquidity swept at ${sweep.get('level', 0):.4f}")
            if mss.get("shift") and mss.get("direction") == "bullish_bos":
                checks_passed += 1
                reasons.append("Bullish market structure shift confirmed")
        elif direction == "SELL":
            if in_premium:
                checks_passed += 1
                reasons.append("Price in premium zone")
            if sweep.get("swept") and sweep.get("direction") == "swept_high":
                checks_passed += 1
                reasons.append(f"Liquidity swept at ${sweep.get('level', 0):.4f}")
            if mss.get("shift") and mss.get("direction") == "bearish_bos":
                checks_passed += 1
                reasons.append("Bearish market structure shift confirmed")

        if poi:
            checks_passed += 1
            reasons.append(f"POI active: {poi.get('type', 'unknown')}")

        total_required = 4
        confidence = round(min(checks_passed / total_required, 1.0), 2)

        tp1_price = vp.get("hvn", price * (1 + tp_pct / 100.0 if direction == "BUY" else 1 - tp_pct / 100.0))
        risk_dist = abs(price - (recent_low if direction == "BUY" else recent_high))
        reward_dist = abs(tp1_price - price)
        rr_ratio = round(reward_dist / (risk_dist + 1e-10), 2) if risk_dist > 0 else 0.0

        ict_data = {
            "bias": "bullish" if direction == "BUY" else "bearish" if direction == "SELL" else "unclear",
            "swing_high": round(recent_high, 4),
            "swing_low": round(recent_low, 4),
            "equilibrium": round(eq, 4),
            "volume_profile": vp,
            "order_blocks": len(ob_list),
            "fvgs": len(fvg_list),
            "liquidity_sweep": sweep,
            "market_structure_shift": mss,
            "rr_to_tp1": rr_ratio,
            "checks_passed": checks_passed,
        }

        if direction and checks_passed >= 3 and poi and rr_ratio >= 2.0:
            sl_distance_pct = round((risk_dist / price) * 100, 2) if price > 0 else sl_pct
            tp_distance_pct = round((reward_dist / price) * 100, 2) if price > 0 else tp_pct
            entry_model = "FLIP_EM" if mss.get("shift") else "MS_EM"
            return {
                "symbol": symbol,
                "action": direction,
                "confidence": confidence,
                "reason": " & ".join(reasons),
                "stop_loss_pct": max(sl_distance_pct, sl_pct),
                "take_profit_pct": max(tp_distance_pct, tp_pct),
                "trailing_stop_pct": trailing_sl_pct,
                "indicators": ind,
                "ict": ict_data,
                "entry_model": entry_model
            }

        return {
            "symbol": symbol,
            "action": "HOLD",
            "confidence": confidence * 0.5,
            "reason": f"ICT A+ checklist: {checks_passed}/{total_required} passed | RR: {rr_ratio} | {' & '.join(reasons) if reasons else 'No POI or structure'}",
            "indicators": ind,
            "ict": ict_data
        }

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
