import asyncio
import os
import sys
import httpx

sys.path.insert(0, os.path.abspath("."))

async def test_api_endpoints():
    print("Testing REST API & Server routes...")
    # Import app and test with httpx AsyncClient using ASGITransport
    from app.main import app
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. Test status
        res = await client.get("/api/status")
        assert res.status_code == 200, f"Status failed: {res.text}"
        data = res.json()
        print("  ✓ /api/status OK:", data["bot"]["trading_mode"])

        # 2. Test balances
        res = await client.get("/api/balances")
        assert res.status_code == 200
        print("  ✓ /api/balances OK")

        # 3. Test strategies
        res = await client.get("/api/strategies")
        assert res.status_code == 200
        strats = res.json()
        print(f"  ✓ /api/strategies OK ({len(strats)} strategies available)")

        # 4. Test memories
        res = await client.get("/api/memories")
        assert res.status_code == 200
        mems = res.json()
        print(f"  ✓ /api/memories OK ({len(mems)} AI memories loaded)")

        # 5. Test manual paper trade
        trade_payload = {
            "symbol": "ETHUSDT",
            "side": "BUY",
            "amount_usdt": 150.0,
            "mode": "PAPER",
            "stop_loss_pct": 2.0,
            "take_profit_pct": 4.0,
            "reason": "API Endpoint Integration Test"
        }
        res = await client.post("/api/trade/open", json=trade_payload)
        assert res.status_code == 200
        trade_data = res.json()
        assert trade_data["success"] is True
        opened_id = trade_data["trade"]["id"]
        print(f"  ✓ /api/trade/open OK (Trade #{opened_id} opened)")

        # 6. Test closing trade
        res = await client.post(f"/api/trade/close/{opened_id}")
        assert res.status_code == 200
        close_data = res.json()
        assert close_data["success"] is True
        print(f"  ✓ /api/trade/close OK (Trade #{opened_id} closed with PnL ${close_data['trade']['pnl']:.2f})")

        # 7. Test analytics
        res = await client.get("/api/analytics")
        assert res.status_code == 200
        analytics = res.json()
        print(f"  ✓ /api/analytics OK (Total closed trades: {analytics['closed_trades_count']})")

        # 8. Test HTML Dashboard
        res = await client.get("/")
        assert res.status_code == 200
        assert "DEEPALPHA AI" in res.text
        print("  ✓ Dashboard HTML rendering OK")

    print("ALL API ENDPOINT INTEGRATION TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_api_endpoints())
