import os
import sys
import tempfile
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the project root is on the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Create a temporary database for all tests — never touch production data
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TEST_DB_PATH = _TMP_DB.name
_TMP_DB.close()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["BINANCE_API_KEY"] = ""
os.environ["BINANCE_SECRET_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["DASHBOARD_PASSWORD"] = ""
os.environ["SECRET_KEY"] = ""


@pytest.fixture(autouse=True)
def isolated_db():
    """Each test gets a fresh in-memory database. Never touches production."""
    from app.database import init_db
    asyncio.run(init_db())
    yield
    # Clean up test data after each test
    import aiosqlite
    async def _cleanup():
        conn = await aiosqlite.connect(TEST_DB_PATH)
        await conn.execute("DELETE FROM trades")
        await conn.execute("DELETE FROM paper_balances")
        await conn.execute("DELETE FROM strategy_configs")
        await conn.execute("DELETE FROM strategy_memories")
        await conn.execute("DELETE FROM settings_kv WHERE key NOT IN ('GROQ_API_KEY', 'GROQ_MODEL')")
        await conn.commit()
        await conn.close()
    asyncio.run(_cleanup())


@pytest.fixture(autouse=True)
def reset_paper_balance():
    """Reset paper balance to $10,000 before each test."""
    from app.database import AsyncSessionLocal, PaperBalance
    from sqlalchemy import select
    async def _reset():
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(PaperBalance).where(PaperBalance.asset == "USDT"))
            bal = res.scalar_one_or_none()
            if bal:
                bal.free = 10000.0
                bal.locked = 0.0
            else:
                session.add(PaperBalance(asset="USDT", free=10000.0, locked=0.0))
            await session.commit()
    asyncio.run(_reset())


@pytest.fixture(autouse=True)
def mock_binance_fallback():
    """Prevent real Binance API calls in tests — return deterministic synthetic data."""
    with patch("app.binance_client.binance_client._http_client") as mock_http:
        import pandas as pd
        import numpy as np

        # Mock ticker response
        mock_ticker_data = {"symbol": "BTCUSDT", "price": 100000.0, "synthetic": True}
        mock_http.get.return_value = AsyncMock(
            status_code=200,
            json=lambda: {"lastPrice": 100000.0, "bidPrice": 99980.0, "askPrice": 100020.0,
                          "highPrice": 101000.0, "lowPrice": 99000.0, "volume": 15000.0,
                          "quoteVolume": 1500000000.0, "priceChangePercent": 1.0, "closeTime": 1234567890000}
        )

        yield mock_http


@pytest.fixture(autouse=True)
def mock_llm_wait():
    """Default LLM mock returns WAIT to prevent accidental trade execution."""
    with patch("app.ai_agent.ai_agent.evaluate_ict_setup_with_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = {
            "decision": "WAIT",
            "confidence": 0.0,
            "llm_reasoning": "Test mock: LLM returns WAIT by default",
            "concerns": ["TEST_MOCK"],
            "model": "test_mock"
        }
        yield mock_llm


# Helper to create deterministic candle data
def make_candles(price=100.0, count=50, interval_ms=900000, trend="flat", volatility=0.01):
    """Create deterministic OHLCV candles for testing."""
    import pandas as pd
    import numpy as np
    import time
    
    now_ms = int(time.time() * 1000)
    timestamps = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    
    p = price
    for i in range(count):
        if trend == "up":
            change = volatility * price * (0.5 + np.random.random() * 0.5)
        elif trend == "down":
            change = -volatility * price * (0.5 + np.random.random() * 0.5)
        else:
            change = volatility * price * (np.random.random() - 0.5) * 2
        
        o = p
        c = p + change
        h = max(o, c) * (1 + abs(change) / price * 0.3)
        l = min(o, c) * (1 - abs(change) / price * 0.3)
        v = 1000.0 + np.random.random() * 500
        
        ts = now_ms - (count - i) * interval_ms
        timestamps.append(ts)
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        volumes.append(v)
        p = c
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df.attrs["synthetic"] = False
    return df


@pytest.fixture
def fresh_event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
