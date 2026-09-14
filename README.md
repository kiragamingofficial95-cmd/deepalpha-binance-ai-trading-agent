# DEEPALPHA AI — 24/7 Autonomous Binance Trading Agent & Strategy Optimizer

An institutional-grade, 24/7 autonomous cryptocurrency trading agent and real-time strategy optimization platform powered by **Groq LLM (`llama-3.3-70b-versatile`)**, **FastAPI**, **TradingView Lightweight Charts**, and **CCXT**.

---

## Key Features

1. **Dual Trading Modes**:
   - **Paper Trading**: Realistic exchange matching engine with real Binance market prices, 0.075% standard maker/taker fees, simulated slippage (0.02%), stop loss/take profit triggers, trailing stops, and a starting portfolio of $10,000 USDT (resettable at any time).
   - **Real Binance Trading**: Live spot & futures order execution on Binance API (with Testnet & Mainnet support).

2. **24/7 Autonomous Engine**:
   - Continuous market scanner monitoring high-liquidity pairs (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `ADAUSDT`).
   - Automated signal confluence evaluation using vectorized technical indicators (RSI, MACD, Bollinger Bands, EMA 20/50/200, ATR, Supertrend, Stochastic RSI, Volume Analysis).
   - Real-time Stop-Loss, Take-Profit, and Trailing Stop management.

3. **Groq AI Strategy Coach with Memory & Self-Tuning**:
   - Powered by ultra-fast Groq LLM inference (`llama-3.3-70b-versatile`).
   - **Persistent Memory Bank**: Synthesizes lessons, rules, and post-mortem insights from every trade.
   - **Dynamic Tool Execution**: The AI inspects past trade logs, analyzes win/loss metrics, and dynamically updates active strategy parameters (RSI bounds, EMA spans, Stop-Loss / Take-Profit ratios).

4. **Institutional Risk Management Safeguards**:
   - **Daily Drawdown Circuit Breaker**: Halts automated trading if daily loss exceeds a configurable threshold (e.g. 5%).
   - **Dynamic Position Sizing**: Fixed Fractional Risk sizing based on distance to stop-loss.
   - **Concentration Caps & Exposure Limits**: Max open positions guard and symbol duplication prevention.

5. **Glassmorphic Interactive Dashboard**:
   - Live TradingView Candlestick chart with dynamic timeframe switching (5m, 15m, 1h, 4h, 1d).
   - Real-time WebSocket event streaming for instant trade alerts, price updates, and execution logs.
   - In-depth Quantitative Analytics: Win Rate %, Profit Factor, Sharpe Ratio, Max Drawdown %, Average Win/Loss, and Cumulative Equity Curve.
   - Secure Auth tab for Binance API key and Groq API key setup.

---

## Quick Start (Local)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Application
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **http://localhost:8000** in your browser.

---

## Railway 24/7 Hosting

Deploying to Railway for 24/7 continuous operation takes one command:

```bash
railway up
```

Railway automatically detects the `Dockerfile` and `railway.json` and runs the bot 24/7.
You can configure environment variables (`GROQ_API_KEY`, `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`) in the Railway dashboard or directly inside the web UI.

---

## Architecture Overview

```
TRADING_AGENT/
├── app/
│   ├── config.py             # Settings and environment variables
│   ├── database.py           # SQLAlchemy async models & SQLite persistence
│   ├── indicators.py         # Vectorized technical indicator suite (RSI, MACD, BB, EMA, ATR)
│   ├── binance_client.py     # Multi-gateway Binance REST & WebSocket connector
│   ├── paper_engine.py       # Simulated exchange matching engine with real market fills
│   ├── risk_manager.py       # Circuit breaker & dynamic position sizing
│   ├── strategy_engine.py    # Algorithmic strategies & signal evaluator
│   ├── ai_agent.py           # Groq AI LLM with memory & tool calling
│   ├── bot_runner.py         # 24/7 async execution loop & WebSocket broadcaster
│   ├── analytics.py          # Quantitative performance & equity curve calculator
│   ├── main.py               # FastAPI application & REST/WebSocket routes
│   └── templates/
│       └── index.html        # Glassmorphic UI Dashboard
├── static/
│   ├── css/dashboard.css     # Custom sleek dark styling & animations
│   └── js/
│       ├── chart.js          # TradingView Lightweight Charts integration
│       ├── chat.js           # Groq AI strategy chat & memory manager
│       └── app.js            # Dashboard orchestration & WebSocket client
├── Dockerfile                # Production Docker container
├── railway.json              # Railway deployment config
├── Procfile                  # PaaS web process command
└── requirements.txt          # Python dependencies
```
