import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="allow")
    
    APP_NAME: str = "DeepAlpha AI - Binance Autonomous Trading System"
    ENV: str = os.getenv("ENV", "production")
    PORT: int = int(os.getenv("PORT", "8000"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    
    # Security / Auth
    DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_PASSWORD", "")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    
    # Binance Default Config (can also be configured & stored in DB via Web UI)
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    TRADING_MODE: str = os.getenv("TRADING_MODE", "PAPER")
    ALLOW_REAL_TRADING: bool = os.getenv("ALLOW_REAL_TRADING", "false").lower() == "true"
    
    # Groq AI Config
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{DATA_DIR / 'trading_agent.db'}"
    )

    # Bot Runner Engine Settings
    DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT"]
    SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    RISK_PER_TRADE_PERCENT: float = float(os.getenv("RISK_PER_TRADE_PERCENT", "2.0")) # 2% of balance
    MAX_DAILY_LOSS_PERCENT: float = float(os.getenv("MAX_DAILY_LOSS_PERCENT", "5.0")) # 5% daily circuit breaker
    DEFAULT_STOP_LOSS_PCT: float = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "1.5")) # 1.5% SL
    DEFAULT_TAKE_PROFIT_PCT: float = float(os.getenv("DEFAULT_TAKE_PROFIT_PCT", "3.0")) # 3.0% TP (2:1 R:R)

    # Risk policy (hard rules — the LLM can never change these)
    MIN_EXECUTABLE_RR: float = float(os.getenv("MIN_EXECUTABLE_RR", "2.0"))  # 2R minimum, no maximum
    MAX_CORRELATED_RISK_PCT: float = float(os.getenv("MAX_CORRELATED_RISK_PCT", "3.0"))  # % equity at risk per correlation group
    MAX_PORTFOLIO_RISK_PCT: float = float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "6.0"))  # % equity at risk across all open trades

    # Market data quality
    # Synthetic/fallback market data must never silently create trades. Trading on
    # synthetic candles is only possible if explicitly enabled for safe simulation.
    ALLOW_SYNTHETIC_DATA_TRADING: bool = os.getenv("ALLOW_SYNTHETIC_DATA_TRADING", "false").lower() == "true"
    # Max age (in multiples of the candle interval) tolerated for the newest candle
    # before data is treated as stale and no trades may be taken.
    MARKET_DATA_MAX_AGE_INTERVALS: float = float(os.getenv("MARKET_DATA_MAX_AGE_INTERVALS", "3.0"))

    # Groq AI token budget controls (prevents HTTP 413 rate-limit errors on small-context tiers)
    GROQ_MAX_INPUT_TOKENS: int = int(os.getenv("GROQ_MAX_INPUT_TOKENS", "4500"))
    GROQ_HISTORY_MESSAGES: int = int(os.getenv("GROQ_HISTORY_MESSAGES", "6"))
    GROQ_MAX_TOKENS: int = int(os.getenv("GROQ_MAX_TOKENS", "1024"))
    GROQ_MEMORY_BANK_LIMIT: int = int(os.getenv("GROQ_MEMORY_BANK_LIMIT", "4"))
    GROQ_TOOL_RESULT_CHARS: int = int(os.getenv("GROQ_TOOL_RESULT_CHARS", "2000"))

    # LLM validator behaviour
    LLM_VALIDATION_TIMEOUT_SECONDS: float = float(os.getenv("LLM_VALIDATION_TIMEOUT_SECONDS", "20"))

settings = Settings()
