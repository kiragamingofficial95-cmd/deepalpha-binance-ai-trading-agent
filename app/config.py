import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseSettings):
    APP_NAME: str = "DeepAlpha AI - Binance Autonomous Trading System"
    ENV: str = os.getenv("ENV", "production")
    PORT: int = int(os.getenv("PORT", "8000"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    
    # Security / Auth
    DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_PASSWORD", "admin123")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "deepalpha_super_secret_jwt_key_2026_xyz")
    
    # Binance Default Config (can also be configured & stored in DB via Web UI)
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    TRADING_MODE: str = os.getenv("TRADING_MODE", "PAPER")  # PAPER or REAL
    
    # Groq AI Config (can also be updated via Web UI)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
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
    
    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()
