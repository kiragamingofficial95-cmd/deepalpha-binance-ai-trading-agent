import asyncio
import datetime
import logging
from typing import Any, Optional
from app.config import settings
from app.database import AsyncSessionLocal, SettingKV, Trade
from app.binance_client import binance_client
from app.paper_engine import paper_engine
from app.risk_manager import risk_manager
from app.strategy_engine import strategy_engine
from sqlalchemy import select

logger = logging.getLogger("bot_runner")

class BotRunner:
    """
    24/7 Autonomous execution loop with resilience, error recovery, and event streaming.
    """
    def __init__(self):
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self.watchlist = list(settings.DEFAULT_SYMBOLS)
        self.trading_mode = settings.TRADING_MODE  # PAPER or REAL
        self.scan_interval = settings.SCAN_INTERVAL_SECONDS
        self.last_scan_time: Optional[datetime.datetime] = None
        self.scan_count = 0
        self.recent_logs: list[dict[str, Any]] = []
        self.max_logs = 100
        self.listeners: list[asyncio.Queue] = []
        self.latest_signals: dict[str, Any] = {}

    def log_event(self, level: str, message: str, meta: Optional[dict[str, Any]] = None):
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "level": level,
            "message": message,
            "meta": meta or {}
        }
        self.recent_logs.insert(0, entry)
        if len(self.recent_logs) > self.max_logs:
            self.recent_logs.pop()
        
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)

        # Notify any active WebSockets
        self.broadcast_event({"type": "LOG_EVENT", "data": entry})

    def broadcast_event(self, message: dict[str, Any]):
        for queue in list(self.listeners):
            try:
                queue.put_nowait(message)
            except Exception:
                pass

    def add_listener(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.listeners.append(q)
        return q

    def remove_listener(self, q: asyncio.Queue):
        if q in self.listeners:
            self.listeners.remove(q)

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.log_event("INFO", f"Autonomous 24/7 Trading Agent Engine STARTED in {self.trading_mode} mode")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.log_event("INFO", "Autonomous Trading Agent Engine STOPPED")

    async def set_trading_mode(self, mode: str):
        mode = mode.upper()
        if mode in ["PAPER", "REAL"]:
            self.trading_mode = mode
            self.log_event("INFO", f"Trading mode switched to {mode}")

    async def set_watchlist(self, symbols: list[str]):
        clean = [s.strip().upper().replace("/", "") for s in symbols if s.strip()]
        if clean:
            self.watchlist = clean
            self.log_event("INFO", f"Watchlist updated: {', '.join(self.watchlist)}")

    async def _run_loop(self):
        while self.is_running:
            try:
                await self._execute_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log_event("ERROR", f"Error in bot runner cycle: {str(e)}")
            
            await asyncio.sleep(self.scan_interval)

    async def _execute_cycle(self):
        self.scan_count += 1
        self.last_scan_time = datetime.datetime.utcnow()

        # 1. Manage SL/TP triggers for Paper positions
        if self.trading_mode == "PAPER":
            closed = await paper_engine.evaluate_open_positions_triggers()
            for c in closed:
                pnl = c.get('pnl', 0.0)
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                self.log_event("INFO", f"Trigger Closed Trade #{c['id']} ({c['symbol']}): {pnl_str} ({c['exit_reason']})", meta=c)
                self.broadcast_event({"type": "TRADE_CLOSED", "data": c})

        # 2. Get active strategies (multi-strategy support: each applies to its own symbols)
        strategies = await strategy_engine.get_active_strategies()

        # 3. Iterate strategies, then their assigned symbols (or all watchlist if none assigned)
        for strategy in strategies:
            timeframe = strategy.timeframe if strategy else "15m"
            target_symbols = [s for s in self.watchlist if strategy_engine.strategy_applies_to(strategy, s, self.watchlist)]

            for symbol in target_symbols:
                try:
                    # Fetch recent candles
                    df = await binance_client.fetch_klines(symbol, timeframe=timeframe, limit=100)
                    if df.empty:
                        continue

                    # Evaluate strategy signals
                    signal = await strategy_engine.evaluate_symbol(symbol, df, strategy)
                    signal["strategy"] = strategy.name if strategy else "ai_adaptive_momentum"
                    self.latest_signals[symbol] = signal

                    # Broadcast signal update
                    self.broadcast_event({"type": "SIGNAL_UPDATE", "data": signal})

                    # Check if actionable BUY or SELL signal
                    if signal.get("action") == "BUY" and signal.get("confidence", 0) >= 0.65:
                        await self._handle_buy_signal(symbol, signal, strategy)

                except Exception as e:
                    logger.error(f"Error analyzing symbol {symbol} ({strategy.name if strategy else '?'}): {e}")

        # Broadcast general tick
        self.broadcast_event({
            "type": "TICK",
            "data": {
                "scan_count": self.scan_count,
                "timestamp": self.last_scan_time.isoformat(),
                "mode": self.trading_mode,
                "running": self.is_running
            }
        })

    async def _handle_buy_signal(self, symbol: str, signal: dict[str, Any], strategy: Any):
        # 1. Check risk manager validation
        risk_check = await risk_manager.can_open_trade(
            symbol=symbol,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            max_daily_loss_pct=settings.MAX_DAILY_LOSS_PERCENT
        )

        if not risk_check["allowed"]:
            self.log_event("WARNING", f"BUY signal on {symbol} skipped: {risk_check['reason']}")
            return

        # 2. Calculate position size
        current_price = signal.get("indicators", {}).get("price", 0.0)
        sl_pct = signal.get("stop_loss_pct", settings.DEFAULT_STOP_LOSS_PCT)
        tp_pct = signal.get("take_profit_pct", settings.DEFAULT_TAKE_PROFIT_PCT)
        trailing_pct = signal.get("trailing_stop_pct", 1.0)

        if self.trading_mode == "PAPER":
            balance = await paper_engine.get_balance("USDT")
            size_usdt = await risk_manager.calculate_position_size(
                total_balance=balance,
                entry_price=current_price,
                stop_loss_pct=sl_pct,
                risk_per_trade_pct=settings.RISK_PER_TRADE_PERCENT
            )

            if size_usdt <= 10.0 or size_usdt > balance:
                self.log_event("WARNING", f"Cannot open trade on {symbol}: Calculated size ${size_usdt:.2f} invalid for balance ${balance:.2f}")
                return

            res = await paper_engine.open_position(
                symbol=symbol,
                side="BUY",
                amount_usdt=size_usdt,
                strategy_name=strategy.name if strategy else "AI_Adaptive",
                strategy_version=strategy.version if strategy else 1,
                entry_reason=signal.get("reason", "Strategy Buy Signal"),
                stop_loss_pct=sl_pct,
                take_profit_pct=tp_pct,
                trailing_stop_pct=trailing_pct,
                technical_snapshot=signal.get("indicators", {})
            )

            if res.get("success"):
                trade = res["trade"]
                self.log_event("INFO", f"OPENED PAPER BUY {symbol}: ${size_usdt:.2f} @ ${trade['entry_price']:.4f} (SL: ${trade['stop_loss'] or 0:.4f}, TP: ${trade['take_profit'] or 0:.4f})", meta=trade)
                self.broadcast_event({"type": "TRADE_OPENED", "data": trade})
            else:
                self.log_event("ERROR", f"Failed to open paper trade on {symbol}: {res.get('error')}")

        elif self.trading_mode == "REAL":
            real_bal = await binance_client.fetch_real_balance()
            if not real_bal.get("success"):
                self.log_event("ERROR", f"Real trade failed: Cannot fetch Binance balance: {real_bal.get('error')}")
                return

            usdt_bal = next((b["free"] for b in real_bal.get("balances", []) if b["asset"] == "USDT"), 0.0)
            size_usdt = await risk_manager.calculate_position_size(
                total_balance=usdt_bal,
                entry_price=current_price,
                stop_loss_pct=sl_pct,
                risk_per_trade_pct=settings.RISK_PER_TRADE_PERCENT
            )

            if size_usdt < 15.0 or size_usdt > usdt_bal:
                self.log_event("WARNING", f"Real trade skipped: Insufficient USDT balance (${usdt_bal:.2f})")
                return

            qty = size_usdt / current_price
            order_res = await binance_client.place_real_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                quantity=qty
            )

            if order_res.get("success"):
                async with AsyncSessionLocal() as session:
                    trade = Trade(
                        symbol=symbol,
                        side="BUY",
                        order_type="MARKET",
                        mode="REAL",
                        quantity=qty,
                        entry_price=order_res.get("price", current_price),
                        amount_usdt=size_usdt,
                        fees=size_usdt * 0.00075,
                        stop_loss=current_price * (1 - sl_pct / 100.0),
                        take_profit=current_price * (1 + tp_pct / 100.0),
                        status="OPEN",
                        strategy_name=strategy.name if strategy else "AI_Adaptive",
                        entry_reason=signal.get("reason", "Real Trading Signal"),
                        technical_snapshot=str(signal.get("indicators", {})),
                        entry_time=datetime.datetime.utcnow()
                    )
                    session.add(trade)
                    await session.commit()
                    await session.refresh(trade)
                    self.log_event("INFO", f"REAL ORDER FILLED on Binance for {symbol}: qty={qty:.6f} @ ${trade.entry_price:.4f}", meta=trade.to_dict())
                    self.broadcast_event({"type": "TRADE_OPENED", "data": trade.to_dict()})
            else:
                self.log_event("ERROR", f"Binance Real Order Failed for {symbol}: {order_res.get('error')}")

    def get_status(self) -> dict[str, Any]:
        return {
            "is_running": self.is_running,
            "trading_mode": self.trading_mode,
            "scan_interval": self.scan_interval,
            "scan_count": self.scan_count,
            "last_scan_time": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "watchlist": self.watchlist,
            "signals": self.latest_signals,
            "recent_logs": self.recent_logs[:20]
        }


bot_runner = BotRunner()
