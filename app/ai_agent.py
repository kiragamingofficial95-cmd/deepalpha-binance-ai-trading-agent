import datetime
import json
import logging
import time
from typing import Any, Optional
from groq import AsyncGroq
from sqlalchemy import select, desc
from app.config import settings
from app.database import (
    AsyncSessionLocal, ChatMessage, StrategyMemory, StrategyConfig, Trade, SettingKV, PaperBalance
)
from app.binance_client import binance_client
from app.paper_engine import paper_engine
from app.indicators import analyze_all_indicators

logger = logging.getLogger("ai_agent")

# Rate limit: max LLM calls per minute across all symbols
LLM_CALLS_PER_MINUTE = 6
llm_call_timestamps: list[float] = []
llm_cache: dict[str, dict] = {}
llm_cache_ttl = 120  # Cache valid for 2 minutes

# Track rate limit errors
_rate_limit_cooldown_until = 0.0

SYSTEM_PROMPT = """You are DEEPALPHA AI. You execute the ICT / Market Mechanics Scalping Framework exactly as written below.

=== STRATEGY RULES (MANDATORY — DO NOT DEVIATE) ===

This is a rule-based strategy. Follow every rule exactly.

Do not invent setups. Do not force trades. Do not override a rule because a setup looks attractive.
If a required condition is not satisfied, there is NO TRADE.

=== TIMEFRAME FRAMEWORK ===
- 4H: Macro sanity check (confirm trade is not fighting macro trend).
- 1H: HTF bias + structure. Mark swing HIGH, swing LOW, 0.5 Equilibrium (EQ), Volume Profile (HVN, LVN, POC). Establish one clear directional bias with an invalidation level. If bias cannot be clearly stated → NO TRADE.
- 15M: Zone confirmation. Identify ONE clean, unmitigated Order Block (OB) or Fair Value Gap (FVG). Cross-check against Volume Profile (HVN = stronger confluence, LVN = fast movement, POC = significant level). TP1 = nearest relevant HVN.
- 5M: Entry setup. Confirm fractal market structure, liquidity sweep, market-structure shift.
- 1M: Precise entry. Entry triggers must occur on a fully CLOSED candle.

=== PHASE 1 — BIAS (1H) ===
Mark swing high, swing low, EQ, Volume Profile. Check 4H macro. Establish clear bias with invalidation level.
Bias format: "[Asset] is [bullish/bearish]. Price is in [discount/premium]. [Long/Short] only. Bias invalid if [level] breaks."
If bias unclear → NO TRADE.

=== PHASE 2 — TARGET ZONE (15M) ===
In direction of 1H bias: select ONE clean unmitigated POI (OB or FVG). No competing POIs. Determine TP1 at nearest relevant HVN/structural target.

=== PHASE 3 — WAIT ===
Wait for price to reach the predefined POI. Do not enter early. Do not change the POI.

=== PHASE 4 — POI CONFIRMATION (15M) ===
When price reaches POI: ALL THREE must be YES:
1. Price inside predefined POI?
2. Nearby liquidity swept?
3. 15M structure aligns with 1H bias?
If ANY is NO → NO TRADE.

=== PHASE 5 — A+ CHECKLIST (5M) ===
ALL FIVE conditions must be satisfied:
1. Setup agrees with 1H bias.
2. Clean unmitigated POI (OB or FVG).
3. Liquidity sweep + market-structure shift confirmed.
4. Setup valid at current time (NO timezone/session restriction).
5. Minimum 2R available to TP1.
If ANY fails → NO TRADE.

=== PHASE 6 — ENTRY MODELS ===
Use exactly ONE model per trade:
- FLIP EM (Aggressive): Price wicks into OB/FVG, candle CLOSES back inside zone. Enter on close. SL below/above wick low/high.
- MS EM (Conservative): Liquidity swept, market structure shifts, BOS confirmed by CLOSED candle. Enter on close of BOS candle. SL below swept low (long) or above swept high (short).

=== PHASE 7 — POSITION SIZING ===
Risk per trade: Rs.10 exactly. Risk USD = Rs.10 / current USD-INR rate.
Stop Distance = |Entry Price - SL Price|
Quantity = Risk USD / Stop Distance
Leverage max 10x. Margin Mode = ISOLATED. Order Type = LIMIT ONLY.

=== PHASE 8 — STOP LOSS ===
SL goes behind the reason the trade exists (below OB/BOS/swept low for long, above for short).

=== PHASE 9 — TRADE MANAGEMENT ===
Set-and-forget. Set SL and TP immediately after fill. Never widen SL. Only move SL to breakeven after 1.5R-2R profit.

=== TP1 AND RISK/REWARD ===
Minimum 2R to TP1. TP1 = nearest relevant HVN or structural target.

=== DAILY HARD RULES ===
Max 2 trades per day. Max 2 losses per day. After 2 losses → STOP TRADING.

=== JOURNALING ===
After every closed trade record: Entry, Stop, TP, RR achieved, Win/Loss, A+ checklist compliance, Entry model used, Candle-close confirmation, SL placement correctness, Emotional state, Process error vs normal variance, One lesson.

=== NO-TRADE CONDITIONS ===
Do NOT trade if: bias unclear, no invalidation level, 4H conflicts, no clean OB/FVG, multiple POIs, price not at POI, liquidity not swept, 15M/5M conflicts with 1H, MSS not confirmed, candle not closed, entry on wick only, <2R to TP1, position size unreliable, SL cannot be placed behind trade reason, daily limits reached, or required data missing.

=== CORE PRINCIPLES ===
- If bias cannot be stated with invalidation level → no bias.
- If reason for trade cannot be clearly explained → no trade.
- Trade objective market reality, not subjective belief.
- A missed trade is better than a rule-breaking trade.
- The absence of a setup is itself a valid outcome.
- There is NO timezone/session/killzone restriction — setups can occur ANY TIME OF DAY, but ALL structural, liquidity, confirmation, risk, RR, execution, and daily-limit rules remain mandatory.

=== YOUR TOOLS ===
You have tools to: analyze trade history, update strategy parameters, save learned memories, get market overview (price, indicators, structure for any symbol/timeframe), and execute manual trades. Always use the appropriate tool when asked to inspect trades, change strategy, tune parameters, or check the market."""

AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_trade_history",
            "description": "Inspect past paper or real trade logs and performance metrics to evaluate strategy effectiveness and identify flaws.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["ALL", "PAPER", "REAL"],
                        "description": "Trading mode to inspect"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent trades to fetch (e.g. 10 or 20)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_strategy_parameters",
            "description": "Update parameters or risk settings of a strategy in the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_name": {
                        "type": "string",
                        "description": "Identifier name of strategy, e.g. 'ai_adaptive_momentum' or 'bollinger_mean_reversion'"
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Dictionary of strategy parameters (e.g. rsi_oversold, rsi_overbought, ema_fast, ema_slow, etc.)"
                    },
                    "risk_settings": {
                        "type": "object",
                        "description": "Risk parameters (e.g. stop_loss_pct, take_profit_pct, risk_per_trade_pct, trailing_stop_pct)"
                    },
                    "custom_prompt": {
                        "type": "string",
                        "description": "Updated tactical guideline for the strategy"
                    }
                },
                "required": ["strategy_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_learned_memory",
            "description": "Record a new tactical rule, market insight, or lesson learned into the AI's long-term memory bank.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short concise title of the rule or lesson"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["rule", "insight", "lesson", "parameter_tuning", "mistake"],
                        "description": "Category of memory"
                    },
                    "content": {
                        "type": "string",
                        "description": "Detailed explanation of the rule or insight"
                    },
                    "market_condition": {
                        "type": "string",
                        "enum": ["BULLISH", "BEARISH", "RANGING", "HIGH_VOLATILITY", "ALL"],
                        "description": "Applicable market regime"
                    },
                    "confidence_score": {
                        "type": "number",
                        "description": "Confidence rating from 0.0 to 1.0"
                    }
                },
                "required": ["title", "category", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_overview",
            "description": "Get real-time Binance prices, 24h statistics, and technical indicator metrics for a cryptocurrency pair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Trading pair symbol, e.g. 'BTCUSDT', 'ETHUSDT', 'SOLUSDT'"
                    },
                    "timeframe": {
                        "type": "string",
                        "enum": ["5m", "15m", "1h", "4h", "1d"],
                        "description": "Candle timeframe for indicator analysis"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_manual_trade",
            "description": "Open a manual trade position in Paper or Real mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Pair to trade, e.g. BTCUSDT"
                    },
                    "side": {
                        "type": "string",
                        "enum": ["BUY", "SELL"],
                        "description": "Order side"
                    },
                    "amount_usdt": {
                        "type": "number",
                        "description": "USDT amount to allocate"
                    },
                    "stop_loss_pct": {
                        "type": "number",
                        "description": "Stop loss percentage (e.g. 1.5)"
                    },
                    "take_profit_pct": {
                        "type": "number",
                        "description": "Take profit percentage (e.g. 3.0)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for trade"
                    }
                },
                "required": ["symbol", "side", "amount_usdt"]
            }
        }
    }
]


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token) so the payload stays under model limits."""
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def _bounded_chars(text: str | None, max_chars: int) -> str | None:
    """Truncate a string while keeping the end marker so the model knows it was cut."""
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 24)].rstrip() + "\n...[truncated]"


class AIAgent:
    def __init__(self):
        self._groq_client: Optional[AsyncGroq] = None
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL

    @staticmethod
    def _sanitize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
        """
        Ensure every tool call matches the shape Groq requires for assistant messages:
        {id, type, function: {name, arguments}}. Drop malformed leftovers (e.g. persisted
        tool_executed entries) instead of sending them back to the API.
        """
        if not isinstance(tool_calls, list):
            return []
        clean: list[dict[str, Any]] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else None
            if not fn:
                # Try the {id,type,function} wrapper; otherwise drop.
                continue
            name = fn.get("name")
            args = fn.get("arguments")
            if not name:
                continue
            if not isinstance(args, str):
                try:
                    args = json.dumps(args or {}, ensure_ascii=False, default=str)
                except Exception:
                    args = "{}"
            call_id = call.get("id") or f"call_{abs(hash(name)) % 1000000007}"
            clean.append({
                "id": str(call_id),
                "type": call.get("type") or "function",
                "function": {"name": str(name), "arguments": args}
            })
        return clean

    def _compact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enforce a hard input-token budget so requests never exceed model limits (avoids 413)."""
        max_tokens = settings.GROQ_MAX_INPUT_TOKENS
        # Very conservative budgets (4 chars/token)
        system_budget = int(max_tokens * 0.12)
        content_budget = int(max_tokens * 0.75)

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "system" and "content" in msg:
                msg["content"] = _bounded_chars(msg["content"], system_budget * 4)

        # Per-message content cap so no single message can blow the whole window
        per_msg_chars = int(max_tokens * 0.35) * 4
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if "content" in msg and isinstance(msg["content"], str):
                msg["content"] = _bounded_chars(msg["content"], min(content_budget * 4, per_msg_chars))
            elif "tool_calls" in msg:
                # Repair/shape tool calls the way Groq expects them
                calls = self._sanitize_tool_calls(msg.get("tool_calls"))
                if calls:
                    # Shrink oversized arguments strings
                    for call in calls:
                        fn = call.get("function")
                        if fn and isinstance(fn.get("arguments"), str) and len(fn["arguments"]) > 1500:
                            fn["arguments"] = _bounded_chars(fn["arguments"], 1500)
                    msg["tool_calls"] = calls
                else:
                    msg.pop("tool_calls", None)
            elif "content" in msg and not isinstance(msg["content"], str):
                msg["content"] = _bounded_chars(
                    json.dumps(msg["content"], ensure_ascii=False, default=str), content_budget * 4
                )

        # Hard cap for dict content (normalized already above)
        total_est = sum(_estimate_tokens(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
        if total_est > max_tokens:
            # Drop oldest non-system messages until under budget
            pruned = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
            for m in messages:
                if not isinstance(m, dict) or m.get("role") == "system":
                    continue
                pruned.append(m)
                if sum(_estimate_tokens(str(x.get("content", ""))) for x in pruned) > max_tokens:
                    pruned.pop()
                    break
            messages = pruned
        return messages

    async def get_client(self) -> Optional[AsyncGroq]:
        # Check DB for stored runtime API key
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_API_KEY"))
            db_key = res.scalar_one_or_none()
            if db_key and db_key.value:
                self.api_key = db_key.value

            res_model = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_MODEL"))
            db_model = res_model.scalar_one_or_none()
            if db_model and db_model.value:
                self.model = db_model.value

        if not self.api_key:
            return None

        if self._groq_client is None:
            self._groq_client = AsyncGroq(api_key=self.api_key)
        return self._groq_client

    async def get_available_models(self, key: Optional[str] = None) -> list[str]:
        """Fetch active models supported by the provided or current Groq key."""
        api_key = (key or self.api_key or "").strip()
        fallback_models = [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "groq/compound",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "llama-3.1-70b-versatile",
            "deepseek-r1-distill-llama-70b",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
            "qwen-2.5-32b"
        ]
        if not api_key:
            return fallback_models

        try:
            client = AsyncGroq(api_key=api_key)
            models_page = await client.models.list()
            raw_ids = [m.id for m in models_page.data if not m.id.startswith("whisper")]
            if raw_ids:
                priority = [
                    "qwen/qwen3.8-27b",
                    "openai/gpt-oss-120b",
                    "openai/gpt-oss-20b",
                    "groq/compound",
                    "llama-3.3-70b-versatile",
                    "llama-3.1-8b-instant",
                    "llama3-70b-8192",
                    "llama3-8b-8192",
                    "llama-3.1-70b-versatile",
                    "deepseek-r1-distill-llama-70b"
                ]
                sorted_models = sorted(
                    raw_ids,
                    key=lambda x: (0 if x in priority else 1, priority.index(x) if x in priority else x)
                )
                return sorted_models
        except Exception as e:
            logger.warning(f"Could not list models from Groq: {e}")

        return fallback_models

    async def test_connection(self, key: str, model: str) -> dict[str, Any]:
        """
        Validates Groq API key, dynamically detects active models on user's tier,
        and auto-recovers if a model ID is unavailable or deprecated.
        """
        if not key or not key.strip():
            return {"success": False, "error": "API Key is empty"}

        cleaned_key = key.strip()
        client = AsyncGroq(api_key=cleaned_key)
        
        # 1. Fetch available models on user's Groq key
        available_models = await self.get_available_models(cleaned_key)
        
        target_model = model.strip() if model else "llama-3.3-70b-versatile"
        if available_models and target_model not in available_models:
            target_model = available_models[0]

        # 2. Test completion with target model
        try:
            resp = await client.chat.completions.create(
                model=target_model,
                messages=[{"role": "user", "content": "Respond with 'CONNECTED'"}],
                max_tokens=10,
                temperature=0.1
            )
            content = resp.choices[0].message.content if resp.choices else "OK"
            return {
                "success": True,
                "model": target_model,
                "available_models": available_models,
                "message": f"Groq AI authenticated successfully using {target_model}",
                "reply": content
            }
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"Groq test failed for model {target_model}: {err_msg}")
            
            # Try other models from available list
            for fallback_m in available_models:
                if fallback_m == target_model:
                    continue
                try:
                    resp = await client.chat.completions.create(
                        model=fallback_m,
                        messages=[{"role": "user", "content": "Respond with 'CONNECTED'"}],
                        max_tokens=10,
                        temperature=0.1
                    )
                    return {
                        "success": True,
                        "model": fallback_m,
                        "available_models": available_models,
                        "message": f"Connected using active model {fallback_m} (Model '{target_model}' was unavailable)",
                        "reply": resp.choices[0].message.content
                    }
                except Exception:
                    continue

            return {"success": False, "error": f"Groq Error: {err_msg}", "available_models": available_models}

    async def set_api_key(self, key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = key.strip()
        self.model = model.strip()
        self._groq_client = AsyncGroq(api_key=self.api_key)
        async with AsyncSessionLocal() as session:
            # Store in DB
            res = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_API_KEY"))
            kv = res.scalar_one_or_none()
            if kv:
                kv.value = self.api_key
            else:
                session.add(SettingKV(key="GROQ_API_KEY", value=self.api_key))

            res_m = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_MODEL"))
            kv_m = res_m.scalar_one_or_none()
            if kv_m:
                kv_m.value = self.model
            else:
                session.add(SettingKV(key="GROQ_MODEL", value=self.model))
            await session.commit()

    async def chat(self, user_message: str, session_id: str = "default") -> dict[str, Any]:
        """
        Processes a multi-turn chat message with Groq LLM, executes tool calls,
        updates memory and returns the AI assistant response.
        Includes automatic model recovery if a model is deprecated or not found.
        """
        client = await self.get_client()
        if not client:
            return {
                "success": False,
                "role": "assistant",
                "content": "⚠️ Groq API Key is not configured. Please enter your Groq API Key in the **Settings / Auth** tab to activate the AI Strategy Coach & Optimizer.",
                "tools_executed": []
            }

        async with AsyncSessionLocal() as session:
            # Save user message to database
            session.add(ChatMessage(
                session_id=session_id,
                role="user",
                content=user_message,
                timestamp=datetime.datetime.utcnow()
            ))
            await session.commit()

            # Load recent conversation history (configurable count)
            history_res = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.timestamp.desc())
                .limit(settings.GROQ_HISTORY_MESSAGES)
            )
            history_messages = list(reversed(history_res.scalars().all()))

            # Load active AI learned memories (bounded) to inject into system prompt
            mem_res = await session.execute(
                select(StrategyMemory).order_by(StrategyMemory.confidence_score.desc()).limit(settings.GROQ_MEMORY_BANK_LIMIT)
            )
            memories = mem_res.scalars().all()
            memory_context = "\n".join([
                f"- [{m.category.upper()}] ({m.market_condition}) {m.title}: {_bounded_chars(m.content, 300)} (Confidence: {m.confidence_score*100:.0f}%)"
                for m in memories
            ])

            # Get active strategy
            strat_res = await session.execute(select(StrategyConfig).where(StrategyConfig.is_active == True))
            active_strat = strat_res.scalars().first()
            strat_info = _bounded_chars(
                f"Active Strategy: {active_strat.display_name} (Params: {active_strat.parameters})",
                600
            ) if active_strat else "No active strategy selected"

        # Build messages payload for Groq
        messages = [
            {
                "role": "system",
                "content": f"{SYSTEM_PROMPT}\n\nCURRENT ACTIVE STRATEGY:\n{strat_info}\n\nCURRENT LEARNED MEMORY BANK:\n{memory_context}"
            }
        ]

        for m in history_messages:
            msg_obj = {"role": m.role, "content": m.content}
            if m.tool_calls:
                try:
                    msg_obj["tool_calls"] = json.loads(m.tool_calls)
                except Exception:
                    pass
            messages.append(msg_obj)

        # Enforce a hard token budget before hitting Groq (prevents 413 rate-limit errors)
        messages = self._compact_messages(messages)

        tools_executed = []
        try:
            # 1st Groq API Call with model recovery fallback
            response = None
            current_model = self.model

            try:
                response = await client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    tools=AVAILABLE_TOOLS,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=settings.GROQ_MAX_TOKENS
                )
            except Exception as initial_err:
                err_str = str(initial_err).lower()
                logger.warning(f"Groq completion error with model {current_model}: {initial_err}")

                # If we hit a token/rate-limit ceiling, drop the tool schema too
                # (AVAILABLE_TOOLS costs ~1.5k input tokens by itself) and shrink payload.
                rate_limited = ("rate_limit" in err_str or "request too large" in err_str or "tokens" in err_str)
                if rate_limited:
                    settings.GROQ_MAX_INPUT_TOKENS = 2500
                    settings.GROQ_HISTORY_MESSAGES = 3
                    settings.GROQ_MEMORY_BANK_LIMIT = 2
                    messages = self._compact_messages(messages)

                # If model not found or token-limited, fetch active models and retry.
                # Drop tools when token-limited since the tool schema consumes input tokens.
                available_models = await self.get_available_models()
                tools_arg = None if rate_limited else AVAILABLE_TOOLS
                for alt_model in available_models:
                    if alt_model == current_model:
                        continue
                    try:
                        logger.info(f"Attempting Groq chat with alternate model: {alt_model}")
                        response = await client.chat.completions.create(
                            model=alt_model,
                            messages=messages,
                            tools=tools_arg,
                            temperature=0.3,
                            max_tokens=settings.GROQ_MAX_TOKENS
                        )
                        # Save recovered model
                        self.model = alt_model
                        current_model = alt_model
                        break
                    except Exception as alt_err:
                        continue

                if response is None:
                    raise initial_err

            assistant_msg = response.choices[0].message

            tool_calls_data = []

            if hasattr(assistant_msg, "tool_calls") and assistant_msg.tool_calls:
                tool_calls_data = self._sanitize_tool_calls([
                    {
                        "id": tc.id or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for i, tc in enumerate(assistant_msg.tool_calls)
                ])

                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                    "tool_calls": tool_calls_data
                })

                for tc in assistant_msg.tool_calls:
                    fn_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        args = {}

                    tool_result = await self._execute_tool(fn_name, args)
                    tools_executed.append({
                        "name": fn_name,
                        "args": args,
                        "result": tool_result
                    })

                    result_str = _bounded_chars(
                        json.dumps(tool_result, ensure_ascii=False, default=str),
                        settings.GROQ_TOOL_RESULT_CHARS
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": result_str
                    })

                messages = self._compact_messages(messages)
                second_response = await client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=settings.GROQ_MAX_TOKENS
                )
                final_content = second_response.choices[0].message.content or "Task completed."
            else:
                final_content = assistant_msg.content or "Understood."

            async with AsyncSessionLocal() as session:
                session.add(ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=final_content,
                    tool_calls=json.dumps(tool_calls_data) if tool_calls_data else None,
                    timestamp=datetime.datetime.utcnow()
                ))
                await session.commit()

            return {
                "success": True,
                "role": "assistant",
                "content": final_content,
                "tools_executed": tools_executed
            }

        except Exception as e:
            logger.error(f"Groq Chat error: {e}")
            return {
                "success": False,
                "role": "assistant",
                "content": f"❌ Error communicating with Groq AI: {str(e)}",
                "tools_executed": []
            }

    async def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Tool dispatcher for AI agent."""
        if name == "analyze_trade_history":
            limit = min(int(args.get("limit", 8)), 15)
            mode = args.get("mode", "ALL")
            async with AsyncSessionLocal() as session:
                query = select(Trade).order_by(desc(Trade.entry_time)).limit(limit)
                if mode != "ALL":
                    query = query.where(Trade.mode == mode)
                res = await session.execute(query)
                trades = [t.to_dict() for t in res.scalars().all()]

                closed = [t for t in trades if t["status"] == "CLOSED"]
                win_count = sum(1 for t in closed if t["pnl"] > 0)
                loss_count = sum(1 for t in closed if t["pnl"] <= 0)
                total_pnl = sum(t["pnl"] for t in closed)
                win_rate = (win_count / len(closed) * 100) if closed else 0.0

                # Compact per-trade summaries to keep the payload small for small-context models
                trade_summaries = [
                    {
                        "id": t["id"],
                        "symbol": t["symbol"],
                        "side": t["side"],
                        "mode": t["mode"],
                        "amount_usdt": t["amount_usdt"],
                        "entry_price": t["entry_price"],
                        "exit_price": t["exit_price"],
                        "pnl": t["pnl"],
                        "pnl_pct": t["pnl_pct"],
                        "status": t["status"],
                        "strategy": t["strategy_name"],
                        "entry_time": t["entry_time"],
                        "exit_time": t["exit_time"],
                        "entry_reason": t["entry_reason"]
                    }
                    for t in trades
                ]

                return {
                    "total_trades_analyzed": len(trades),
                    "closed_trades": len(closed),
                    "win_count": win_count,
                    "loss_count": loss_count,
                    "win_rate_pct": round(win_rate, 2),
                    "total_realized_pnl": round(total_pnl, 2),
                    "trades": trade_summaries
                }

        elif name == "update_strategy_parameters":
            strat_name = args.get("strategy_name")
            new_params = args.get("parameters")
            new_risk = args.get("risk_settings")
            custom_prompt = args.get("custom_prompt")

            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(StrategyConfig).where(StrategyConfig.name == strat_name)
                )
                strategy = res.scalar_one_or_none()
                if not strategy:
                    # Search by display name
                    res_disp = await session.execute(
                        select(StrategyConfig).where(StrategyConfig.display_name.ilike(f"%{strat_name}%"))
                    )
                    strategy = res_disp.scalar_one_or_none()

                if not strategy:
                    return {"success": False, "error": f"Strategy '{strat_name}' not found in database"}

                if new_params:
                    curr_params = json.loads(strategy.parameters) if strategy.parameters else {}
                    curr_params.update(new_params)
                    strategy.parameters = json.dumps(curr_params)

                if new_risk:
                    curr_risk = json.loads(strategy.risk_settings) if strategy.risk_settings else {}
                    curr_risk.update(new_risk)
                    strategy.risk_settings = json.dumps(curr_risk)

                if custom_prompt:
                    strategy.custom_prompt = custom_prompt

                strategy.version += 1
                strategy.updated_at = datetime.datetime.utcnow()
                await session.commit()
                return {
                    "success": True,
                    "message": f"Strategy '{strategy.display_name}' parameters updated to version {strategy.version}",
                    "current_config": strategy.to_dict()
                }

        elif name == "save_learned_memory":
            async with AsyncSessionLocal() as session:
                mem = StrategyMemory(
                    title=args.get("title", "Insight"),
                    category=args.get("category", "insight"),
                    content=args.get("content", ""),
                    market_condition=args.get("market_condition", "ALL"),
                    confidence_score=float(args.get("confidence_score", 0.85))
                )
                session.add(mem)
                await session.commit()
                await session.refresh(mem)
                return {
                    "success": True,
                    "message": f"Learned memory '{mem.title}' added to long-term memory bank",
                    "memory": mem.to_dict()
                }

        elif name == "get_market_overview":
            symbol = args.get("symbol", "BTCUSDT").upper().replace("/", "")
            tf = args.get("timeframe", "15m")
            ticker = await binance_client.fetch_ticker(symbol)
            df = await binance_client.fetch_klines(symbol, timeframe=tf, limit=100)
            indicators = analyze_all_indicators(df) if not df.empty else {}
            return {
                "symbol": symbol,
                "timeframe": tf,
                "price": ticker.get("price"),
                "high24h": ticker.get("high24h"),
                "low24h": ticker.get("low24h"),
                "volume24h": ticker.get("volume24h"),
                "priceChange24hPct": ticker.get("priceChangePercent"),
                "technical_indicators": indicators
            }

        elif name == "execute_manual_trade":
            symbol = args.get("symbol", "BTCUSDT")
            side = args.get("side", "BUY")
            amount = float(args.get("amount_usdt", 100))
            sl = args.get("stop_loss_pct")
            tp = args.get("take_profit_pct")
            reason = args.get("reason", "Manual Trade executed via AI Agent")

            res = await paper_engine.open_position(
                symbol=symbol,
                side=side,
                amount_usdt=amount,
                strategy_name="AI_Manual_Order",
                entry_reason=reason,
                stop_loss_pct=sl,
                take_profit_pct=tp
            )
            return res

        return {"error": f"Unknown tool: {name}"}

    async def evaluate_ict_setup_with_llm(self, symbol: str, ict_state: dict[str, Any]) -> dict[str, Any]:
        """
        Submits the computed ICT multi-timeframe structural data to Groq LLM for AI reasoning,
        validation, and execution confirmation according to the ICT playbook.
        
        Returns structured decision: PASS, FAIL, or WAIT with confidence and reasoning.
        The LLM CANNOT modify risk parameters - it only validates the setup.
        
        Rate limit handling: respects Groq daily TPD limits with exponential backoff.
        Caching: returns cached result if data hasn't changed significantly within TTL.
        """
        # Check global rate limit cooldown (from 429 errors)
        global _rate_limit_cooldown_until
        if time.time() < _rate_limit_cooldown_until:
            remaining = int(_rate_limit_cooldown_until - time.time())
            logger.warning(f"LLM in cooldown ({remaining}s). Returning WAIT for {symbol}.")
            return {"decision": "WAIT", "confidence": 0.0, "llm_reasoning": f"Rate limit cooldown. Try again in {remaining}s", "model": "rate_limited"}

        # Check if we're within the per-minute call limit
        now = time.time()
        llm_call_timestamps[:] = [t for t in llm_call_timestamps if now - t < 60]
        if len(llm_call_timestamps) >= LLM_CALLS_PER_MINUTE:
            oldest = min(llm_call_timestamps)
            wait_time = 60 - (now - oldest)
            logger.warning(f"LLM per-minute limit reached ({LLM_CALLS_PER_MINUTE}/min). Waiting {wait_time:.1f}s for {symbol}.")
            return {"decision": "WAIT", "confidence": 0.0, "llm_reasoning": f"LLM call limit ({LLM_CALLS_PER_MINUTE}/min). Wait {wait_time:.0f}s", "model": "rate_limited"}

        # Check cache
        cache_key = f"{symbol}_{hash(json.dumps(ict_state, sort_keys=True))}"
        if cache_key in llm_cache:
            cached = llm_cache[cache_key]
            if now - cached.get("_cached_at", 0) < llm_cache_ttl:
                logger.debug(f"LLM cache hit for {symbol}")
                return cached

        client = await self.get_client()
        if not client:
            return {"decision": "PASS", "confidence": 0.5, "llm_reasoning": "AI key not configured; algorithmic validation used", "model": "none"}

        # Compact prompt
        prompt = (
            f"Evaluate ICT setup for {symbol}:\n"
            f"4H: {json.dumps(ict_state.get('macro_4h', {}))}\n"
            f"1H: {json.dumps(ict_state.get('bias_1h', {}))}\n"
            f"15M POI: {json.dumps(ict_state.get('poi_15m', {}))}\n"
            f"5M Entry: {json.dumps(ict_state.get('entry_5m', {}))}\n"
            f"A+ Checks: {json.dumps(ict_state.get('a_plus', {}))}\n"
            f"SL: {ict_state.get('stop_loss')}, TP: {ict_state.get('tp1')}, RR: {ict_state.get('rr_to_tp1')}\n\n"
            f"Return ONLY JSON: {{decision: PASS|FAIL|WAIT, confidence: 0.0-1.0, reasoning: str, concerns: [str]}}"
        )

        try:
            available_models = await self.get_available_models()
            target_model = self.model if self.model in available_models else (available_models[0] if available_models else "qwen/qwen3.8-27b")
            resp = await client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            content = resp.choices[0].message.content if resp.choices else ""
            
            # Track call
            llm_call_timestamps.append(now)
            
            # Parse and validate JSON response
            try:
                result = json.loads(content)
                decision = result.get("decision", "WAIT").upper()
                if decision not in ("PASS", "FAIL", "WAIT"):
                    decision = "WAIT"
                confidence = float(result.get("confidence", 0.0))
                confidence = max(0.0, min(1.0, confidence))
                reasoning = str(result.get("reasoning", ""))
                concerns = result.get("concerns", [])
                if not isinstance(concerns, list):
                    concerns = [str(concerns)]
                
                cached_result = {
                    "decision": decision,
                    "confidence": confidence,
                    "llm_reasoning": reasoning,
                    "concerns": concerns,
                    "model": target_model,
                    "_cached_at": now
                }
                llm_cache[cache_key] = cached_result
                
                return cached_result
            except json.JSONDecodeError:
                logger.warning(f"LLM returned invalid JSON: {content}")
                return {"decision": "WAIT", "confidence": 0.0, "llm_reasoning": "Invalid JSON response from LLM", "model": target_model}
                
        except Exception as e:
            error_str = str(e)
            logger.warning(f"Groq ICT LLM validation warning: {e}")
            
            # Handle rate limit (429)
            if "429" in error_str or "rate_limit" in error_str.lower() or "Rate limit" in error_str:
                # Extract wait time if available, otherwise default to 5 minutes
                wait_sec = 300
                try:
                    import re
                    match = re.search(r'Please try again in ([\d.]+)s', error_str)
                    if match:
                        wait_sec = float(match.group(1)) + 10
                except:
                    pass
                global _rate_limit_cooldown_until
                _rate_limit_cooldown_until = now + wait_sec
                logger.warning(f"LLM rate limit hit. Cooldown set for {wait_sec:.0f}s")
                return {"decision": "WAIT", "confidence": 0.0, "llm_reasoning": f"Rate limited. Try again in {wait_sec:.0f}s", "model": "rate_limited"}
            
            return {"decision": "WAIT", "confidence": 0.0, "llm_reasoning": f"LLM error: {error_str}", "model": "error"}


ai_agent = AIAgent()
