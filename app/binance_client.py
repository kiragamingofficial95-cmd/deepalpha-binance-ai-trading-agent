import asyncio
import logging
import time
from typing import Any, Optional
import ccxt.async_support as ccxt
import httpx
import pandas as pd
from app.config import settings

logger = logging.getLogger("binance_client")

# Official Binance public gateways (including data-api.binance.vision which has no geo-restrictions).
# NOTE: testnet.binance.vision is deliberately excluded — its market data is a separate
# price series and must never silently feed live-market trading decisions.
PUBLIC_GATEWAYS = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.com/api/v3",
    "https://api.binance.us/api/v3"
]

DEFAULT_FALLBACK_PRICES = {
    "BTCUSDT": 92450.0,
    "ETHUSDT": 3420.0,
    "SOLUSDT": 188.5,
    "BNBUSDT": 645.0,
    "ADAUSDT": 0.85
}

class BinanceClient:
    def __init__(self, api_key: str = "", secret_key: str = "", testnet: bool = False):
        self.api_key = api_key or settings.BINANCE_API_KEY
        self.secret_key = secret_key or settings.BINANCE_SECRET_KEY
        self.testnet = testnet or settings.BINANCE_TESTNET
        self._exchange: Optional[ccxt.binance] = None
        self._http_client = httpx.AsyncClient(timeout=8.0, follow_redirects=True)
        self._last_known_prices = dict(DEFAULT_FALLBACK_PRICES)

    async def get_exchange(self) -> ccxt.binance:
        if self._exchange is None:
            config = {
                'apiKey': self.api_key,
                'secret': self.secret_key,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot',
                    'adjustForTimeDifference': True,
                }
            }
            if self.testnet:
                config['urls'] = {
                    'api': {
                        'public': 'https://testnet.binance.vision/api',
                        'private': 'https://testnet.binance.vision/api',
                    }
                }
            self._exchange = ccxt.binance(config)
        return self._exchange

    async def update_credentials(self, api_key: str, secret_key: str, testnet: bool = False):
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
        self.api_key = api_key
        self.secret_key = secret_key
        self.testnet = testnet
        self._exchange = None

    async def test_connection(self) -> dict[str, Any]:
        """Test Binance API credentials and connectivity."""
        if not self.api_key or not self.secret_key:
            return {"connected": False, "error": "API Key or Secret is missing"}
        try:
            exchange = await self.get_exchange()
            balance = await exchange.fetch_balance()
            free_usdt = balance.get('USDT', {}).get('free', 0.0)
            total_usdt = balance.get('USDT', {}).get('total', 0.0)
            return {
                "connected": True,
                "testnet": self.testnet,
                "free_usdt": free_usdt,
                "total_usdt": total_usdt,
                "message": "Binance API authenticated successfully"
            }
        except Exception as e:
            logger.error(f"Binance connection test failed: {e}")
            return {"connected": False, "error": str(e)}

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        """
        Fetch real-time ticker data for a symbol (e.g. BTCUSDT).
        Tries multiple public mirrors (data-api.binance.vision, etc.) with automated failover.
        The returned dict carries `synthetic: True` when no live source was reachable —
        callers must refuse to execute trades on synthetic prices.
        """
        formatted_symbol = symbol.replace("/", "").upper()

        for base_url in PUBLIC_GATEWAYS:
            try:
                url = f"{base_url}/ticker/24hr?symbol={formatted_symbol}"
                resp = await self._http_client.get(url)
                if resp.status_code == 429:
                    # Rate limited: back off briefly instead of hammering the gateway
                    retry_after = float(resp.headers.get("Retry-After", "1") or 1)
                    logger.warning(f"fetch_ticker {symbol}: 429 rate limited, backing off {retry_after:.1f}s")
                    await asyncio.sleep(min(retry_after, 5.0))
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    last_price = float(data.get("lastPrice", 0.0))
                    if last_price > 0:
                        self._last_known_prices[formatted_symbol] = last_price
                        return {
                            "symbol": symbol,
                            "price": last_price,
                            "bid": float(data.get("bidPrice", last_price * 0.9998)),
                            "ask": float(data.get("askPrice", last_price * 1.0002)),
                            "high24h": float(data.get("highPrice", last_price * 1.03)),
                            "low24h": float(data.get("lowPrice", last_price * 0.97)),
                            "volume24h": float(data.get("volume", 15000.0)),
                            "quoteVolume24h": float(data.get("quoteVolume", 150000000.0)),
                            "priceChangePercent": float(data.get("priceChangePercent", 1.25)),
                            "timestamp": int(data.get("closeTime", time.time() * 1000)),
                            "synthetic": False
                        }
            except Exception as e:
                logger.debug(f"Gateway {base_url} failed for {symbol}: {e}")
                continue

        # Fallback to CCXT if exchange initialized
        if self.api_key and self.secret_key:
            try:
                exchange = await self.get_exchange()
                ccxt_symbol = f"{symbol[:-4]}/{symbol[-4:]}" if "/" not in symbol else symbol
                ticker = await exchange.fetch_ticker(ccxt_symbol)
                last_price = float(ticker.get("last", 0.0))
                if last_price > 0:
                    self._last_known_prices[formatted_symbol] = last_price
                    return {
                        "symbol": symbol,
                        "price": last_price,
                        "bid": float(ticker.get("bid", last_price)),
                        "ask": float(ticker.get("ask", last_price)),
                        "high24h": float(ticker.get("high", last_price * 1.02)),
                        "low24h": float(ticker.get("low", last_price * 0.98)),
                        "volume24h": float(ticker.get("baseVolume", 1000.0)),
                        "quoteVolume24h": float(ticker.get("quoteVolume", 50000000.0)),
                        "priceChangePercent": float(ticker.get("percentage", 0.5)),
                        "timestamp": int(ticker.get("timestamp", time.time() * 1000)),
                        "synthetic": False
                    }
            except Exception:
                pass

        # Robust synthetic fallback so paper engine & simulation stay alive.
        # This data is NOT real market data - callers must refuse trades on it.
        fallback_p = self._last_known_prices.get(formatted_symbol, 100.0)
        return {
            "symbol": symbol,
            "price": fallback_p,
            "bid": fallback_p * 0.9998,
            "ask": fallback_p * 1.0002,
            "high24h": fallback_p * 1.03,
            "low24h": fallback_p * 0.97,
            "volume24h": 12500.0,
            "quoteVolume24h": 12500.0 * fallback_p,
            "priceChangePercent": 1.45,
            "timestamp": int(time.time() * 1000),
            "synthetic": True
        }

    async def fetch_klines(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> pd.DataFrame:
        """
        Fetch OHLCV candlestick data and return as Pandas DataFrame.
        """
        formatted_symbol = symbol.replace("/", "").upper()
        tf = timeframe.lower() if timeframe != "1M" else "1M"

        for base_url in PUBLIC_GATEWAYS:
            try:
                url = f"{base_url}/klines?symbol={formatted_symbol}&interval={tf}&limit={limit}"
                resp = await self._http_client.get(url)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1") or 1)
                    logger.warning(f"fetch_klines {symbol} {tf}: 429 rate limited, backing off {retry_after:.1f}s")
                    await asyncio.sleep(min(retry_after, 5.0))
                    continue
                if resp.status_code == 200:
                    raw_data = resp.json()
                    if raw_data and len(raw_data) > 0:
                        df = pd.DataFrame(raw_data, columns=[
                            'timestamp', 'open', 'high', 'low', 'close', 'volume',
                            'close_time', 'quote_asset_volume', 'number_of_trades',
                            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                        ])
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            df[col] = df[col].astype(float)
                        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.attrs["synthetic"] = False
                        df.attrs["data_source"] = f"binance_rest:{base_url}"
                        logger.info(f"fetch_klines {symbol} {tf}: gateway={base_url} rows={len(df)}")
                        return df
                    else:
                        logger.warning(f"fetch_klines {symbol} {tf}: gateway={base_url} returned empty raw_data")
                else:
                    logger.warning(f"fetch_klines {symbol} {tf}: gateway={base_url} status={resp.status_code}")
            except Exception as e:
                logger.error(f"fetch_klines {symbol} {tf}: gateway={base_url} error={e}")
                continue

        # SYNTHETIC FALLBACK so the dashboard & simulation stay alive. This data is
        # generated, not real market data. The DataFrame is flagged with
        # attrs["synthetic"]=True and the strategy engine must refuse to trade on it.
        logger.warning(f"fetch_klines {symbol} {tf}: all gateways failed — returning SYNTHETIC fallback candles")
        base_p = self._last_known_prices.get(formatted_symbol, 100.0)
        now = int(time.time() * 1000)
        tf_map = {
            "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
            "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
            "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
            "3d": 259_200_000, "1w": 604_800_000, "1M": 2_592_000_000
        }
        interval_ms = tf_map.get(tf, 900_000)
        candles = []
        p = base_p * 0.96
        for i in range(limit):
            t = now - (limit - i) * interval_ms
            fluctuation = (hash(f"{symbol}_{i}") % 100 - 48) / 1000.0
            o = p
            c = p * (1 + fluctuation)
            h = max(o, c) * (1 + abs(fluctuation) * 0.5)
            l = min(o, c) * (1 - abs(fluctuation) * 0.5)
            v = 100.0 + (hash(f"{i}") % 500)
            candles.append([t, o, h, l, c, v, t + interval_ms, v * c, 50, v * 0.5, v * 0.5 * c, 0])
            p = c

        df = pd.DataFrame(candles, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.attrs["synthetic"] = True
        df.attrs["data_source"] = "synthetic_fallback"
        return df

    async def fetch_real_balance(self) -> dict[str, Any]:
        """Fetch actual account balance from Binance API."""
        if not self.api_key or not self.secret_key:
            return {"error": "Binance API Key/Secret not configured"}
        try:
            exchange = await self.get_exchange()
            balance = await exchange.fetch_balance()
            balances = []
            for asset, data in balance.items():
                if isinstance(data, dict):
                    total = data.get('total', 0.0) or 0.0
                    free = data.get('free', 0.0) or 0.0
                    used = data.get('used', 0.0) or 0.0
                    if total > 0.0001:
                        balances.append({
                            "asset": asset,
                            "free": float(free),
                            "locked": float(used),
                            "total": float(total)
                        })
            return {"success": True, "balances": balances, "raw": balance.get('total', {})}
        except Exception as e:
            logger.error(f"Error fetching real balance: {e}")
            return {"success": False, "error": str(e), "balances": []}

    async def place_real_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> dict[str, Any]:
        """Place a live order on Binance."""
        if not self.api_key or not self.secret_key:
            return {"success": False, "error": "Cannot place REAL order: API keys missing"}
        try:
            exchange = await self.get_exchange()
            ccxt_symbol = f"{symbol[:-4]}/{symbol[-4:]}" if "/" not in symbol else symbol
            side = side.lower()
            order_type = order_type.lower()
            
            params = {}
            order = await exchange.create_order(
                symbol=ccxt_symbol,
                type=order_type,
                side=side,
                amount=quantity,
                price=price if order_type == 'limit' else None,
                params=params
            )
            return {
                "success": True,
                "order_id": order.get('id'),
                "status": order.get('status'),
                "filled": float(order.get('filled', quantity)),
                "price": float(order.get('price', price or 0.0)),
                "raw": order
            }
        except Exception as e:
            logger.error(f"Binance real order placement failed: {e}")
            return {"success": False, "error": str(e)}

    async def close(self):
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
        await self._http_client.aclose()


# Singleton client instance
binance_client = BinanceClient()
