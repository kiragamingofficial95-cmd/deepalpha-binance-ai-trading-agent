import asyncio
import datetime
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, desc

from app.config import settings
from app.database import init_db, AsyncSessionLocal, SettingKV, Trade, ChatMessage, StrategyMemory, StrategyConfig, PaperBalance
from app.binance_client import binance_client
from app.paper_engine import paper_engine
from app.risk_manager import risk_manager
from app.strategy_engine import strategy_engine
from app.ai_agent import ai_agent
from app.bot_runner import bot_runner
from app.analytics import analytics_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database
    await init_db()
    
    # Load stored settings into memory
    async with AsyncSessionLocal() as session:
        # Load Binance keys
        bk = await session.execute(select(SettingKV).where(SettingKV.key == "BINANCE_API_KEY"))
        bk_val = bk.scalar_one_or_none()
        bs = await session.execute(select(SettingKV).where(SettingKV.key == "BINANCE_SECRET_KEY"))
        bs_val = bs.scalar_one_or_none()
        bt = await session.execute(select(SettingKV).where(SettingKV.key == "BINANCE_TESTNET"))
        bt_val = bt.scalar_one_or_none()
        
        if bk_val and bs_val:
            await binance_client.update_credentials(
                api_key=bk_val.value,
                secret_key=bs_val.value,
                testnet=(bt_val.value.lower() == "true") if bt_val else False
            )
        
        # Load Groq key
        gk = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_API_KEY"))
        gk_val = gk.scalar_one_or_none()
        gm = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_MODEL"))
        gm_val = gm.scalar_one_or_none()
        if gk_val and gk_val.value:
            await ai_agent.set_api_key(
                key=gk_val.value,
                model=gm_val.value if gm_val else settings.GROQ_MODEL
            )

    # Start 24/7 background trading runner
    await bot_runner.start()
    logger.info("Autonomous AI Trading Engine initialized and active.")

    yield

    # Shutdown
    await bot_runner.stop()
    await binance_client.close()
    logger.info("Autonomous AI Trading Engine gracefully stopped.")


app = FastAPI(
    title=settings.APP_NAME,
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static & Templates setup
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# Request Pydantic Schemas
class BinanceCredentialsRequest(BaseModel):
    api_key: str
    secret_key: str
    testnet: bool = False

class GroqConfigRequest(BaseModel):
    api_key: str
    model: str = "llama-3.3-70b-versatile"

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"

class ManualTradeRequest(BaseModel):
    symbol: str
    side: str  # BUY or SELL
    amount_usdt: float
    mode: Optional[str] = None  # PAPER or REAL
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    reason: Optional[str] = "Manual Dashboard Execution"

class StrategyUpdateRequest(BaseModel):
    strategy_id: int
    is_active: Optional[bool] = None
    timeframe: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None
    risk_settings: Optional[dict[str, Any]] = None
    custom_prompt: Optional[str] = None

class MemoryCreateRequest(BaseModel):
    title: str
    category: str
    content: str
    market_condition: str = "ALL"
    confidence_score: float = 0.85

class WatchlistUpdateRequest(BaseModel):
    symbols: list[str]


# ==========================================
# Web UI Routes
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.APP_NAME}
    )


# ==========================================
# WebSocket Stream for Real-time Dashboard Updates
# ==========================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = bot_runner.add_listener()
    try:
        # Send initial status
        init_status = {
            "type": "INITIAL_STATE",
            "data": {
                "bot": bot_runner.get_status(),
                "time": datetime.datetime.utcnow().isoformat()
            }
        }
        await websocket.send_text(json.dumps(init_status))

        while True:
            # Wait for event from bot runner or heartbeat
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                await websocket.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                # Send ping heartbeat
                await websocket.send_text(json.dumps({"type": "HEARTBEAT", "time": datetime.datetime.utcnow().isoformat()}))
    except WebSocketDisconnect:
        bot_runner.remove_listener(queue)
    except Exception as e:
        logger.warning(f"WebSocket client error: {e}")
        bot_runner.remove_listener(queue)


# ==========================================
# Bot Runner Management Endpoints
# ==========================================
@app.get("/api/status")
async def get_bot_status():
    status = bot_runner.get_status()
    analytics = await analytics_engine.get_performance_summary(mode=bot_runner.trading_mode)
    return {
        "success": True,
        "bot": status,
        "performance": analytics,
        "groq_configured": bool(ai_agent.api_key),
        "binance_configured": bool(binance_client.api_key)
    }

@app.post("/api/bot/start")
async def start_bot():
    await bot_runner.start()
    return {"success": True, "message": "Autonomous 24/7 Trading Agent started"}

@app.post("/api/bot/stop")
async def stop_bot():
    await bot_runner.stop()
    return {"success": True, "message": "Autonomous 24/7 Trading Agent stopped"}

@app.post("/api/bot/mode")
async def set_mode(payload: dict[str, str]):
    mode = payload.get("mode", "PAPER").upper()
    await bot_runner.set_trading_mode(mode)
    return {"success": True, "trading_mode": mode}

@app.post("/api/bot/watchlist")
async def update_watchlist(payload: WatchlistUpdateRequest):
    await bot_runner.set_watchlist(payload.symbols)
    return {"success": True, "watchlist": bot_runner.watchlist}


# ==========================================
# Authentication & Setup Endpoints
# ==========================================
@app.post("/api/auth/binance")
async def setup_binance(payload: BinanceCredentialsRequest):
    await binance_client.update_credentials(
        api_key=payload.api_key,
        secret_key=payload.secret_key,
        testnet=payload.testnet
    )
    # Test connection
    test_result = await binance_client.test_connection()

    if test_result.get("connected"):
        async with AsyncSessionLocal() as session:
            for k, v in [
                ("BINANCE_API_KEY", payload.api_key),
                ("BINANCE_SECRET_KEY", payload.secret_key),
                ("BINANCE_TESTNET", str(payload.testnet).lower())
            ]:
                res = await session.execute(select(SettingKV).where(SettingKV.key == k))
                kv = res.scalar_one_or_none()
                if kv:
                    kv.value = v
                else:
                    session.add(SettingKV(key=k, value=v))
            await session.commit()
        bot_runner.log_event("INFO", f"Binance API keys connected successfully ({'Testnet' if payload.testnet else 'Mainnet'})")

    return test_result

@app.get("/api/auth/binance/test")
async def test_binance_status():
    return await binance_client.test_connection()

@app.post("/api/auth/groq")
async def setup_groq(payload: GroqConfigRequest):
    test_result = await ai_agent.test_connection(payload.api_key, payload.model)
    if test_result.get("success"):
        actual_model = test_result.get("model", payload.model)
        await ai_agent.set_api_key(key=payload.api_key, model=actual_model)
        bot_runner.log_event("INFO", f"Groq AI connected successfully with model {actual_model}")
        return {
            "success": True,
            "message": test_result.get("message", "Connected successfully"),
            "model": actual_model,
            "reply": test_result.get("reply")
        }
    return {
        "success": False,
        "error": test_result.get("error", "Connection failed"),
        "model": payload.model
    }


# ==========================================
# Trade Operations & Balances
# ==========================================
@app.get("/api/balances")
async def get_balances():
    paper_bals = await paper_engine.get_all_balances()
    real_bals = await binance_client.fetch_real_balance()
    return {
        "mode": bot_runner.trading_mode,
        "paper": paper_bals,
        "real": real_bals
    }

@app.post("/api/paper/reset")
async def reset_paper_balance(payload: dict[str, float] = None):
    amount = payload.get("initial_usdt", 10000.0) if payload else 10000.0
    res = await paper_engine.reset_balance(amount)
    bot_runner.log_event("INFO", f"Paper Trading balance reset to ${amount:,.2f} USDT")
    return res

@app.get("/api/trades")
async def get_trades(
    mode: Optional[str] = None,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    return await analytics_engine.get_trades_list(mode, status, symbol, limit, offset)

@app.post("/api/trade/open")
async def open_manual_trade(payload: ManualTradeRequest):
    mode = (payload.mode or bot_runner.trading_mode).upper()
    if mode == "PAPER":
        res = await paper_engine.open_position(
            symbol=payload.symbol,
            side=payload.side,
            amount_usdt=payload.amount_usdt,
            strategy_name="Manual_Order",
            entry_reason=payload.reason or "Manual Order via Dashboard",
            stop_loss_pct=payload.stop_loss_pct,
            take_profit_pct=payload.take_profit_pct,
            trailing_stop_pct=payload.trailing_stop_pct
        )
        if res.get("success"):
            bot_runner.log_event("INFO", f"Manual PAPER {payload.side} on {payload.symbol} opened for ${payload.amount_usdt:.2f}", meta=res["trade"])
            bot_runner.broadcast_event({"type": "TRADE_OPENED", "data": res["trade"]})
        return res
    elif mode == "REAL":
        ticker = await binance_client.fetch_ticker(payload.symbol)
        curr_price = ticker.get("price", 0.0)
        if curr_price <= 0:
            return {"success": False, "error": f"Invalid market price for {payload.symbol}"}
        qty = payload.amount_usdt / curr_price
        order_res = await binance_client.place_real_order(
            symbol=payload.symbol,
            side=payload.side,
            order_type="MARKET",
            quantity=qty
        )
        if order_res.get("success"):
            async with AsyncSessionLocal() as session:
                trade = Trade(
                    symbol=payload.symbol.upper(),
                    side=payload.side.upper(),
                    order_type="MARKET",
                    mode="REAL",
                    quantity=qty,
                    entry_price=order_res.get("price", curr_price),
                    amount_usdt=payload.amount_usdt,
                    fees=payload.amount_usdt * 0.00075,
                    status="OPEN",
                    strategy_name="Manual_Order",
                    entry_reason=payload.reason or "Manual Order",
                    entry_time=datetime.datetime.utcnow()
                )
                session.add(trade)
                await session.commit()
                await session.refresh(trade)
                bot_runner.log_event("INFO", f"Manual REAL {payload.side} filled on Binance for {payload.symbol}: ${payload.amount_usdt:.2f}", meta=trade.to_dict())
                bot_runner.broadcast_event({"type": "TRADE_OPENED", "data": trade.to_dict()})
                return {"success": True, "trade": trade.to_dict()}
        return order_res

@app.post("/api/trade/close/{trade_id}")
async def close_trade(trade_id: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Trade).where(Trade.id == trade_id))
        trade = res.scalar_one_or_none()
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        
        if trade.mode == "PAPER":
            close_res = await paper_engine.close_position(trade_id, exit_reason="MANUAL_UI_CLOSE")
            if close_res.get("success"):
                bot_runner.broadcast_event({"type": "TRADE_CLOSED", "data": close_res["trade"]})
            return close_res
        elif trade.mode == "REAL":
            # Place counter order on Binance
            counter_side = "SELL" if trade.side == "BUY" else "BUY"
            real_order = await binance_client.place_real_order(
                symbol=trade.symbol,
                side=counter_side,
                order_type="MARKET",
                quantity=trade.quantity
            )
            if real_order.get("success"):
                ticker = await binance_client.fetch_ticker(trade.symbol)
                fill_price = real_order.get("price") or ticker.get("price", trade.entry_price)
                
                trade.exit_price = fill_price
                trade.exit_time = datetime.datetime.utcnow()
                trade.exit_reason = "MANUAL_REAL_CLOSE"
                trade.status = "CLOSED"
                if trade.side == "BUY":
                    trade.pnl = (fill_price - trade.entry_price) * trade.quantity - (trade.fees * 2)
                    trade.pnl_pct = ((fill_price - trade.entry_price) / trade.entry_price) * 100.0
                else:
                    trade.pnl = (trade.entry_price - fill_price) * trade.quantity - (trade.fees * 2)
                    trade.pnl_pct = ((trade.entry_price - fill_price) / trade.entry_price) * 100.0
                
                await session.commit()
                await session.refresh(trade)
                bot_runner.log_event("INFO", f"Closed REAL trade #{trade.id} ({trade.symbol}): PnL ${trade.pnl:.2f}")
                bot_runner.broadcast_event({"type": "TRADE_CLOSED", "data": trade.to_dict()})
                return {"success": True, "trade": trade.to_dict()}
            return real_order


# ==========================================
# Quantitative Analytics & Performance
# ==========================================
@app.get("/api/analytics")
async def get_analytics(mode: Optional[str] = None):
    return await analytics_engine.get_performance_summary(mode)


# ==========================================
# Market Data & Kline Feeds
# ==========================================
@app.get("/api/market/ticker")
async def get_ticker(symbol: str = "BTCUSDT"):
    return await binance_client.fetch_ticker(symbol)

@app.get("/api/market/klines")
async def get_klines(symbol: str = "BTCUSDT", timeframe: str = "15m", limit: int = 100):
    df = await binance_client.fetch_klines(symbol, timeframe, limit)
    if df.empty:
        return {"symbol": symbol, "candles": []}
    
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time": int(row["timestamp"] / 1000),  # TradingView requires seconds unix timestamp
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"])
        })
    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}


# ==========================================
# Groq AI Chat & Strategy Memory
# ==========================================
@app.post("/api/chat")
async def send_chat_message(payload: ChatRequest):
    return await ai_agent.chat(payload.message, payload.session_id)

@app.get("/api/chat/history")
async def get_chat_history(session_id: str = "default"):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.timestamp.asc())
        )
        messages = [m.to_dict() for m in res.scalars().all()]
        return {"session_id": session_id, "messages": messages}

@app.post("/api/chat/clear")
async def clear_chat_history(session_id: str = "default"):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        )
        for m in res.scalars().all():
            await session.delete(m)
        await session.commit()
    return {"success": True, "message": "Chat memory reset"}


# ==========================================
# Strategy Configs & AI Learned Memory Bank
# ==========================================
@app.get("/api/strategies")
async def get_strategies():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(StrategyConfig))
        return [s.to_dict() for s in res.scalars().all()]

@app.post("/api/strategies/activate/{strategy_id}")
async def activate_strategy(strategy_id: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(StrategyConfig))
        all_strats = res.scalars().all()
        target = None
        for s in all_strats:
            if s.id == strategy_id:
                s.is_active = True
                target = s
            else:
                s.is_active = False
        await session.commit()
        if target:
            bot_runner.log_event("INFO", f"Active Strategy changed to: {target.display_name}")
            return {"success": True, "active_strategy": target.to_dict()}
        raise HTTPException(status_code=404, detail="Strategy not found")

@app.post("/api/strategies/update")
async def update_strategy(payload: StrategyUpdateRequest):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(StrategyConfig).where(StrategyConfig.id == payload.strategy_id))
        strat = res.scalar_one_or_none()
        if not strat:
            raise HTTPException(status_code=404, detail="Strategy not found")

        if payload.is_active is not None:
            strat.is_active = payload.is_active
        if payload.timeframe:
            strat.timeframe = payload.timeframe
        if payload.parameters:
            strat.parameters = json.dumps(payload.parameters)
        if payload.risk_settings:
            strat.risk_settings = json.dumps(payload.risk_settings)
        if payload.custom_prompt is not None:
            strat.custom_prompt = payload.custom_prompt

        strat.version += 1
        strat.updated_at = datetime.datetime.utcnow()
        await session.commit()
        await session.refresh(strat)
        return {"success": True, "strategy": strat.to_dict()}

@app.get("/api/memories")
async def get_memories():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(StrategyMemory).order_by(desc(StrategyMemory.confidence_score)))
        return [m.to_dict() for m in res.scalars().all()]

@app.post("/api/memories")
async def create_memory(payload: MemoryCreateRequest):
    async with AsyncSessionLocal() as session:
        mem = StrategyMemory(
            title=payload.title,
            category=payload.category,
            content=payload.content,
            market_condition=payload.market_condition,
            confidence_score=payload.confidence_score
        )
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        return {"success": True, "memory": mem.to_dict()}

@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(StrategyMemory).where(StrategyMemory.id == memory_id))
        mem = res.scalar_one_or_none()
        if mem:
            await session.delete(mem)
            await session.commit()
            return {"success": True, "message": "Memory deleted"}
        raise HTTPException(status_code=404, detail="Memory not found")
