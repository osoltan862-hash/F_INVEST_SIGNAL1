import os
import time
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd

# ============================================================
# F-INVEST SIGNAL ENGINE V1.0
# Independent project based on the video structure.
# Analysis only: it does NOT place orders.
# ============================================================

SYMBOL = os.getenv("SYMBOL", "BTC/USDT").strip().upper()

# The video uses 5m. 10m and 15m are NOT used in this version.
TIMEFRAME = "5m"
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "250"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))

# Video settings
VOLUME_FILTER_LEVEL = float(os.getenv("VOLUME_FILTER_LEVEL", "1.3"))
VOLUME_LOOKBACK = int(os.getenv("VOLUME_LOOKBACK", "11"))

IMPULSE_FAST = int(os.getenv("IMPULSE_FAST", "50"))
IMPULSE_SLOW = int(os.getenv("IMPULSE_SLOW", "103"))

# Momentum Confirmation is OFF, matching the video settings.
MOMENTUM_CONFIRMATION = os.getenv("MOMENTUM_CONFIRMATION", "false").lower() == "true"
MOMENTUM_PERIOD = int(os.getenv("MOMENTUM_PERIOD", "11"))
OVERBOUGHT_LEVEL = float(os.getenv("OVERBOUGHT_LEVEL", "65"))

# User's modifications
SL_POINTS = float(os.getenv("SL_POINTS", "30"))
TP1_POINTS = float(os.getenv("TP1_POINTS", "15"))
TP2_POINTS = float(os.getenv("TP2_POINTS", "30"))
TP3_POINTS = float(os.getenv("TP3_POINTS", "50"))

LAST_SIGNAL_BAR = None

exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})


def wma(series, length):
    weights = np.arange(1, length + 1, dtype=float)
    return series.rolling(length).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True,
    )


def rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def fetch_candles():
    rows = exchange.fetch_ohlcv(
        SYMBOL,
        TIMEFRAME,
        limit=CANDLE_LIMIT,
    )

    if not rows:
        raise RuntimeError("Binance returned no candles.")

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True,
    )

    return df


def analyze(df):
    minimum = IMPULSE_SLOW + VOLUME_LOOKBACK + 5

    if len(df) < minimum:
        return None

    # Never generate a signal from the still-forming candle.
    closed = df.iloc[:-1].copy()

    if len(closed) < minimum:
        return None

    closed["wma_fast"] = wma(
        closed["close"],
        IMPULSE_FAST,
    )

    closed["wma_slow"] = wma(
        closed["close"],
        IMPULSE_SLOW,
    )

    closed["volume_avg"] = (
        closed["volume"]
        .shift(1)
        .rolling(VOLUME_LOOKBACK)
        .mean()
    )

    closed["volume_ratio"] = (
        closed["volume"] / closed["volume_avg"]
    )

    closed["rsi"] = rsi(
        closed["close"],
        MOMENTUM_PERIOD,
    )

    c = closed.iloc[-1]
    p = closed.iloc[-2]

    if (
        pd.isna(c["wma_fast"])
        or pd.isna(c["wma_slow"])
        or pd.isna(c["volume_avg"])
    ):
        return None

    volume_ok = (
        c["volume_ratio"] >= VOLUME_FILTER_LEVEL
    )

    # Impulse direction.
    bullish_impulse = (
        c["wma_fast"] > c["wma_slow"]
        and c["close"] > c["open"]
        and c["wma_fast"] > p["wma_fast"]
    )

    bearish_impulse = (
        c["wma_fast"] < c["wma_slow"]
        and c["close"] < c["open"]
        and c["wma_fast"] < p["wma_fast"]
    )

    # Optional momentum filter.
    # It remains OFF by default, matching the video configuration.
    momentum_buy_ok = True
    momentum_sell_ok = True

    if MOMENTUM_CONFIRMATION:
        momentum_buy_ok = (
            c["rsi"] > 50
            and c["rsi"] < OVERBOUGHT_LEVEL
        )

        momentum_sell_ok = c["rsi"] < 50

    buy = (
        volume_ok
        and bullish_impulse
        and momentum_buy_ok
    )

    sell = (
        volume_ok
        and bearish_impulse
        and momentum_sell_ok
    )

    if not buy and not sell:
        return {
            "signal": "NONE",
            "bar": c["timestamp"],
            "close": float(c["close"]),
            "volume_ratio": float(c["volume_ratio"]),
            "rsi": (
                None
                if pd.isna(c["rsi"])
                else float(c["rsi"])
            ),
        }

    signal = "BUY" if buy else "SELL"
    entry = float(c["close"])

    if signal == "BUY":
        sl = entry - SL_POINTS
        tp1 = entry + TP1_POINTS
        tp2 = entry + TP2_POINTS
        tp3 = entry + TP3_POINTS
    else:
        sl = entry + SL_POINTS
        tp1 = entry - TP1_POINTS
        tp2 = entry - TP2_POINTS
        tp3 = entry - TP3_POINTS

    return {
        "signal": signal,
        "bar": c["timestamp"],
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "volume_ratio": float(c["volume_ratio"]),
        "rsi": (
            None
            if pd.isna(c["rsi"])
            else float(c["rsi"])
        ),
        "wma_fast": float(c["wma_fast"]),
        "wma_slow": float(c["wma_slow"]),
    }


def fmt(value):
    return f"{value:.8f}".rstrip("0").rstrip(".")


def print_signal(result):
    print("\n" + "=" * 60)
    print(
        f"F-INVEST SIGNAL ENGINE | "
        f"{SYMBOL} | {TIMEFRAME}"
    )
    print("=" * 60)
    print(f"Signal : {result['signal']}")
    print(f"Entry  : {fmt(result['entry'])}")
    print(f"SL     : {fmt(result['sl'])}")
    print(f"TP1    : {fmt(result['tp1'])}")
    print(f"TP2    : {fmt(result['tp2'])}")
    print(f"TP3    : {fmt(result['tp3'])}")
    print(f"Volume : {result['volume_ratio']:.2f}x")
    print(
        f"WMA {IMPULSE_FAST}: "
        f"{fmt(result['wma_fast'])}"
    )
    print(
        f"WMA {IMPULSE_SLOW}: "
        f"{fmt(result['wma_slow'])}"
    )

    if result["rsi"] is not None:
        print(f"RSI    : {result['rsi']:.2f}")

    print(f"Bar    : {result['bar']}")
    print("=" * 60)


def main():
    global LAST_SIGNAL_BAR

    print("=" * 60)
    print("F-INVEST SIGNAL ENGINE V1.0")
    print("=" * 60)
    print(f"SYMBOL       : {SYMBOL}")
    print(f"TIMEFRAME    : {TIMEFRAME}")
    print(f"VOLUME       : {VOLUME_FILTER_LEVEL}x / {VOLUME_LOOKBACK} bars")
    print(
        f"IMPULSE WMA  : "
        f"{IMPULSE_FAST} / {IMPULSE_SLOW}"
    )
    print(
        f"SL / TP      : "
        f"{SL_POINTS} / {TP1_POINTS} / "
        f"{TP2_POINTS} / {TP3_POINTS}"
    )
    print("MOMENTUM     : OFF")
    print("MODE         : ANALYSIS ONLY")
    print("=" * 60)

    while True:
        try:
            df = fetch_candles()
            result = analyze(df)

            if result is None:
                print(
                    f"{datetime.now(timezone.utc).isoformat()} | "
                    "Waiting for enough candles..."
                )

            elif result["signal"] == "NONE":
                print(
                    f"{datetime.now(timezone.utc).isoformat()} | "
                    f"{SYMBOL} | 5m | NO SIGNAL | "
                    f"Volume {result['volume_ratio']:.2f}x"
                )

            elif result["bar"] != LAST_SIGNAL_BAR:
                print_signal(result)
                LAST_SIGNAL_BAR = result["bar"]

        except Exception as exc:
            print(
                f"{datetime.now(timezone.utc).isoformat()} | "
                f"ERROR | {exc}"
            )

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
