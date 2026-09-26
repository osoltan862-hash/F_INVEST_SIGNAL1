import os
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
import json


# =========================================================
# Support Rebound Scanner V1.1
# Binance Public Market Data API
# =========================================================

APP_VERSION = "V1.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("support_rebound_scanner")


# =========================================================
# Binance Market Data API
# =========================================================

BINANCE_DATA_API = os.getenv(
    "BINANCE_DATA_API",
    "https://data-api.binance.vision"
)


# =========================================================
# Strategy Settings
# =========================================================

TIMEFRAME = os.getenv("TIMEFRAME", "5m")

CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "150"))

DROP_LOOKBACK = int(os.getenv("DROP_LOOKBACK", "24"))
MIN_DROP_PERCENT = float(os.getenv("MIN_DROP_PERCENT", "3.0"))

SUPPORT_LOOKBACK = int(os.getenv("SUPPORT_LOOKBACK", "12"))
SUPPORT_TOLERANCE_PERCENT = float(
    os.getenv("SUPPORT_TOLERANCE_PERCENT", "0.20")
)

VOLUME_LOOKBACK = int(os.getenv("VOLUME_LOOKBACK", "11"))
VOLUME_MULTIPLIER = float(
    os.getenv("VOLUME_MULTIPLIER", "1.30")
)

WMA_FAST = int(os.getenv("WMA_FAST", "50"))
WMA_SLOW = int(os.getenv("WMA_SLOW", "103"))

SL_PERCENT = float(os.getenv("SL_PERCENT", "3.0"))
TP1_PERCENT = float(os.getenv("TP1_PERCENT", "1.0"))
TP2_PERCENT = float(os.getenv("TP2_PERCENT", "2.0"))
TP3_PERCENT = float(os.getenv("TP3_PERCENT", "3.0"))

MIN_BODY_RATIO = float(
    os.getenv("MIN_BODY_RATIO", "0.25")
)


# =========================================================
# FastAPI
# =========================================================

app = FastAPI(
    title="Support Rebound Scanner",
    version=APP_VERSION
)


# =========================================================
# Static Files
# =========================================================

STATIC_DIR = os.path.join(
    os.path.dirname(__file__),
    "static"
)

if os.path.isdir(STATIC_DIR):
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static"
    )


# =========================================================
# Binance HTTP Helper
# =========================================================

def binance_get(path: str, params: Optional[dict] = None):

    url = BINANCE_DATA_API.rstrip("/") + path

    if params:
        url += "?" + urlencode(params)

    logger.info("Binance request: %s", url)

    request = Request(
        url,
        headers={
            "User-Agent": "Support-Rebound-Scanner/1.1"
        },
        method="GET"
    )

    try:

        with urlopen(request, timeout=20) as response:

            raw = response.read().decode("utf-8")

            return json.loads(raw)

    except HTTPError as e:

        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""

        logger.error(
            "Binance HTTP %s: %s",
            e.code,
            body
        )

        raise RuntimeError(
            f"Binance API HTTP {e.code}: {body}"
        )

    except URLError as e:

        logger.error(
            "Binance connection error: %s",
            e
        )

        raise RuntimeError(
            f"Binance connection error: {e}"
        )

    except Exception as e:

        logger.exception(
            "Unexpected Binance error"
        )

        raise RuntimeError(
            f"Binance API error: {e}"
        )


# =========================================================
# Symbol Helpers
# =========================================================

def normalize_symbol(symbol: str) -> str:

    if not symbol:
        return ""

    symbol = symbol.strip().upper()

    symbol = symbol.replace("-", "/")

    if "/" not in symbol and symbol.endswith("USDT"):
        symbol = symbol[:-4] + "/USDT"

    if "/" not in symbol:
        return symbol

    base, quote = symbol.split("/", 1)

    return f"{base}/{quote}"


def binance_symbol(symbol: str) -> str:

    normalized = normalize_symbol(symbol)

    return normalized.replace("/", "")


# =========================================================
# Binance Spot Symbols
# =========================================================

def get_spot_usdt_symbols():

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    symbols = []

    for item in data.get("symbols", []):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):

            symbols.append(
                item.get("symbol")
            )

    symbols = sorted(
        set(symbols)
    )

    logger.info(
        "Loaded %s Binance Spot USDT symbols",
        len(symbols)
    )

    return symbols


# =========================================================
# Fetch Candles
# =========================================================

def fetch_ohlcv(symbol: str) -> pd.DataFrame:

    raw_symbol = binance_symbol(symbol)

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": raw_symbol,
            "interval": TIMEFRAME,
            "limit": CANDLE_LIMIT
        }
    )

    if not data:

        raise RuntimeError(
            f"No candle data returned for {raw_symbol}"
        )

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore"
    ]

    df = pd.DataFrame(
        data,
        columns=columns
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume"
    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["open_time"] = pd.to_numeric(
        df["open_time"],
        errors="coerce"
    )

    df["close_time"] = pd.to_numeric(
        df["close_time"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    ).reset_index(drop=True)

    # =====================================================
    # Remove current still-forming candle
    # =====================================================

    if len(df) > 1:

        df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


# =========================================================
# WMA
# =========================================================

def calculate_wma(
    series: pd.Series,
    period: int
) -> pd.Series:

    weights = pd.Series(
        range(1, period + 1),
        dtype=float
    )

    return series.rolling(
        period
    ).apply(
        lambda values: (
            values * weights.values
        ).sum() / weights.sum(),
        raw=True
    )


# =========================================================
# Price Levels
# =========================================================

def calculate_levels(entry: float):

    sl = entry * (
        1.0 - SL_PERCENT / 100.0
    )

    tp1 = entry * (
        1.0 + TP1_PERCENT / 100.0
    )

    tp2 = entry * (
        1.0 + TP2_PERCENT / 100.0
    )

    tp3 = entry * (
        1.0 + TP3_PERCENT / 100.0
    )

    return {
        "entry": float(entry),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3)
    }


# =========================================================
# Candle Rejection
# =========================================================

def rejection_candle(row):

    candle_range = float(
        row["high"] - row["low"]
    )

    if candle_range <= 0:
        return {
            "bullish": False,
            "body_ratio": 0.0,
            "close_upper_half": False,
            "lower_wick_ratio": 0.0
        }

    body = abs(
        float(row["close"]) -
        float(row["open"])
    )

    body_ratio = body / candle_range

    lower_wick = (
        min(
            float(row["open"]),
            float(row["close"])
        )
        - float(row["low"])
    )

    lower_wick_ratio = (
        lower_wick / candle_range
    )

    close_position = (
        float(row["close"]) -
        float(row["low"])
    ) / candle_range

    return {
        "bullish": (
            float(row["close"]) >
            float(row["open"])
        ),

        "body_ratio": float(
            body_ratio
        ),

        "close_upper_half": (
            close_position >= 0.50
        ),

        "lower_wick_ratio": float(
            lower_wick_ratio
        )
    }


# =========================================================
# Main Analysis
# =========================================================

def analyze_symbol(symbol: str):

    normalized = normalize_symbol(symbol)

    if not normalized.endswith("/USDT"):

        raise HTTPException(
            status_code=400,
            detail="Only Binance Spot USDT symbols are supported."
        )

    df = fetch_ohlcv(normalized)

    minimum_required = max(
        WMA_SLOW + 5,
        DROP_LOOKBACK + 5,
        SUPPORT_LOOKBACK + 5,
        VOLUME_LOOKBACK + 5,
        110
    )

    if len(df) < minimum_required:

        return {
            "symbol": normalized,
            "signal": "NOT ENOUGH DATA",
            "message": (
                f"Need at least {minimum_required} "
                f"closed candles."
            )
        }

    # =====================================================
    # WMA
    # =====================================================

    df["wma_fast"] = calculate_wma(
        df["close"],
        WMA_FAST
    )

    df["wma_slow"] = calculate_wma(
        df["close"],
        WMA_SLOW
    )

    current = df.iloc[-1]
    previous = df.iloc[-2]

    entry = float(
        current["close"]
    )

    wma_fast = float(
        current["wma_fast"]
    )

    wma_slow = float(
        current["wma_slow"]
    )

    previous_wma_fast = float(
        previous["wma_fast"]
    )

    # =====================================================
    # Recent High / Drop
    # =====================================================

    drop_start = max(
        0,
        len(df) - DROP_LOOKBACK - 1
    )

    drop_end = len(df) - 1

    previous_window = df.iloc[
        drop_start:drop_end
    ]

    recent_high = float(
        previous_window["high"].max()
    )

    drop_percent = (
        (recent_high - entry)
        / recent_high
        * 100.0
    )

    drop_condition = (
        drop_percent >= MIN_DROP_PERCENT
    )

    # =====================================================
    # Support
    # =====================================================

    support_window = df.iloc[
        -SUPPORT_LOOKBACK:
    ]

    support = float(
        support_window["low"].min()
    )

    support_distance_percent = (
        abs(entry - support)
        / support
        * 100.0
    )

    # =====================================================
    # Support Touch
    # =====================================================

    current_low = float(
        current["low"]
    )

    previous_low = float(
        previous["low"]
    )

    current_support_distance = (
        abs(current_low - support)
        / support
        * 100.0
    )

    previous_support_distance = (
        abs(previous_low - support)
        / support
        * 100.0
    )

    support_touched = (
        current_support_distance
        <= SUPPORT_TOLERANCE_PERCENT
        or
        previous_support_distance
        <= SUPPORT_TOLERANCE_PERCENT
    )

    # =====================================================
    # Rejection Candle
    # =====================================================

    candle = rejection_candle(
        current
    )

    bullish_rejection = (
        candle["bullish"]
        and
        candle["body_ratio"]
        >= MIN_BODY_RATIO
        and
        candle["close_upper_half"]
        and
        candle["lower_wick_ratio"]
        >= 0.15
    )

    # =====================================================
    # Volume Confirmation
    # =====================================================

    volume_start = (
        len(df)
        - VOLUME_LOOKBACK
        - 1
    )

    volume_end = len(df) - 1

    previous_volumes = df.iloc[
        volume_start:volume_end
    ]["volume"]

    average_volume = float(
        previous_volumes.mean()
    )

    current_volume = float(
        current["volume"]
    )

    if average_volume > 0:

        volume_ratio = (
            current_volume
            / average_volume
        )

    else:

        volume_ratio = 0.0

    volume_condition = (
        volume_ratio >= VOLUME_MULTIPLIER
    )

    # =====================================================
    # WMA Turning Up
    # =====================================================

    wma_turning_up = (
        wma_fast >
        previous_wma_fast
    )

    # =====================================================
    # Final Signal
    # =====================================================

    conditions = {

        "drop": bool(
            drop_condition
        ),

        "support_touch": bool(
            support_touched
        ),

        "bullish_rejection": bool(
            bullish_rejection
        ),

        "volume_confirmation": bool(
            volume_condition
        ),

        "wma50_turning_up": bool(
            wma_turning_up
        )
    }

    confirmed = all(
        conditions.values()
    )

    if confirmed:

        signal = "EARLY SUPPORT REBOUND"

        message = (
            "All support-rebound conditions "
            "are confirmed."
        )

    else:

        signal = "NO CONFIRMED SIGNAL"

        failed = [
            name
            for name, value
            in conditions.items()
            if not value
        ]

        message = (
            "Waiting for: "
            + ", ".join(failed)
        )

    # =====================================================
    # Levels
    # =====================================================

    levels = calculate_levels(
        entry
    )

    # =====================================================
    # Result
    # =====================================================

    return {

        "symbol": normalized,

        "signal": signal,

        "message": message,

        "timeframe": TIMEFRAME,

        "market": "Binance Spot",

        "price": round(
            entry,
            12
        ),

        "entry": round(
            levels["entry"],
            12
        ),

        "drop_percent": round(
            drop_percent,
            3
        ),

        "recent_high": round(
            recent_high,
            12
        ),

        "support": round(
            support,
            12
        ),

        "support_distance_percent": round(
            support_distance_percent,
            3
        ),

        "volume_ratio": round(
            volume_ratio,
            3
        ),

        "average_volume": round(
            average_volume,
            8
        ),

        "current_volume": round(
            current_volume,
            8
        ),

        "wma50": round(
            wma_fast,
            12
        ),

        "wma103": round(
            wma_slow,
            12
        ),

        "previous_wma50": round(
            previous_wma_fast,
            12
        ),

        "candle_body_ratio": round(
            candle["body_ratio"],
            3
        ),

        "lower_wick_ratio": round(
            candle["lower_wick_ratio"],
            3
        ),

        "conditions": conditions,

        "levels": {

            "entry": round(
                levels["entry"],
                12
            ),

            "sl": round(
                levels["sl"],
                12
            ),

            "tp1": round(
                levels["tp1"],
                12
            ),

            "tp2": round(
                levels["tp2"],
                12
            ),

            "tp3": round(
                levels["tp3"],
                12
            )
        },

        "strategy": {

            "drop_lookback": DROP_LOOKBACK,

            "min_drop_percent":
                MIN_DROP_PERCENT,

            "support_lookback":
                SUPPORT_LOOKBACK,

            "support_tolerance_percent":
                SUPPORT_TOLERANCE_PERCENT,

            "volume_lookback":
                VOLUME_LOOKBACK,

            "volume_multiplier":
                VOLUME_MULTIPLIER,

            "wma_fast":
                WMA_FAST,

            "wma_slow":
                WMA_SLOW,

            "sl_percent":
                SL_PERCENT,

            "tp1_percent":
                TP1_PERCENT,

            "tp2_percent":
                TP2_PERCENT,

            "tp3_percent":
                TP3_PERCENT
        },

        "updated_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


# =========================================================
# Routes
# =========================================================

@app.get("/")
def home():

    index_file = os.path.join(
        STATIC_DIR,
        "index.html"
    )

    if not os.path.isfile(index_file):

        raise HTTPException(
            status_code=404,
            detail="static/index.html not found."
        )

    return FileResponse(
        index_file
    )


@app.get("/api/health")
def health():

    return {

        "status": "ok",

        "version": APP_VERSION,

        "binance_api":
            BINANCE_DATA_API,

        "timeframe":
            TIMEFRAME,

        "market":
            "Binance Spot",

        "strategy":
            "Drop → Support → Rejection → Volume → WMA50 Up"
    }


@app.get("/api/symbols")
def symbols():

    try:

        data = get_spot_usdt_symbols()

        return {

            "count": len(data),

            "symbols": data

        }

    except Exception as e:

        logger.exception(
            "Failed to load symbols"
        )

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


@app.get("/api/scan")
def scan(
    symbol: str = Query(
        ...,
        description="Example: BTC/USDT"
    )
):

    try:

        result = analyze_symbol(
            symbol
        )

        return result

    except HTTPException:

        raise

    except Exception as e:

        logger.exception(
            "Scan failed for %s",
            symbol
        )

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


# =========================================================
# Local / Railway Start
# =========================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port
      )
