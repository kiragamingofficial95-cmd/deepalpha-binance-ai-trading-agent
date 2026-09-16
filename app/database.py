import datetime
import json
from typing import AsyncGenerator, Any, Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, Index, select, desc
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    symbol = Column(String(20), index=True, nullable=False)
    side = Column(String(10), nullable=False)  # BUY or SELL
    order_type = Column(String(20), default="MARKET")
    mode = Column(String(10), default="PAPER", index=True)  # PAPER or REAL
    
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    amount_usdt = Column(Float, nullable=False)
    
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    fees = Column(Float, default=0.0)
    
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    trailing_stop_pct = Column(Float, nullable=True)
    highest_price = Column(Float, nullable=True)
    lowest_price = Column(Float, nullable=True)
    
    status = Column(String(20), default="OPEN", index=True)  # OPEN, CLOSED, CANCELLED, REJECTED
    strategy_name = Column(String(50), default="AI_Adaptive", index=True)
    strategy_version = Column(Integer, default=1)
    
    entry_reason = Column(Text, default="")
    exit_reason = Column(Text, default="")
    exit_reason_enum = Column(String(30), nullable=True)  # TP, SL, BREAKEVEN, TRAILING_STOP, MANUAL_PAPER_CLOSE, TIMEOUT, REJECTED_RR, REJECTED_LLM, REJECTED_CORRELATED, REJECTED_DUPLICATE
    technical_snapshot = Column(Text, default="{}")  # JSON of indicators at entry
    
    entry_time = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    exit_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # RR tracking
    structural_rr = Column(Float, nullable=True)  # Theoretical RR from market structure
    estimated_executable_rr = Column(Float, nullable=True)  # Pre-fill estimated RR
    executable_rr = Column(Float, nullable=True)  # Actual RR after fill (authoritative)
    realized_r = Column(Float, nullable=True)  # Actual closed PnL / initial risk
    
    # MFE/MAE tracking
    mfe = Column(Float, default=0.0)  # Maximum favorable excursion (USD)
    mae = Column(Float, default=0.0)  # Maximum adverse excursion (USD)
    mfe_r = Column(Float, default=0.0)  # MFE in R units
    mae_r = Column(Float, default=0.0)  # MAE in R units
    
    # Setup tracking
    setup_id = Column(String(100), index=True, nullable=True)
    correlation_group = Column(String(30), nullable=True)
    
    # Risk tracking
    initial_risk_usd = Column(Float, nullable=True)
    risk_usd = Column(Float, nullable=True)
    fees_estimate = Column(Float, nullable=True)
    slippage_estimate = Column(Float, nullable=True)
    
    # LLM tracking
    llm_decision = Column(String(10), nullable=True)  # PASS, FAIL, WAIT
    llm_confidence = Column(Float, nullable=True)
    llm_model = Column(String(50), nullable=True)
    llm_reasoning = Column(Text, nullable=True)
    llm_prompt_version = Column(String(20), nullable=True)
    
    # Rule engine tracking (for LLM impact analysis)
    rule_engine_decision = Column(String(10), nullable=True)  # PASS, FAIL
    
    # Market state snapshot
    market_state_snapshot = Column(Text, nullable=True)  # JSON of 4H/1H/15M/5M data
    
    # Timing
    confirmation_candle_timestamp = Column(DateTime, nullable=True)
    
    # Break-even tracking
    be_triggered = Column(Boolean, default=False)
    be_timestamp = Column(DateTime, nullable=True)
    be_price = Column(Float, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "mode": self.mode,
            "quantity": round(self.quantity, 6),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4) if self.exit_price else None,
            "amount_usdt": round(self.amount_usdt, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "fees": round(self.fees, 4),
            "stop_loss": round(self.stop_loss, 4) if self.stop_loss else None,
            "take_profit": round(self.take_profit, 4) if self.take_profit else None,
            "status": self.status,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "entry_reason": self.entry_reason,
            "exit_reason": self.exit_reason,
            "exit_reason_enum": self.exit_reason_enum,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "duration_minutes": (
                round((self.exit_time - self.entry_time).total_seconds() / 60, 1)
                if self.exit_time and self.entry_time else None
            ),
            # New fields
            "structural_rr": round(self.structural_rr, 2) if self.structural_rr else None,
            "estimated_executable_rr": round(self.estimated_executable_rr, 2) if self.estimated_executable_rr else None,
            "executable_rr": round(self.executable_rr, 2) if self.executable_rr else None,
            "realized_r": round(self.realized_r, 2) if self.realized_r else None,
            "mfe": round(self.mfe, 2),
            "mae": round(self.mae, 2),
            "mfe_r": round(self.mfe_r, 2),
            "mae_r": round(self.mae_r, 2),
            "setup_id": self.setup_id,
            "correlation_group": self.correlation_group,
            "initial_risk_usd": round(self.initial_risk_usd, 2) if self.initial_risk_usd else None,
            "risk_usd": round(self.risk_usd, 2) if self.risk_usd else None,
            "llm_decision": self.llm_decision,
            "llm_confidence": self.llm_confidence,
            "llm_model": self.llm_model,
            "rule_engine_decision": self.rule_engine_decision,
            "be_triggered": self.be_triggered,
        }


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String(50), default="default", index=True)
    role = Column(String(20), nullable=False)  # user, assistant, system, tool
    content = Column(Text, nullable=False)
    tool_calls = Column(Text, nullable=True)  # JSON representation of tool calls
    tool_results = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tool_calls": json.loads(self.tool_calls) if self.tool_calls else None,
            "tool_results": json.loads(self.tool_results) if self.tool_results else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


class StrategyMemory(Base):
    __tablename__ = "strategy_memories"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    category = Column(String(50), default="insight")  # insight, rule, parameter_tuning, lesson, mistake
    content = Column(Text, nullable=False)
    market_condition = Column(String(50), default="ALL")  # BULLISH, BEARISH, RANGING, HIGH_VOLATILITY, ALL
    confidence_score = Column(Float, default=0.8)
    applied_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "content": self.content,
            "market_condition": self.market_condition,
            "confidence_score": round(self.confidence_score, 2),
            "applied_count": self.applied_count,
            "success_rate": round(self.success_rate, 2),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    is_active = Column(Boolean, default=False)
    timeframe = Column(String(10), default="15m")
    
    # JSON parameters
    parameters = Column(Text, default="{}")
    custom_prompt = Column(Text, default="")
    risk_settings = Column(Text, default="{}")
    symbols = Column(Text, default="[]")  # JSON list of target symbols; [] = all watchlist
    
    # Correlation settings
    correlation_group = Column(String(30), default="CRYPTO_MAJOR")
    max_correlated_risk_pct = Column(Float, default=3.0)  # Max % of equity in correlated positions
    
    # LLM settings
    llm_prompt_version = Column(String(20), default="v1")
    llm_model = Column(String(50), nullable=True)
    llm_temperature = Column(Float, default=0.2)
    
    version = Column(Integer, default=1)
    total_trades = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "is_active": self.is_active,
            "timeframe": self.timeframe,
            "parameters": json.loads(self.parameters) if self.parameters else {},
            "custom_prompt": self.custom_prompt,
            "risk_settings": json.loads(self.risk_settings) if self.risk_settings else {},
            "symbols": json.loads(self.symbols) if self.symbols else [],
            "correlation_group": self.correlation_group,
            "max_correlated_risk_pct": self.max_correlated_risk_pct,
            "llm_prompt_version": self.llm_prompt_version,
            "llm_model": self.llm_model,
            "llm_temperature": self.llm_temperature,
            "version": self.version,
            "total_trades": self.total_trades,
            "win_count": self.win_count,
            "win_rate": round((self.win_count / self.total_trades * 100), 2) if self.total_trades > 0 else 0.0,
            "total_pnl": round(self.total_pnl, 2),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class SettingKV(Base):
    __tablename__ = "settings_kv"

    key = Column(String(100), primary_key=True, index=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class PaperBalance(Base):
    __tablename__ = "paper_balances"

    asset = Column(String(20), primary_key=True, index=True)
    free = Column(Float, default=10000.0)
    locked = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "free": round(self.free, 4),
            "locked": round(self.locked, 4),
            "total": round(self.free + self.locked, 4),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Lightweight migration: add missing columns
        def _migrate_all(sync_conn):
            # strategy_configs columns
            cols = sync_conn.exec_driver_sql("PRAGMA table_info(strategy_configs)").fetchall()
            col_names = [c[1] for c in cols]
            if "symbols" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN symbols TEXT DEFAULT '[]'")
            if "correlation_group" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN correlation_group TEXT DEFAULT 'CRYPTO_MAJOR'")
            if "max_correlated_risk_pct" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN max_correlated_risk_pct REAL DEFAULT 3.0")
            if "llm_prompt_version" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN llm_prompt_version TEXT DEFAULT 'v1'")
            if "llm_model" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN llm_model TEXT")
            if "llm_temperature" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE strategy_configs ADD COLUMN llm_temperature REAL DEFAULT 0.2")
            
            # trades columns
            cols = sync_conn.exec_driver_sql("PRAGMA table_info(trades)").fetchall()
            col_names = [c[1] for c in cols]
            new_trade_cols = {
                "lowest_price": "REAL",
                "exit_reason_enum": "TEXT",
                "structural_rr": "REAL",
                "estimated_executable_rr": "REAL",
                "executable_rr": "REAL",
                "realized_r": "REAL",
                "mfe": "REAL DEFAULT 0.0",
                "mae": "REAL DEFAULT 0.0",
                "mfe_r": "REAL DEFAULT 0.0",
                "mae_r": "REAL DEFAULT 0.0",
                "setup_id": "TEXT",
                "correlation_group": "TEXT",
                "initial_risk_usd": "REAL",
                "risk_usd": "REAL",
                "fees_estimate": "REAL",
                "slippage_estimate": "REAL",
                "llm_decision": "TEXT",
                "llm_confidence": "REAL",
                "llm_model": "TEXT",
                "llm_reasoning": "TEXT",
                "llm_prompt_version": "TEXT",
                "rule_engine_decision": "TEXT",
                "market_state_snapshot": "TEXT",
                "confirmation_candle_timestamp": "TIMESTAMP",
                "be_triggered": "BOOLEAN DEFAULT 0",
                "be_timestamp": "TIMESTAMP",
                "be_price": "REAL",
            }
            for col_name, col_type in new_trade_cols.items():
                if col_name not in col_names:
                    sync_conn.exec_driver_sql(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
        
        await conn.run_sync(_migrate_all)
    
    # Initialize default strategies and paper balance if not existing
    async with AsyncSessionLocal() as session:
        # Check Groq API key setting
        gk_res = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_API_KEY"))
        gk_row = gk_res.scalar_one_or_none()
        if not gk_row or not gk_row.value:
            if gk_row:
                gk_row.value = settings.GROQ_API_KEY
            else:
                session.add(SettingKV(key="GROQ_API_KEY", value=settings.GROQ_API_KEY))

        gm_res = await session.execute(select(SettingKV).where(SettingKV.key == "GROQ_MODEL"))
        gm_row = gm_res.scalar_one_or_none()
        if not gm_row or not gm_row.value:
            if gm_row:
                gm_row.value = settings.GROQ_MODEL
            else:
                session.add(SettingKV(key="GROQ_MODEL", value=settings.GROQ_MODEL))
        await session.commit()

        # Check USDT balance
        res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
        if not res.scalar_one_or_none():
            session.add(PaperBalance(asset="USDT", free=10000.0, locked=0.0))
            await session.commit()
            
        # Check default strategies
        strat_res = await session.execute(select(StrategyConfig))
        if not strat_res.scalars().first():
            default_strategies = [
                StrategyConfig(
                    name="ict_market_mechanics",
                    display_name="ICT / Market Mechanics Scalping Framework",
                    description="Rule-based ICT framework using market structure, liquidity sweeps, order blocks, FVGs, Volume Profile, and multi-timeframe analysis. Strict A+ checklist with 2R minimum, set-and-forget management.",
                    is_active=True,
                    timeframe="15m",
                    parameters=json.dumps({
                        "swing_lookback": 3,
                        "entry_models": ["FLIP_EM", "MS_EM"],
                        "min_rr_to_tp1": 2.0,
                        "max_daily_trades": 2,
                        "max_daily_losses": 2,
                        "risk_amount_inr": 10,
                        "max_leverage": 10,
                        "margin_mode": "ISOLATED",
                        "order_type": "LIMIT_ONLY",
                        "no_session_restriction": True,
                        "volume_profile_bins": 15
                    }),
                    custom_prompt="Follow the ICT Market Mechanics Scalping Framework exactly. 4H macro sanity, 1H bias+structure, 15M POI (OB/FVG) with Volume Profile confluence, 5M A+ checklist, 1M entry on candle close only. Minimum 2R to TP1. Set-and-forget. Max 2 trades/day, max 2 losses/day.",
                    risk_settings=json.dumps({
                        "risk_per_trade_pct": 2.0,
                        "stop_loss_pct": 1.5,
                        "take_profit_pct": 3.0,
                        "trailing_stop_pct": 1.0,
                        "risk_amount_usd": 10.0
                    }),
                    correlation_group="CRYPTO_MAJOR",
                    max_correlated_risk_pct=3.0,
                    llm_prompt_version="v1",
                    llm_model="qwen/qwen3.8-27b",
                    llm_temperature=0.2
                ),
                StrategyConfig(
                    name="ai_adaptive_momentum",
                    display_name="AI Adaptive Momentum & Trend",
                    description="Dynamically evaluates RSI, MACD, EMA 20/50 crossovers and market regime with Groq AI self-tuning.",
                    is_active=False,
                    timeframe="15m",
                    parameters=json.dumps({
                        "rsi_period": 14,
                        "rsi_oversold": 32,
                        "rsi_overbought": 68,
                        "macd_fast": 12,
                        "macd_slow": 26,
                        "macd_signal": 9,
                        "ema_fast": 20,
                        "ema_slow": 50,
                        "ema_trend": 200,
                        "atr_multiplier_sl": 1.8,
                        "atr_multiplier_tp": 3.2,
                        "use_ai_filter": True
                    }),
                    custom_prompt="Focus on strong trend continuation with confirmation from RSI divergence and volume expansion.",
                    risk_settings=json.dumps({
                        "risk_per_trade_pct": 2.0,
                        "stop_loss_pct": 1.5,
                        "take_profit_pct": 3.0,
                        "trailing_stop_pct": 1.0
                    }),
                    correlation_group="CRYPTO_MAJOR",
                    max_correlated_risk_pct=3.0,
                    llm_prompt_version="v1",
                    llm_model="qwen/qwen3.8-27b",
                    llm_temperature=0.2
                ),
                StrategyConfig(
                    name="bollinger_mean_reversion",
                    display_name="Bollinger Band Mean Reversion",
                    description="Captures over-extended moves at outer 2-standard-deviation bands during ranging markets.",
                    is_active=False,
                    timeframe="15m",
                    parameters=json.dumps({
                        "bb_period": 20,
                        "bb_std": 2.0,
                        "rsi_filter": True,
                        "rsi_low": 30,
                        "rsi_high": 70
                    }),
                    custom_prompt="Trigger buy on lower band touch with bullish candle reversal; exit at middle/upper band.",
                    risk_settings=json.dumps({
                        "risk_per_trade_pct": 1.5,
                        "stop_loss_pct": 1.2,
                        "take_profit_pct": 2.4,
                        "trailing_stop_pct": 0.8
                    }),
                    correlation_group="CRYPTO_MAJOR",
                    max_correlated_risk_pct=3.0,
                    llm_prompt_version="v1",
                    llm_model="qwen/qwen3.8-27b",
                    llm_temperature=0.2
                ),
                StrategyConfig(
                    name="grid_scalper",
                    display_name="Multi-Level Grid Scalper",
                    description="Deploys automated multi-tier limit grids to capture crypto volatility in tight ranges.",
                    is_active=False,
                    timeframe="5m",
                    parameters=json.dumps({
                        "grid_levels": 5,
                        "grid_spacing_pct": 0.6,
                        "take_profit_per_grid": 0.8
                    }),
                    custom_prompt="Accumulate in dips, trim in surges.",
                    risk_settings=json.dumps({
                        "risk_per_trade_pct": 1.0,
                        "stop_loss_pct": 2.5,
                        "take_profit_pct": 1.5
                    }),
                    correlation_group="CRYPTO_MAJOR",
                    max_correlated_risk_pct=3.0,
                    llm_prompt_version="v1",
                    llm_model="qwen/qwen3.8-27b",
                    llm_temperature=0.2
                )
            ]
            session.add_all(default_strategies)
            
            # Initial AI Strategy Memories & Lessons
            initial_memories = [
                StrategyMemory(
                    title="BTC Trend Filter Rule",
                    category="rule",
                    content="Never take aggressive BUY signals when price is trading strictly below 200 EMA on the 1H timeframe.",
                    market_condition="BEARISH",
                    confidence_score=0.92,
                    applied_count=14,
                    success_rate=78.5
                ),
                StrategyMemory(
                    title="High Volatility Breakout Confirmation",
                    category="insight",
                    content="Breakout entries yield 35% higher win rate when volume is at least 1.8x the 20-period volume moving average.",
                    market_condition="HIGH_VOLATILITY",
                    confidence_score=0.88,
                    applied_count=9,
                    success_rate=81.0
                ),
                StrategyMemory(
                    title="Risk-to-Reward Ratio Discipline",
                    category="rule",
                    content="Always enforce a minimum 1.8:1 TP to SL ratio to maintain positive expectancy even during 45% win rate periods.",
                    market_condition="ALL",
                    confidence_score=0.95,
                    applied_count=25,
                    success_rate=85.0
                )
            ]
            session.add_all(initial_memories)
            await session.commit()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
