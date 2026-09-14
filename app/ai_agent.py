import datetime
import json
import logging
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

SYSTEM_PROMPT = """You are DEEPALPHA AI, an elite institutional crypto quantitative trading and strategy optimization AI agent for Binance.
You have direct control over a 24/7 trading engine capable of Paper Trading and Real Binance Trading.

Your primary missions:
1. Strategy Optimization & Continuous Learning: Analyze trade performance (win rates, profit factor, losing patterns, risk-to-reward) from paper/real trade logs, identify what works in current market regimes (bullish, bearish, chop), and proactively tune active strategy parameters (RSI thresholds, EMA spans, Stop-Loss, Take-Profit, Trailing Stops).
2. Memory & Playbook Evolution: Synthesize trading insights into persistent long-term memory records (`save_learned_memory`) so the autonomous engine adapts over time.
3. Live Market Intelligence: Deeply evaluate Binance pairs with technical indicators (RSI, MACD, Bollinger Bands, EMA 20/50/200, Volume, ATR) and formulate high-probability trade setups.
4. Execution & Supervision: Execute trades, adjust risk rules, start/stop the 24/7 autonomous runner when instructed.

When answering, be decisive, analytical, precise, and quantify your reasoning.
You have access to tools for querying trade performance, tuning parameters, saving lessons, querying live market metrics, and managing bot operations. Always use the appropriate tool when user asks to inspect trades, change strategy, adjust parameters, or check market conditions.
"""

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


class AIAgent:
    def __init__(self):
        self._groq_client: Optional[AsyncGroq] = None
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL

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

    async def set_api_key(self, key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = key
        self.model = model
        self._groq_client = AsyncGroq(api_key=key)
        async with AsyncSessionLocal() as session:
            # Store in DB
            res = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_API_KEY"))
            kv = res.scalar_one_or_none()
            if kv:
                kv.value = key
            else:
                session.add(SettingKV(key="GROQ_API_KEY", value=key))

            res_m = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_MODEL"))
            kv_m = res_m.scalar_one_or_none()
            if kv_m:
                kv_m.value = model
            else:
                session.add(SettingKV(key="GROQ_MODEL", value=model))
            await session.commit()

    async def chat(self, user_message: str, session_id: str = "default") -> dict[str, Any]:
        """
        Processes a multi-turn chat message with Groq LLM, executes tool calls,
        updates memory and returns the AI assistant response.
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

            # Load recent conversation history (last 15 messages)
            history_res = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.timestamp.desc())
                .limit(15)
            )
            history_messages = list(reversed(history_res.scalars().all()))

            # Load active AI learned memories to inject into system prompt
            mem_res = await session.execute(
                select(StrategyMemory).order_by(StrategyMemory.confidence_score.desc()).limit(8)
            )
            memories = mem_res.scalars().all()
            memory_context = "\n".join([
                f"- [{m.category.upper()}] ({m.market_condition}) {m.title}: {m.content} (Confidence: {m.confidence_score*100:.0f}%, Success: {m.success_rate:.1f}%)"
                for m in memories
            ])

            # Get active strategy
            strat_res = await session.execute(select(StrategyConfig).where(StrategyConfig.is_active == True))
            active_strat = strat_res.scalars().first()
            strat_info = f"Active Strategy: {active_strat.display_name} (Params: {active_strat.parameters})" if active_strat else "No active strategy selected"

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

        tools_executed = []
        try:
            # 1st Groq API Call
            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=AVAILABLE_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2048
            )

            assistant_msg = response.choices[0].message

            # Check if tools are called
            if assistant_msg.tool_calls:
                # Add assistant message with tool calls to conversation
                tool_calls_data = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in assistant_msg.tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                    "tool_calls": tool_calls_data
                })

                # Execute each tool call
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

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": json.dumps(tool_result)
                    })

                # 2nd Groq call to generate final human response with tool results
                second_response = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=2048
                )
                final_content = second_response.choices[0].message.content or "Task completed."
            else:
                final_content = assistant_msg.content or "Understood."

            # Save assistant reply to database
            async with AsyncSessionLocal() as session:
                session.add(ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=final_content,
                    tool_calls=json.dumps(tools_executed) if tools_executed else None,
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
            limit = args.get("limit", 15)
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

                return {
                    "total_trades_analyzed": len(trades),
                    "closed_trades": len(closed),
                    "win_count": win_count,
                    "loss_count": loss_count,
                    "win_rate_pct": round(win_rate, 2),
                    "total_realized_pnl": round(total_pnl, 2),
                    "trades": trades
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


ai_agent = AIAgent()
