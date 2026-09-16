import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure app is in python path
sys.path.insert(0, os.path.abspath("."))

async def test_full_system():
    print("==================================================")
    print("STARTING FULL DEEPALPHA AI TRADING AGENT SYSTEM TEST")
    print("==================================================")

    # 1. Test Database Init
    print("[1/6] Initializing Database & Default Strategies...")
    from app.database import init_db, AsyncSessionLocal, StrategyConfig, Trade, PaperBalance, StrategyMemory
    from sqlalchemy import select
    await init_db()
    print("  ✓ Database initialized successfully")

    # 2. Test Paper Engine
    print("[2/6] Testing Paper Trading Matching Engine...")
    from app.paper_engine import paper_engine
    bal = await paper_engine.get_balance("USDT")
    print(f"  ✓ Initial Paper Balance: ${bal:,.2f} USDT")
    
    # Open paper position on BTCUSDT
    trade_res = await paper_engine.open_position(
        symbol="BTCUSDT",
        side="BUY",
        amount_usdt=250.0,
        strategy_name="Unit_Test_Strategy",
        entry_reason="Testing Paper Engine Fill",
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        trailing_stop_pct=1.0
    )
    assert trade_res["success"] is True, f"Failed to open position: {trade_res}"
    trade = trade_res["trade"]
    print(f"  ✓ Opened Paper Trade #{trade['id']}: {trade['side']} {trade['symbol']} qty={trade['quantity']} @ ${trade['entry_price']:.4f}")
    
    # Close paper position
    close_res = await paper_engine.close_position(trade["id"], exit_reason="Unit_Test_Close")
    assert close_res["success"] is True, f"Failed to close position: {close_res}"
    closed_trade = close_res["trade"]
    print(f"  ✓ Closed Paper Trade #{closed_trade['id']}: Exit=${closed_trade['exit_price']:.4f}, PnL=${closed_trade['pnl']:.2f} ({closed_trade['pnl_pct']:+.2f}%)")

    # 3. Test Binance Client & Public Market Data
    print("[3/6] Testing Binance Connector & Public Feeds...")
    from app.binance_client import binance_client
    ticker = await binance_client.fetch_ticker("BTCUSDT")
    print(f"  ✓ Live BTCUSDT Price: ${ticker.get('price'):,.2f} (24h Change: {ticker.get('priceChangePercent'):+.2f}%)")
    
    klines = await binance_client.fetch_klines("BTCUSDT", timeframe="15m", limit=50)
    assert not klines.empty, "Klines DataFrame is empty"
    print(f"  ✓ Fetched {len(klines)} Binance 15m candles successfully")

    # 4. Test Technical Indicators
    print("[4/6] Testing Vectorized Technical Indicators Engine...")
    from app.indicators import analyze_all_indicators
    indicators = analyze_all_indicators(klines)
    print(f"  ✓ Indicators Snapshot: RSI={indicators['rsi']}, MACD={indicators['macd']}, Trend={indicators['trend_status']}, EMA20={indicators['ema20']}")

    # 5. Test Strategy Signal Evaluation
    print("[5/6] Testing Strategy Signal Engine...")
    from app.strategy_engine import strategy_engine
    active_strat = await strategy_engine.get_active_strategy()
    signal = await strategy_engine.evaluate_symbol("BTCUSDT", klines, active_strat)
    print(f"  ✓ Generated Signal for BTCUSDT: Action={signal['action']} (Confidence: {signal.get('confidence', 0)*100:.0f}%, Reason: {signal.get('reason')})")

    # 6. Test Quantitative Analytics Engine
    print("[6/6] Testing Quantitative Analytics Engine...")
    from app.analytics import analytics_engine
    analytics = await analytics_engine.get_performance_summary("PAPER")
    print(f"  ✓ Analytics Summary: Total Trades={analytics['total_trades']}, Win Rate={analytics['win_rate_pct']}%, PnL=${analytics['total_realized_pnl']:.2f}, Sharpe={analytics['sharpe_ratio']}")

    await binance_client.close()
    print("==================================================")
    print("ALL 6/6 CORE SUBSYSTEM TESTS PASSED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(test_full_system())
