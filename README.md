# DEEPALPHA AI — 24/7 Autonomous Binance Trading Agent & Strategy Optimizer

An institutional-grade, 24/7 autonomous cryptocurrency trading agent and real-time strategy optimization platform powered by **Groq LLM (`llama-3.3-70b-versatile`)**, **FastAPI**, **TradingView Lightweight Charts**, and **CCXT**.

---

## 🚀 Live Demo & Links

- **GitHub Repository**: [https://github.com/kiragamingofficial95-cmd/deepalpha-binance-ai-trading-agent](https://github.com/kiragamingofficial95-cmd/deepalpha-binance-ai-trading-agent)
- **Live Railway Deployment**: [https://deepalpha-trading-agent-production.up.railway.app](https://deepalpha-trading-agent-production.up.railway.app)

---

## 🌟 Key Features

1. **Dual Trading Modes**:
   - **Paper Trading Engine**: High-fidelity matching engine with real live Binance order books, realistic taker fees (0.075%), slippage simulation (0.02%), automated SL/TP & trailing stops, and a starting balance of $10,000 USDT (resettable at any time).
   - **Real Binance Trading**: Live spot & futures order execution on Binance API (with Testnet & Mainnet support).

2. **24/7 Autonomous Execution Engine**:
   - Background async worker continuously scanning watchlist pairs (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `ADAUSDT`).
   - Automated signal confluence evaluation using vectorized technical indicators (RSI, MACD, Bollinger Bands, EMA 20/50/200, ATR, Supertrend, Stochastic RSI, Volume Analysis).
   - Real-time Stop-Loss, Take-Profit, and Trailing Stop triggers.

3. **Groq AI Strategy Coach with Memory & Self-Tuning**:
   - Powered by ultra-fast Groq LLM inference (`llama-3.3-70b-versatile`).
   - **Persistent Memory Bank**: Synthesizes lessons, rules, and post-mortem insights from past trades into long-term storage.
   - **Dynamic Tool Calling**: The AI inspects trade logs, analyzes win/loss metrics, and dynamically updates active strategy parameters (RSI bounds, EMA spans, Stop-Loss / Take-Profit ratios).

4. **Institutional Risk Management Safeguards**:
   - **Daily Drawdown Circuit Breaker**: Halts automated trading if daily loss exceeds a configurable threshold (e.g. 5%).
   - **Dynamic Position Sizing**: Fixed Fractional Risk sizing based on distance to stop-loss.
   - **Concentration Caps & Exposure Limits**: Max open positions guard and symbol duplication prevention.

5. **Glassmorphic Interactive Dashboard**:
   - Live TradingView Candlestick chart with dynamic timeframe switching (5m, 15m, 1h, 4h, 1d).
   - Real-time WebSocket event streaming for instant trade alerts, price updates, and execution logs.
   - In-depth Quantitative Analytics: Win Rate %, Profit Factor, Sharpe Ratio, Max Drawdown %, Average Win/Loss, and Cumulative Equity Curve.
   - Secure Setup tab for Binance API keys and Groq API key configuration.

---

## 🌐 How to Deploy on Render (Step-by-Step Guide)

Deploying to [Render](https://render.com) ensures your AI Trading Agent runs **24/7 non-stop** in the cloud.

### Method 1: Deploy with Render Blueprint (Recommended - 1 Click)

1. Go to your [Render Dashboard](https://dashboard.render.com).
2. Click **New +** in the top-right corner and select **Blueprint**.
3. Connect your GitHub repository: `https://github.com/kiragamingofficial95-cmd/deepalpha-binance-ai-trading-agent`.
4. Render will automatically read the `render.yaml` file from the repo and configure:
   - **Runtime**: Docker
   - **Service Name**: `deepalpha-trading-agent`
   - **Persistent Disk**: `/app/data` (1 GB for SQLite database & memory persistence)
   - **Environment Variables**: Default bot parameters
5. Click **Apply**.
6. Once deployed, click on your service's `.onrender.com` URL to open your live dashboard!

---

### Method 2: Manual Web Service Setup on Render

If you prefer setting up the Web Service manually:

#### Step 1: Create a New Web Service
1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** → **Web Service**.
3. Select **Build and deploy from a Git repository** and connect:
   ```
   https://github.com/kiragamingofficial95-cmd/deepalpha-binance-ai-trading-agent
   ```

#### Step 2: Configure Service Settings
- **Name**: `deepalpha-trading-agent`
- **Region**: Select closest to you (e.g., `Oregon (US West)` or `Frankfurt (EU)`)
- **Branch**: `main`
- **Language / Runtime**: **Docker**
  *(Render will automatically build using the included `Dockerfile`)*
- **Instance Type**: **Starter** or **Standard** (ensures 24/7 uptime without sleeping)

*(If using Native Python runtime instead of Docker)*:
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

#### Step 3: Add Environment Variables
In the **Environment Variables** section, add the following:

| Variable | Value | Description |
|---|---|---|
| `PORT` | `8000` | Application port |
| `ENV` | `production` | Production environment mode |
| `TRADING_MODE` | `PAPER` | Default mode: `PAPER` or `REAL` |
| `GROQ_API_KEY` | *(Optional)* `gsk_...` | Groq API Key (or enter via Web UI) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model for Strategy AI |
| `BINANCE_API_KEY` | *(Optional)* | Your Binance API key (or enter via Web UI) |
| `BINANCE_SECRET_KEY` | *(Optional)* | Your Binance Secret key (or enter via Web UI) |
| `BINANCE_TESTNET` | `false` | Set `true` if using Spot Testnet |
| `SCAN_INTERVAL_SECONDS` | `15` | Scanning frequency in seconds |
| `MAX_OPEN_POSITIONS` | `5` | Maximum concurrent open positions |
| `RISK_PER_TRADE_PERCENT`| `2.0` | Max risk % per trade |
| `MAX_DAILY_LOSS_PERCENT`| `5.0` | Daily loss circuit breaker % |

#### Step 4: Add Persistent Disk (Optional but Recommended)
To preserve AI memories, custom strategies, and trade history across restarts:
1. Under **Disks**, click **Add Disk**.
2. **Name**: `trading-agent-data`
3. **Mount Path**: `/app/data`
4. **Size**: `1 GB`

#### Step 5: Deploy & Launch
1. Click **Create Web Service**.
2. Render will build the container and output live deployment logs.
3. Open your custom Render domain (`https://deepalpha-trading-agent.onrender.com`).

---

## 🛠️ Setup & Configuration in the Web UI

Once your application is running on Render, Railway, or locally:

1. **Access the Web Dashboard**:
   - Open your app URL in your browser.
2. **Configure Groq AI (Strategy Coach & Memory)**:
   - Navigate to the **Binance & Groq Setup** tab.
   - Enter your [Groq API Key](https://console.groq.com/keys) (`gsk_...`).
   - Select `llama-3.3-70b-versatile` and click **Connect Groq AI**.
3. **Configure Binance API (For Real or Testnet Trading)**:
   - In the **Binance & Groq Setup** tab, paste your Binance API Key and Secret.
   - Toggle **Use Binance Testnet** if testing with paper testnet API keys.
   - Click **Validate & Save Binance Credentials**.
4. **Select Trading Mode**:
   - In the top header bar, switch between **PAPER TRADING** and **REAL BINANCE**.
5. **Start the 24/7 Autonomous Bot**:
   - Click the green **Start 24/7 Bot** button in the header. The bot will begin scanning the market every 15 seconds, analyzing candle indicators, executing trades, and managing stop-loss / take-profit orders automatically!
6. **Interact with the AI Strategy Coach**:
   - Open the **AI Strategy Coach & Memory** tab to chat with DeepAlpha AI.
   - Use quick prompts like *"Analyze recent trade history and suggest parameter improvements for maximum win rate"* or *"Teach the trading bot a new rule"*.

---

## 💻 Local Development Setup

### 1. Clone the Repository
```bash
git clone https://github.com/kiragamingofficial95-cmd/deepalpha-binance-ai-trading-agent.git
cd deepalpha-binance-ai-trading-agent
```

### 2. Create Virtual Environment & Install Dependencies
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Run the Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **http://localhost:8000** in your browser.

---

## 🚂 Railway 24/7 Hosting

Deploying to Railway takes one command with the Railway CLI:

```bash
railway up
```

---

## 📂 Project Architecture

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
├── render.yaml               # Render 1-Click Blueprint specification
├── railway.json              # Railway deployment config
├── Procfile                  # PaaS web process command
└── requirements.txt          # Python dependencies
```

---

## 📄 License

MIT License — free for quantitative research, algorithmic trading, and educational purposes.
