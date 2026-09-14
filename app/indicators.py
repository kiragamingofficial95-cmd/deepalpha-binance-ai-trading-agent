import numpy as np
import pandas as pd
from typing import Any


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return {
        "macd": macd_line,
        "signal": signal_line,
        "hist": hist
    }


def calculate_bollinger_bands(prices: pd.Series, period: int = 20, std_dev: float = 2.0) -> dict[str, pd.Series]:
    middle = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    bandwidth = (upper - lower) / (middle + 1e-10) * 100
    percent_b = (prices - lower) / (upper - lower + 1e-10)
    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "bandwidth": bandwidth,
        "percent_b": percent_b
    }


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    return prices.ewm(span=period, adjust=False).mean()


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    return prices.rolling(window=period).mean()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return atr.fillna(0.0)


def calculate_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> dict[str, pd.Series]:
    atr = calculate_atr(df, period)
    hl2 = (df['high'] + df['low']) / 2
    
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    
    trend = pd.Series(1, index=df.index) # 1 = bullish, -1 = bearish
    supertrend = pd.Series(0.0, index=df.index)
    
    for i in range(1, len(df)):
        if df['close'].iloc[i] > upper_band.iloc[i-1]:
            trend.iloc[i] = 1
        elif df['close'].iloc[i] < lower_band.iloc[i-1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]
            
            if trend.iloc[i] == 1 and lower_band.iloc[i] < lower_band.iloc[i-1]:
                lower_band.iloc[i] = lower_band.iloc[i-1]
            if trend.iloc[i] == -1 and upper_band.iloc[i] > upper_band.iloc[i-1]:
                upper_band.iloc[i] = upper_band.iloc[i-1]
                
        if trend.iloc[i] == 1:
            supertrend.iloc[i] = lower_band.iloc[i]
        else:
            supertrend.iloc[i] = upper_band.iloc[i]
            
    return {
        "supertrend": supertrend,
        "trend": trend
    }


def calculate_stochastic_rsi(prices: pd.Series, rsi_period: int = 14, stoch_period: int = 14, k_period: int = 3, d_period: int = 3) -> dict[str, pd.Series]:
    rsi = calculate_rsi(prices, rsi_period)
    rsi_min = rsi.rolling(window=stoch_period).min()
    rsi_max = rsi.rolling(window=stoch_period).max()
    
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10) * 100
    k = stoch_rsi.rolling(window=k_period).mean()
    d = k.rolling(window=d_period).mean()
    
    return {
        "k": k.fillna(50.0),
        "d": d.fillna(50.0)
    }


def analyze_all_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """
    Computes a full technical indicator suite on a DataFrame with OHLCV data.
    """
    if len(df) < 30:
        return {}
        
    close = df['close']
    volume = df['volume']
    
    rsi = calculate_rsi(close, 14)
    macd = calculate_macd(close, 12, 26, 9)
    bb = calculate_bollinger_bands(close, 20, 2.0)
    ema9 = calculate_ema(close, 9)
    ema20 = calculate_ema(close, 20)
    ema50 = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    atr = calculate_atr(df, 14)
    stoch = calculate_stochastic_rsi(close, 14, 14, 3, 3)
    vol_ma20 = volume.rolling(20).mean()
    
    latest_close = float(close.iloc[-1])
    latest_vol = float(volume.iloc[-1])
    avg_vol = float(vol_ma20.iloc[-1]) if not pd.isna(vol_ma20.iloc[-1]) else latest_vol
    
    vol_ratio = round(latest_vol / (avg_vol + 1e-10), 2)
    
    # Calculate trend classification
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    e200 = float(ema200.iloc[-1]) if len(df) >= 200 else e50
    
    if latest_close > e20 > e50 > e200:
        trend_status = "STRONG_BULLISH"
    elif latest_close > e50:
        trend_status = "BULLISH"
    elif latest_close < e20 < e50 < e200:
        trend_status = "STRONG_BEARISH"
    elif latest_close < e50:
        trend_status = "BEARISH"
    else:
        trend_status = "RANGING"

    return {
        "price": latest_close,
        "rsi": round(float(rsi.iloc[-1]), 2),
        "rsi_prev": round(float(rsi.iloc[-2]), 2) if len(rsi) > 1 else round(float(rsi.iloc[-1]), 2),
        "macd": round(float(macd["macd"].iloc[-1]), 4),
        "macd_signal": round(float(macd["signal"].iloc[-1]), 4),
        "macd_hist": round(float(macd["hist"].iloc[-1]), 4),
        "macd_hist_prev": round(float(macd["hist"].iloc[-2]), 4) if len(macd["hist"]) > 1 else 0.0,
        "bb_upper": round(float(bb["upper"].iloc[-1]), 4),
        "bb_middle": round(float(bb["middle"].iloc[-1]), 4),
        "bb_lower": round(float(bb["lower"].iloc[-1]), 4),
        "bb_bandwidth": round(float(bb["bandwidth"].iloc[-1]), 2),
        "bb_percent_b": round(float(bb["percent_b"].iloc[-1]), 2),
        "ema9": round(float(ema9.iloc[-1]), 4),
        "ema20": round(float(ema20.iloc[-1]), 4),
        "ema50": round(float(ema50.iloc[-1]), 4),
        "ema200": round(float(e200), 4),
        "atr": round(float(atr.iloc[-1]), 4),
        "stoch_k": round(float(stoch["k"].iloc[-1]), 2),
        "stoch_d": round(float(stoch["d"].iloc[-1]), 2),
        "volume_ratio": vol_ratio,
        "trend_status": trend_status
    }
