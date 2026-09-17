import asyncio
import datetime
import json
import logging
import time
from typing import Any, Optional
import pandas as pd
from sqlalchemy import select
from app.config import settings
from app.database import AsyncSessionLocal, StrategyConfig, StrategyMemory, Trade
from app.indicators import analyze_all_indicators, calculate_atr, calculate_ema
from app.binance_client import binance_client
from app.ai_agent import ai_agent

logger = logging.getLogger("strategy_engine")

_TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


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
            ob_time = int(df["timestamp"].iloc[i - 1]) if "timestamp" in df.columns else i - 1
            if closes[i] > opens[i]:
                ob_high = max(opens[i - 1], closes[i - 1])
                ob_low = min(opens[i - 1], closes[i - 1])
                order_blocks.append({"type": "bullish_ob", "high": float(ob_high), "low": float(ob_low), "mid": float((ob_high + ob_low) / 2), "index": i - 1, "time": ob_time})
            elif closes[i] < opens[i]:
                ob_high = max(opens[i - 1], closes[i - 1])
                ob_low = min(opens[i - 1], closes[i - 1])
                order_blocks.append({"type": "bearish_ob", "high": float(ob_high), "low": float(ob_low), "mid": float((ob_high + ob_low) / 2), "index": i - 1, "time": ob_time})
    return order_blocks[-5:]


def _find_fvgs(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Detect Fair Value Gaps (3-candle imbalance gaps)."""
    fvgs = []
    highs = df["high"].values
    lows = df["low"].values
    for i in range(2, len(df)):
        fvg_time = int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i
        if lows[i] > highs[i - 2]:
            fvgs.append({"type": "bullish_fvg", "top": float(lows[i]), "bottom": float(highs[i - 2]), "mid": float((lows[i] + highs[i - 2]) / 2), "index": i, "time": fvg_time})
        elif highs[i] < lows[i - 2]:
            fvgs.append({"type": "bearish_fvg", "top": float(lows[i - 2]), "bottom": float(highs[i]), "mid": float((lows[i - 2] + highs[i]) / 2), "index": i, "time": fvg_time})
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

    async def _setup_already_traded(self, setup_id: str) -> bool:
        """Persisted duplicate-setup check — survives bot cycles and restarts.
        Only considers trades that actually represent an executed or rejected setup
        (OPEN, CLOSED, CANCELLED), not preliminary attempts.
        """
        if not setup_id:
            return False
        try:
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(Trade.id).where(
                        Trade.setup_id == setup_id,
                        Trade.status.in_(["OPEN", "CLOSED", "CANCELLED"])
                    ).limit(1)
                )
                return res.scalar_one_or_none() is not None
        except Exception as e:
            # If the duplicate state cannot be verified (database unavailable),
            # fail closed: treat as duplicate so no untracked trade is opened.
            logger.error(f"Duplicate-setup check failed for {setup_id}: {e}")
            return True

    @staticmethod
    def _is_stale(df: pd.DataFrame, timeframe: str) -> bool:
        """True when the newest candle is too old relative to its interval."""
        if df is None or df.empty or "timestamp" not in df.columns:
            return True
        interval = _TIMEFRAME_SECONDS.get(timeframe.lower(), 900)
        last_ts = int(df["timestamp"].iloc[-1]) / 1000.0
        max_age = interval * settings.MARKET_DATA_MAX_AGE_INTERVALS
        return (time.time() - last_ts) > max_age

    async def _fetch_multi_tf_data(self, symbol: str) -> dict[str, pd.DataFrame]:
        """Fetch 4H, 1H, 15M, 5M data for full ICT analysis concurrently."""
        timeframes = ["4h", "1h", "15m", "5m"]
        tasks = [binance_client.fetch_klines(symbol, timeframe=tf, limit=100) for tf in timeframes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        data = {}
        for tf, res in zip(timeframes, results):
            if isinstance(res, pd.DataFrame) and not res.empty:
                data[tf] = res
            else:
                logger.warning(f"_fetch_multi_tf_data {symbol} {tf}: fetch failed or empty")
        return data

    def _analyze_4h_macro(self, df_4h: Optional[pd.DataFrame]) -> dict[str, Any]:
        """Phase 0: 4H Macro sanity check (confirm trade is not fighting macro trend)."""
        if df_4h is None or len(df_4h) < 10:
            return {"bias": "neutral", "reason": "Insufficient 4H candle data"}
        ema20 = calculate_ema(df_4h['close'], 20).iloc[-1]
        ema50 = calculate_ema(df_4h['close'], 50).iloc[-1]
        close = df_4h['close'].iloc[-1]
        if close > ema20 >= ema50:
            bias = "bullish"
            reason = "4H price above EMA 20/50 uptrend"
        elif close < ema20 <= ema50:
            bias = "bearish"
            reason = "4H price below EMA 20/50 downtrend"
        elif close > ema50:
            bias = "bullish"
            reason = "4H price above EMA 50"
        elif close < ema50:
            bias = "bearish"
            reason = "4H price below EMA 50"
        else:
            bias = "neutral"
            reason = "4H macro ranging"
        return {
            "bias": bias,
            "ema20": float(ema20),
            "ema50": float(ema50),
            "price": float(close),
            "reason": reason
        }

    def _analyze_1h_bias(self, df_1h: pd.DataFrame) -> dict[str, Any]:
        """Phase 1: 1H HTF bias + structure. Swing High/Low, 0.5 Equilibrium (EQ), Volume Profile."""
        if df_1h is None or len(df_1h) < 15:
            return {"bias": "unclear", "reason": "Insufficient 1H candle data"}

        swings = _find_swing_highs_lows(df_1h, lookback=3)
        swing_highs = swings.get("swing_highs", [])
        swing_lows = swings.get("swing_lows", [])

        swing_high = float(swing_highs[-1]["price"]) if swing_highs else float(df_1h["high"].max())
        swing_low = float(swing_lows[-1]["price"]) if swing_lows else float(df_1h["low"].min())
        eq = (swing_high + swing_low) / 2.0
        vp = _volume_profile(df_1h, bins=15)
        price = float(df_1h["close"].iloc[-1])

        ema20 = calculate_ema(df_1h['close'], 20).iloc[-1]
        ema50 = calculate_ema(df_1h['close'], 50).iloc[-1]

        if price >= eq and price >= ema20:
            bias = "bullish"
            discount_premium = "premium" if price > eq else "discount"
            invalid_at = swing_low
            reason = f"1H Bullish: price above EQ ({eq:.2f}) & EMA20 ({ema20:.2f}), invalid below {invalid_at:.2f}"
        elif price <= eq and price <= ema20:
            bias = "bearish"
            discount_premium = "discount" if price < eq else "premium"
            invalid_at = swing_high
            reason = f"1H Bearish: price below EQ ({eq:.2f}) & EMA20 ({ema20:.2f}), invalid above {invalid_at:.2f}"
        elif price > ema20 >= ema50:
            bias = "bullish"
            discount_premium = "discount" if price < eq else "premium"
            invalid_at = swing_low
            reason = f"1H Bullish EMA structure, invalid below {invalid_at:.2f}"
        elif price < ema20 <= ema50:
            bias = "bearish"
            discount_premium = "premium" if price > eq else "discount"
            invalid_at = swing_high
            reason = f"1H Bearish EMA structure, invalid above {invalid_at:.2f}"
        elif price >= eq:
            bias = "bullish"
            discount_premium = "premium"
            invalid_at = swing_low
            reason = f"1H Bullish structure above EQ ({eq:.2f}), invalid below {invalid_at:.2f}"
        else:
            bias = "bearish"
            discount_premium = "discount"
            invalid_at = swing_high
            reason = f"1H Bearish structure below EQ ({eq:.2f}), invalid above {invalid_at:.2f}"

        return {
            "bias": bias,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "eq": eq,
            "invalid_at": invalid_at,
            "discount_premium": discount_premium,
            "volume_profile": vp,
            "reason": reason
        }

    def _analyze_15m_poi(self, df_15m: pd.DataFrame, bias_1h: dict[str, Any]) -> dict[str, Any]:
        """Phase 2: 15M Target Zone: ONE clean unmitigated POI (OB or FVG) + Volume Profile confluence."""
        if df_15m is None or len(df_15m) < 15:
            return {"poi": None, "reason": "Insufficient 15M data"}

        obs = _find_order_blocks(df_15m)
        fvgs = _find_fvgs(df_15m)
        vp = _volume_profile(df_15m, bins=15)
        price = float(df_15m["close"].iloc[-1])
        bias_dir = bias_1h.get("bias", "bullish")

        selected_poi = None
        if bias_dir == "bullish":
            # Prefer bullish OB or FVG below or near current price
            bull_obs = [ob for ob in obs if ob.get("type") == "bullish_ob"]
            bull_fvgs = [fvg for fvg in fvgs if fvg.get("type") == "bullish_fvg"]
            if bull_obs:
                selected_poi = bull_obs[-1]
            elif bull_fvgs:
                selected_poi = bull_fvgs[-1]
            else:
                low_p = float(df_15m["low"].tail(10).min())
                selected_poi = {"type": "bullish_structure_zone", "high": low_p * 1.005, "low": low_p, "mid": low_p * 1.0025}
        else:
            # Prefer bearish OB or FVG above or near current price
            bear_obs = [ob for ob in obs if ob.get("type") == "bearish_ob"]
            bear_fvgs = [fvg for fvg in fvgs if fvg.get("type") == "bearish_fvg"]
            if bear_obs:
                selected_poi = bear_obs[-1]
            elif bear_fvgs:
                selected_poi = bear_fvgs[-1]
            else:
                high_p = float(df_15m["high"].tail(10).max())
                selected_poi = {"type": "bearish_structure_zone", "high": high_p, "low": high_p * 0.995, "mid": high_p * 0.9975}

        return {
            "poi": selected_poi,
            "volume_profile": vp,
            "reason": f"15M POI selected: {selected_poi.get('type') if selected_poi else 'None'}"
        }

    def _analyze_5m_entry(self, df_5m: Optional[pd.DataFrame], bias_1h: dict[str, Any], poi_15m: dict[str, Any]) -> dict[str, Any]:
        """Phase 4 & 5: 5M Entry conditions (fractal structure, liquidity sweep, market-structure shift).

        Entry confirmation MUST be derived from objective 5M evidence (directional liquidity
        sweep, directional MSS/BOS, or a bias-direction reversal candle close). Missing data
        or the absence of any objective signal confirms NOTHING — fail closed.
        """
        if df_5m is None or len(df_5m) < 10:
            return {
                "confirmed": False,
                "reason": "MISSING_DATA: insufficient 5M candle data for entry confirmation",
                "mss": {},
                "liquidity": {},
                "confirmation_time": None
            }

        sweep = _detect_liquidity_sweep(df_5m, lookback=8)
        mss = _detect_market_structure_shift(df_5m)
        bias_dir = bias_1h.get("bias", "bullish")

        confirmed = False
        reasons = []

        if bias_dir == "bullish":
            if sweep.get("swept") and sweep.get("direction") == "swept_low":
                reasons.append("5M Liquidity low swept")
                confirmed = True
            if mss.get("shift") and mss.get("direction") == "bullish_bos":
                reasons.append("5M Bullish Market Structure Shift (BOS)")
                confirmed = True
            if not confirmed and df_5m["close"].iloc[-1] > df_5m["open"].iloc[-1]:
                reasons.append("5M Bullish candle close at zone")
                confirmed = True
        else:
            if sweep.get("swept") and sweep.get("direction") == "swept_high":
                reasons.append("5M Liquidity high swept")
                confirmed = True
            if mss.get("shift") and mss.get("direction") == "bearish_bos":
                reasons.append("5M Bearish Market Structure Shift (BOS)")
                confirmed = True
            if not confirmed and df_5m["close"].iloc[-1] < df_5m["open"].iloc[-1]:
                reasons.append("5M Bearish candle close at zone")
                confirmed = True

        if not confirmed:
            reasons.append("NO_ENTRY_SIGNAL: no directional sweep, MSS/BOS or reversal candle close on 5M")

        last_candle_time = int(df_5m["timestamp"].iloc[-1]) if "timestamp" in df_5m.columns else None

        return {
            "confirmed": confirmed,
            "reason": " | ".join(reasons),
            "mss": mss,
            "liquidity": sweep,
            "confirmation_time": last_candle_time
        }

    def _check_a_plus(
        self,
        bias_1h: dict[str, Any],
        poi_15m: dict[str, Any],
        entry_5m: dict[str, Any],
        ind_15m: dict[str, Any],
        params: dict[str, Any],
        structural_rr: Optional[float] = None,
        confirmation_time: Optional[int] = None
    ) -> dict[str, Any]:
        """Phase 5: Strict A+ checklist verification.

        Every condition is derived from actual available market/setup data — never
        assumed. Each condition records: name, boolean result, evidence, timeframe,
        relevant timestamp and failure reason. If required data is missing the
        condition is FALSE (fail closed).

        The five mandatory conditions follow the strategy rule book:
          c1_bias   — setup agrees with 1H bias
          c2_poi    — one clean unmitigated POI (OB or FVG)
          c3_entry  — objective 5M entry confirmation (sweep / MSS / reversal close)
          c4_time   — setup valid at current time (timestamp of the confirming 5M
                      candle is known; no timezone/session restriction applies)
          c5_rr     — minimum 2R available to TP1
        """
        min_rr = float(params.get("min_rr_to_tp1", 2.0))

        # c1 — 1H bias
        bias_val = bias_1h.get("bias")
        c1 = bias_val in ("bullish", "bearish")
        conditions = [{
            "name": "c1_bias",
            "result": c1,
            "evidence": f"1H bias={bias_val}; {bias_1h.get('reason', '')}",
            "timeframe": "1h",
            "timestamp": bias_1h.get("timestamp"),
            "failure_reason": None if c1 else f"1H bias unclear/missing (got '{bias_val}')"
        }]

        # c2 — POI present. A synthesized structure zone is NOT a clean OB/FVG —
        # only detected order blocks / FVGs satisfy the condition.
        poi = poi_15m.get("poi") or {}
        poi_type = poi.get("type", "")
        c2 = bool(poi) and poi_type in ("bullish_ob", "bearish_ob", "bullish_fvg", "bearish_fvg")
        conditions.append({
            "name": "c2_poi",
            "result": c2,
            "evidence": f"POI type={poi_type or 'None'} high={poi.get('high')} low={poi.get('low')} time={poi.get('time')}",
            "timeframe": "15m",
            "timestamp": poi.get("time"),
            "failure_reason": None if c2 else f"No clean unmitigated OB/FVG POI (got '{poi_type or 'None'}')"
        })

        # c3 — objective 5M entry confirmation
        c3 = bool(entry_5m.get("confirmed", False))
        conditions.append({
            "name": "c3_entry",
            "result": c3,
            "evidence": entry_5m.get("reason", ""),
            "timeframe": "5m",
            "timestamp": entry_5m.get("confirmation_time"),
            "failure_reason": None if c3 else entry_5m.get("reason", "No objective 5M entry confirmation")
        })

        # c4 — setup valid at current time. Derived from the confirming 5M candle
        # timestamp being available (there is deliberately NO session/timezone
        # restriction per the rule book, but the timestamp must be real data).
        c4 = confirmation_time is not None
        conditions.append({
            "name": "c4_time",
            "result": c4,
            "evidence": f"Confirmation candle timestamp={confirmation_time} (no session restriction)",
            "timeframe": "5m",
            "timestamp": confirmation_time,
            "failure_reason": None if c4 else "Confirmation candle timestamp missing — setup time cannot be established"
        })

        # c5 — minimum 2R available to TP1
        c5 = structural_rr is not None and structural_rr >= min_rr
        conditions.append({
            "name": "c5_rr",
            "result": c5,
            "evidence": f"structural_rr={structural_rr}, min required={min_rr}",
            "timeframe": "15m",
            "timestamp": None,
            "failure_reason": None if c5 else f"Structural RR {structural_rr} below minimum {min_rr}"
        })

        checks = {c["name"]: c["result"] for c in conditions}
        passed = sum(1 for v in checks.values() if v)
        all_pass = all(checks.values())
        return {
            "all_pass": all_pass,
            "checks": checks,
            "conditions": conditions,
            "passed": passed,
            "total": len(checks)
        }

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
            return await self._evaluate_ict_full(symbol, strategy, params, sl_pct, tp_pct, trailing_sl_pct)

        if "momentum" in strat_name or "adaptive" in strat_name:
            return self._evaluate_adaptive_momentum(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct, ai_memories)
        elif "bollinger" in strat_name or "mean_reversion" in strat_name:
            return self._evaluate_bollinger_reversion(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct)
        elif "grid" in strat_name:
            return self._evaluate_grid_scalper(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct)

        return self._evaluate_adaptive_momentum(symbol, indicators, params, sl_pct, tp_pct, trailing_sl_pct, ai_memories)

    async def _evaluate_ict_full(
        self,
        symbol: str,
        strategy: Optional[StrategyConfig],
        params: dict[str, Any],
        sl_pct: float,
        tp_pct: float,
        trailing_sl_pct: float
    ) -> dict[str, Any]:
        """ICT / Market Mechanics Scalping Framework — full multi-timeframe evaluation.

        Flow: 4H macro sanity → 1H bias+structure → 15M POI (OB/FVG) → 5M A+ checklist → SL/TP with
        minimum 2R to TP1. Matches the user's rule book exactly. Uses data from all 4 timeframes.
        """
        try:
            # Fetch all timeframes: 4H, 1H, 15M, 5M
            data = await self._fetch_multi_tf_data(symbol)
        except Exception as e:
            logger.error(f"ICT: failed to fetch multi-TF data for {symbol}: {e}")
            return {"symbol": symbol, "action": "HOLD", "confidence": 0.0, "reject_reason": "MISSING_DATA", "reason": f"ICT data fetch error: {e}", "indicators": {}, "ict": {}}

        # Synthetic/fallback candles must never be mistaken for real market data and
        # must never create a trading opportunity (fail closed unless explicitly allowed).
        synthetic_tfs = [tf for tf, df in data.items() if df.attrs.get("synthetic")]
        if synthetic_tfs and not settings.ALLOW_SYNTHETIC_DATA_TRADING:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "SYNTHETIC_DATA",
                "reason": f"ICT NO TRADE: synthetic/fallback data on timeframes {synthetic_tfs}",
                "indicators": {},
                "ict": {"page": "PH0_DATA"}
            }

        # Stale execution-timeframe data → no trade
        if self._is_stale(data.get("15m"), "15m"):
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "STALE_DATA",
                "reason": "ICT NO TRADE: 15M candle data is stale",
                "indicators": {},
                "ict": {"page": "PH0_DATA"}
            }

        df_4h = data.get("4h")
        df_1h = data.get("1h")
        df_15m = data.get("15m")
        df_5m = data.get("5m")

        if df_15m is None or df_1h is None or len(df_15m) < 30 or len(df_1h) < 30:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "MISSING_DATA",
                "reason": "ICT: insufficient multi-timeframe data",
                "indicators": (analyze_all_indicators(df_15m) if df_15m is not None else {}),
                "ict": {}
            }

        ind_15m = analyze_all_indicators(df_15m)
        price = ind_15m.get("price", 0.0)

        # Phase 0 / 4H macro sanity check
        macro = self._analyze_4h_macro(df_4h) if df_4h is not None else {"bias": "UNKNOWN"}

        # Phase 1 — 1H HTF bias + structure
        bias_1h = self._analyze_1h_bias(df_1h)

        # If 1H bias is unclear → NO TRADE
        if bias_1h.get("bias") == "unclear":
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "NO_1H_BIAS",
                "reason": f"ICT NO TRADE: 1H bias unclear | {bias_1h.get('reason')} | 4H: {macro.get('bias')}",
                "indicators": ind_15m,
                "ict": {"bias_1h": bias_1h, "macro_4h": macro, "page": "PH1_BIAS"}
            }

        # If 4H macro conflicts strongly with 1H bias → NO TRADE
        macro_bias = macro.get("bias", "neutral")
        bias_dir = bias_1h.get("bias")
        if macro_bias in ("bullish", "bearish") and macro_bias != bias_dir:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "NO_4H_ALIGNMENT",
                "reason": f"ICT NO TRADE: 4H macro ({macro_bias}) conflicts with 1H bias ({bias_dir})",
                "indicators": ind_15m,
                "ict": {"bias_1h": bias_1h, "macro_4h": macro, "page": "PH1_BIAS"}
            }

        # Phase 2 — 15M Target Zone: ONE clean unmitigated POI (OB or FVG)
        poi_15m = self._analyze_15m_poi(df_15m, bias_1h)
        if not poi_15m.get("poi"):
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "NO_VALID_POI",
                "reason": f"ICT NO TRADE: {poi_15m.get('reason')} | 1H bias: {bias_dir}",
                "indicators": ind_15m,
                "ict": {"bias_1h": bias_1h, "macro_4h": macro, "poi": poi_15m, "page": "PH2_POI"}
            }

        # Phase 4/5 — 5M entry conditions (liquidity sweep + MSS) then A+ checklist
        entry_5m = self._analyze_5m_entry(df_5m, bias_1h, poi_15m) if df_5m is not None else {
            "confirmed": False, "reason": "MISSING_DATA: no 5M candle data", "mss": {}, "liquidity": {}, "confirmation_time": None
        }

        # Compute SL/TP prices from the strategy's own invalidation/target logic
        if bias_dir == "bullish":
            raw_sl = min(poi_15m.get("poi", {}).get("low", price * 0.985), bias_1h.get("swing_low", price * 0.985))
            stop_loss = raw_sl if 0 < raw_sl < price else price * (1 - sl_pct / 100.0)
            risk_dist = price - stop_loss
            raw_tp = poi_15m.get("volume_profile", {}).get("hvn", price * (1 + tp_pct / 100.0))
            # TP = strategy target (HVN when it is a valid target above entry, otherwise
            # the strategy's default %-based TP). TP is NEVER pushed farther away merely
            # to raise RR — setups below the 2R minimum are rejected instead (RR_BELOW_2).
            tp1_price = raw_tp if raw_tp > price else price * (1 + tp_pct / 100.0)
            geometry_valid = stop_loss < price < tp1_price
        else:
            raw_sl = max(poi_15m.get("poi", {}).get("high", price * 1.015), bias_1h.get("swing_high", price * 1.015))
            stop_loss = raw_sl if raw_sl > price else price * (1 + sl_pct / 100.0)
            risk_dist = stop_loss - price
            raw_tp = poi_15m.get("volume_profile", {}).get("hvn", price * (1 - tp_pct / 100.0))
            tp1_price = raw_tp if raw_tp < price else price * (1 - tp_pct / 100.0)
            geometry_valid = tp1_price < price < stop_loss

        # Hard SL/TP geometry validation: LONG SL < entry < TP, SHORT TP < entry < SL
        if not geometry_valid or risk_dist <= 0:
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "INVALID_SLTP",
                "reason": f"ICT NO TRADE: invalid SL/TP geometry (entry={price:.4f}, SL={stop_loss:.4f}, TP={tp1_price:.4f})",
                "indicators": ind_15m,
                "ict": {"page": "PH5_SLTP", "macro_4h": macro, "bias_1h": bias_1h, "poi_15m": poi_15m, "entry_5m": entry_5m}
            }

        reward_dist = (tp1_price - price) if bias_dir == "bullish" else (price - tp1_price)
        structural_rr = round(reward_dist / risk_dist, 2)

        # A+ checklist — every condition derived from real data, including RR (c5)
        a_plus = self._check_a_plus(
            bias_1h, poi_15m, entry_5m, ind_15m, params,
            structural_rr=structural_rr,
            confirmation_time=entry_5m.get("confirmation_time")
        )

        ict_state = {
            "page": "PH5_A_PLUS",
            "macro_4h": macro,
            "bias_1h": bias_1h,
            "poi_15m": poi_15m,
            "entry_5m": entry_5m,
            "a_plus": a_plus,
            "rr_to_tp1": structural_rr,
            "stop_loss": round(stop_loss, 4),
            "tp1": round(tp1_price, 4),
            "structural_rr": structural_rr,
        }

        # NO TRADE if RR below the 2R minimum or the A+ checklist fails
        min_rr = float(params.get("min_rr_to_tp1", settings.MIN_EXECUTABLE_RR))
        if structural_rr < min_rr or not a_plus.get("all_pass"):
            if structural_rr < min_rr:
                reject_reason = "RR_BELOW_2"
                failed = [k for k, v in a_plus.get("checks", {}).items() if not v]
                detail = f"RR {structural_rr} < {min_rr}" + (f" | A+ failed: {', '.join(failed)}" if failed else "")
            else:
                reject_reason = "A_PLUS_FAILED"
                failed = [k for k, v in a_plus.get("checks", {}).items() if not v]
                detail = f"A+ failed: {', '.join(failed)}"
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": reject_reason,
                "reason": f"ICT NO TRADE: {detail} | 1H bias: {bias_dir} | POI: {poi_15m.get('poi', {}).get('type', '?')}",
                "indicators": ind_15m,
                "ict": ict_state
            }

        # Valid setup → compute trade details
        sl_distance_pct = round((risk_dist / price) * 100, 2) if price > 0 else sl_pct
        tp_distance_pct = round((reward_dist / price) * 100, 2) if price > 0 else tp_pct
        entry_model = "FLIP_EM" if entry_5m.get("confirmed") and entry_5m.get("mss", {}).get("shift") else "MS_EM"

        # Deterministic setup identity: symbol + strategy + direction + POI timestamp +
        # POI type + POI high + POI low + entry confirmation timestamp. Deliberately
        # excludes live price so the ID is stable across 15s bot cycles.
        poi = poi_15m.get("poi", {})
        confirmation_time = entry_5m.get("confirmation_time")
        setup_id = "|".join(str(x) for x in [
            symbol,
            strategy.name if strategy else "unknown",
            bias_dir,
            poi.get("type", "unknown"),
            int(poi.get("time", 0) or 0),
            round(float(poi.get("high", 0.0)), 8),
            round(float(poi.get("low", 0.0)), 8),
            int(confirmation_time) if confirmation_time is not None else 0,
        ])

        # Duplicate setup protection (persisted, survives restarts). Checked BEFORE the
        # LLM call so duplicates never burn Groq rate limit.
        if await self._setup_already_traded(setup_id):
            return {
                "symbol": symbol,
                "action": "HOLD",
                "confidence": 0.0,
                "reject_reason": "DUPLICATE_SETUP",
                "reason": f"ICT NO TRADE: duplicate setup {setup_id} already traded",
                "indicators": ind_15m,
                "ict": {**ict_state, "setup_id": setup_id}
            }

        # Prepare market state snapshot for LLM and audit
        market_state_snapshot = {
            "4h": macro,
            "1h": bias_1h,
            "15m": poi_15m,
            "5m": entry_5m,
            "indicators_15m": ind_15m,
            "a_plus": a_plus,
            "structural_rr": structural_rr,
            "estimated_entry": price,
            "estimated_sl": round(stop_loss, 4),
            "estimated_tp": round(tp1_price, 4),
            "strategy_version": strategy.version if strategy else 1,
            "prompt_version": getattr(strategy, "llm_prompt_version", "v1") if strategy else "v1",
            "llm_model": getattr(strategy, "llm_model", None) or settings.GROQ_MODEL,
        }

        # Query Groq AI LLM for ICT setup validation (validator only — it cannot modify
        # entry/SL/TP/size/risk; its FAIL/WAIT blocks the trade in bot_runner)
        llm_val = await ai_agent.evaluate_ict_setup_with_llm(symbol, ict_state)
        ict_state["llm_reasoning"] = llm_val.get("llm_reasoning", "")
        ict_state["llm_model"] = llm_val.get("model", "")
        ict_state["llm_decision"] = llm_val.get("decision", "FAIL")
        ict_state["llm_confidence"] = llm_val.get("confidence", 0.0)
        ict_state["llm_concerns"] = llm_val.get("concerns", [])
        ai_comment = f" | AI Reasoning: {llm_val.get('llm_reasoning', '')}" if llm_val.get("llm_reasoning") else ""

        return {
            "symbol": symbol,
            "action": "BUY" if bias_dir == "bullish" else "SELL",
            "confidence": 1.0,
            "reason": (
                f"ICT SETUP CONFIRMED: 4H macro={macro.get('bias')} | 1H bias={bias_dir} (invalid below/above {bias_1h.get('invalid_at')}) | "
                f"POI={poi_15m.get('poi', {}).get('type')} | {entry_5m.get('reason')} | A+ {a_plus.get('passed')}/{a_plus.get('total')} | RR={structural_rr}{ai_comment}"
            ),
            "stop_loss_pct": max(sl_distance_pct, sl_pct),
            "take_profit_pct": max(tp_distance_pct, tp_pct),
            "trailing_stop_pct": trailing_sl_pct,
            "indicators": ind_15m,
            "ict": ict_state,
            "entry_model": entry_model,
            "stop_loss_price": round(stop_loss, 4),
            "take_profit_price": round(tp1_price, 4),
            "rr_ratio": structural_rr,
            # RR tracked separately: structural (planned) vs executable (post-fill)
            "structural_rr": structural_rr,
            "estimated_executable_rr": structural_rr,  # Pre-fill estimate; refined after fill
            "setup_id": setup_id,
            "market_state_snapshot": market_state_snapshot,
            "confirmation_candle_timestamp": datetime.datetime.now(datetime.timezone.utc),
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
