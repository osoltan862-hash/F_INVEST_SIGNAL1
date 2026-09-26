import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

import pandas as pd

from fastapi import FastAPI, HTTPException, Query, Request as FastAPIRequest
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# =========================================================
# Support Rebound Scanner V1.2
# =========================================================
#
# Strategy:
#
# Drop
#   ↓
# Support / Low Area
#   ↓
# Early Bullish Rejection
#   ↓
# Volume Confirmation
#   ↓
# WMA 50 Turning Up
#
# Binance Spot
# Timeframe: 5m
#
# V1.2 adds diagnostic endpoints and detailed logging.
# =========================================================


APP_VERSION = "V1.2"


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("support_rebound_scanner")


# =========================================================
# Binance Public Market Data API
# =========================================================

BINANCE_DATA_API = os.getenv(
    "BINANCE_DATA_API",
    "https://data-api.binance.vision"
).rstrip("/")


# =========================================================
# Strategy Settings
# =========================================================

TIMEFRAME = os.getenv(
    "TIMEFRAME",
    "5m"
)

CANDLE_LIMIT = int(
    os.getenv(
        "CANDLE_LIMIT",
        "150"
    )
)

DROP_LOOKBACK = int(
    os.getenv(
        "DROP_LOOKBACK",
        "24"
    )
)

MIN_DROP_PERCENT = float(
    os.getenv(
        "MIN_DROP_PERCENT",
        "3.0"
    )
)

SUPPORT_LOOKBACK = int(
    os.getenv(
        "SUPPORT_LOOKBACK",
        "12"
    )
)

SUPPORT_TOLERANCE_PERCENT = float(
    os.getenv(
        "SUPPORT_TOLERANCE_PERCENT",
        "0.20"
    )
)

VOLUME_LOOKBACK = int(
    os.getenv(
        "VOLUME_LOOKBACK",
        "11"
    )
)

VOLUME_MULTIPLIER = float(
    os.getenv(
        "VOLUME_MULTIPLIER",
        "1.30"
    )
)

WMA_FAST = int(
    os.getenv(
        "WMA_FAST",
        "50"
    )
)

WMA_SLOW = int(
    os.getenv(
        "WMA_SLOW",
        "103"
    )
)

MIN_BODY_RATIO = float(
    os.getenv(
        "MIN_BODY_RATIO",
        "0.25"
    )
)

SL_PERCENT = float(
    os.getenv(
        "SL_PERCENT",
        "3.0"
    )
)

TP1_PERCENT = float(
    os.getenv(
        "TP1_PERCENT",
        "1.0"
    )
)

TP2_PERCENT = float(
    os.getenv(
        "TP2_PERCENT",
        "2.0"
    )
)

TP3_PERCENT = float(
    os.getenv(
        "TP3_PERCENT",
        "3.0"
    )
)


# =========================================================
# FastAPI
# =========================================================

app = FastAPI(
    title="Support Rebound Scanner",
    version=APP_VERSION
)


# =========================================================
# Static Directory
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

STATIC_DIR = os.path.join(
    BASE_DIR,
    "static"
)


if os.path.isdir(STATIC_DIR):

    app.mount(
        "/static",
        StaticFiles(
            directory=STATIC_DIR
        ),
        name="static"
    )

    logger.info(
        "Static directory found: %s",
        STATIC_DIR
    )

else:

    logger.error(
        "Static directory NOT FOUND: %s",
        STATIC_DIR
    )


# =========================================================
# Request Logging Middleware
# =========================================================

@app.middleware("http")
async def request_logger(
    request: FastAPIRequest,
    call_next
):

    start_time = datetime.now(
        timezone.utc
    )

    logger.info(
        "REQUEST START | %s %s",
        request.method,
        request.url.path
    )

    try:

        response = await call_next(
            request
        )

        elapsed = (
            datetime.now(
                timezone.utc
            ) - start_time
        ).total_seconds()

        logger.info(
            "REQUEST END | %s %s | status=%s | %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            elapsed
        )

        return response

    except Exception as e:

        logger.exception(
            "REQUEST ERROR | %s %s | %s",
            request.method,
            request.url.path,
            e
        )

        raise


# =========================================================
# Binance GET Helper
# =========================================================

def binance_get(
    path: str,
    params: Optional[dict] = None
):

    url = (
        BINANCE_DATA_API
        + path
    )

    if params:

        url += "?" + urlencode(
            params
        )

    logger.info(
        "BINANCE REQUEST | %s",
        url
    )

    request = Request(
        url=url,
        headers={
            "User-Agent":
                "Support-Rebound-Scanner/1.2"
        },
        method="GET"
    )

    try:

        with urlopen(
            request,
            timeout=20
        ) as response:

            status_code = (
                response.status
            )

            body = (
                response
                .read()
                .decode("utf-8")
            )

            logger.info(
                "BINANCE RESPONSE | HTTP %s",
                status_code
            )

            try:

                data = json.loads(
                    body
                )

            except json.JSONDecodeError:

                logger.error(
                    "BINANCE INVALID JSON | %s",
                    body[:500]
                )

                raise RuntimeError(
                    "Binance returned invalid JSON."
                )

            return data

    except HTTPError as e:

        try:

            body = (
                e.read()
                .decode("utf-8")
            )

        except Exception:

            body = ""

        logger.error(
            "BINANCE HTTP ERROR | code=%s | body=%s",
            e.code,
            body[:1000]
        )

        raise RuntimeError(
            f"Binance HTTP {e.code}: {body}"
        )

    except URLError as e:

        logger.error(
            "BINANCE URL ERROR | %s",
            e
        )

        raise RuntimeError(
            f"Binance connection error: {e}"
        )

    except TimeoutError:

        logger.error(
            "BINANCE TIMEOUT"
        )

        raise RuntimeError(
            "Binance request timed out after 20 seconds."
        )

    except Exception as e:

        logger.exception(
            "BINANCE UNKNOWN ERROR"
        )

        raise RuntimeError(
            f"Binance API error: {e}"
        )


# =========================================================
# Symbol Normalization
# =========================================================

def normalize_symbol(
    symbol: str
) -> str:

    if not symbol:

        return ""

    symbol = (
        symbol
        .strip()
        .upper()
    )

    symbol = symbol.replace(
        "-",
        "/"
    )

    if (
        "/" not in symbol
        and symbol.endswith("USDT")
    ):

        symbol = (
            symbol[:-4]
            + "/USDT"
        )

    return symbol


def binance_symbol(
    symbol: str
) -> str:

    normalized = normalize_symbol(
        symbol
    )

    return normalized.replace(
        "/",
        ""
    )


# =========================================================
# Binance Spot USDT Symbols
# =========================================================

def get_spot_usdt_symbols():

    logger.info(
        "SYMBOLS | requesting exchangeInfo"
    )

    data = binance_get(
        "/api/v3/exchangeInfo"
    )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "Invalid exchangeInfo response."
        )

    raw_symbols = data.get(
        "symbols",
        []
    )

    if not raw_symbols:

        raise RuntimeError(
            "Binance exchangeInfo returned zero symbols."
        )

    symbols = []

    for item in raw_symbols:

        if not isinstance(
            item,
            dict
        ):
            continue

        if (
            item.get("status")
            == "TRADING"

            and item.get("quoteAsset")
            == "USDT"

            and item.get(
                "isSpotTradingAllowed"
            )
            is True
        ):

            symbol = item.get(
                "symbol"
            )

            if symbol:

                symbols.append(
                    symbol
                )

    symbols = sorted(
        set(symbols)
    )

    logger.info(
        "SYMBOLS | loaded %s Spot USDT symbols",
        len(symbols)
    )

    return symbols


# =========================================================
# Fetch OHLCV
# =========================================================

def fetch_ohlcv(
    symbol: str
) -> pd.DataFrame:

    raw_symbol = binance_symbol(
        symbol
    )

    logger.info(
        "KLINES | symbol=%s | interval=%s | limit=%s",
        raw_symbol,
        TIMEFRAME,
        CANDLE_LIMIT
    )

    data = binance_get(
        "/api/v3/klines",
        {
            "symbol": raw_symbol,
            "interval": TIMEFRAME,
            "limit": CANDLE_LIMIT
        }
    )

    if not isinstance(
        data,
        list
    ):

        raise RuntimeError(
            "Binance klines response is not a list."
        )

    if len(data) == 0:

        raise RuntimeError(
            f"No candles returned for {raw_symbol}."
        )

    logger.info(
        "KLINES | received %s candles for %s",
        len(data),
        raw_symbol
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
    ).reset_index(
        drop=True
    )

    # -----------------------------------------------------
    # Remove current candle because it may still be forming
    # -----------------------------------------------------

    if len(df) > 1:

        df = df.iloc[:-1].copy()

    logger.info(
        "KLINES | closed candles available=%s",
        len(df)
    )

    return df.reset_index(
        drop=True
    )


# =========================================================
# WMA
# =========================================================

def calculate_wma(
    series: pd.Series,
    period: int
) -> pd.Series:

    weights = pd.Series(
        range(
            1,
            period + 1
        ),
        dtype=float
    )

    return series.rolling(
        period
    ).apply(
        lambda values:
            (
                values
                * weights.values
            ).sum()
            / weights.sum(),
        raw=True
    )


# =========================================================
# Price Levels
# =========================================================

def calculate_levels(
    entry: float
):

    sl = (
        entry
        * (1.0 - SL_PERCENT / 100.0)
    )

    tp1 = (
        entry
        * (1.0 + TP1_PERCENT / 100.0)
    )

    tp2 = (
        entry
        * (1.0 + TP2_PERCENT / 100.0)
    )

    tp3 = (
        entry
        * (1.0 + TP3_PERCENT / 100.0)
    )

    return {
        "entry": float(entry),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3)
    }


# =========================================================
# Rejection Candle
# =========================================================

def rejection_candle(
    row
):

    candle_range = float(
        row["high"]
        - row["low"]
    )

    if candle_range <= 0:

        return {
            "bullish": False,
            "body_ratio": 0.0,
            "close_upper_half": False,
            "lower_wick_ratio": 0.0
        }

    body = abs(
        float(row["close"])
        - float(row["open"])
    )

    body_ratio = (
        body
        / candle_range
    )

    lower_wick = (
        min(
            float(row["open"]),
            float(row["close"])
        )
        - float(row["low"])
    )

    lower_wick_ratio = (
        lower_wick
        / candle_range
    )

    close_position = (
        float(row["close"])
        - float(row["low"])
    ) / candle_range

    return {

        "bullish":
            float(row["close"])
            > float(row["open"]),

        "body_ratio":
            float(body_ratio),

        "close_upper_half":
            close_position >= 0.50,

        "lower_wick_ratio":
            float(lower_wick_ratio)
    }


# =========================================================
# Main Strategy Analysis
# =========================================================

def analyze_symbol(
    symbol: str
):

    normalized = normalize_symbol(
        symbol
    )

    logger.info(
        "SCAN START | requested=%s | normalized=%s",
        symbol,
        normalized
    )

    if not normalized.endswith(
        "/USDT"
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Only Binance Spot USDT "
                "symbols are supported."
            )
        )

    df = fetch_ohlcv(
        normalized
    )

    minimum_required = max(
        WMA_SLOW + 5,
        DROP_LOOKBACK + 5,
        SUPPORT_LOOKBACK + 5,
        VOLUME_LOOKBACK + 5,
        110
    )

    logger.info(
        "SCAN DATA | candles=%s | minimum=%s",
        len(df),
        minimum_required
    )

    if len(df) < minimum_required:

        logger.warning(
            "SCAN STOP | not enough candles"
        )

        return {

            "symbol": normalized,

            "signal":
                "NOT ENOUGH DATA",

            "message":
                (
                    f"Need at least "
                    f"{minimum_required} "
                    f"closed candles."
                ),

            "candles":
                len(df),

            "minimum_required":
                minimum_required,

            "timeframe":
                TIMEFRAME,

            "updated_at":
                datetime.now(
                    timezone.utc
                ).isoformat()
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
    # Drop
    # =====================================================

    drop_start = max(
        0,
        len(df)
        - DROP_LOOKBACK
        - 1
    )

    drop_end = (
        len(df) - 1
    )

    previous_window = df.iloc[
        drop_start:drop_end
    ]

    recent_high = float(
        previous_window[
            "high"
        ].max()
    )

    if recent_high > 0:

        drop_percent = (
            (
                recent_high
                - entry
            )
            / recent_high
            * 100.0
        )

    else:

        drop_percent = 0.0

    drop_condition = (
        drop_percent
        >= MIN_DROP_PERCENT
    )

    # =====================================================
    # Support
    # =====================================================

    support_window = df.iloc[
        -SUPPORT_LOOKBACK:
    ]

    support = float(
        support_window[
            "low"
        ].min()
    )

    if support > 0:

        support_distance_percent = (
            abs(
                entry
                - support
            )
            / support
            * 100.0
        )

    else:

        support_distance_percent = 0.0

    # =====================================================
    # Support Touch
    # =====================================================

    current_low = float(
        current["low"]
    )

    previous_low = float(
        previous["low"]
    )

    if support > 0:

        current_support_distance = (
            abs(
                current_low
                - support
            )
            / support
            * 100.0
        )

        previous_support_distance = (
            abs(
                previous_low
                - support
            )
            / support
            * 100.0
        )

    else:

        current_support_distance = 999.0
        previous_support_distance = 999.0

    support_touched = (
        current_support_distance
        <= SUPPORT_TOLERANCE_PERCENT

        or

        previous_support_distance
        <= SUPPORT_TOLERANCE_PERCENT
    )

    # =====================================================
    # Bullish Rejection
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
    # Volume
    # =====================================================

    volume_start = (
        len(df)
        - VOLUME_LOOKBACK
        - 1
    )

    volume_end = (
        len(df) - 1
    )

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
        volume_ratio
        >= VOLUME_MULTIPLIER
    )

    # =====================================================
    # WMA Turning Up
    # =====================================================

    wma_turning_up = (
        wma_fast
        > previous_wma_fast
    )

    # =====================================================
    # Conditions
    # =====================================================

    conditions = {

        "drop":
            bool(
                drop_condition
            ),

        "support_touch":
            bool(
                support_touched
            ),

        "bullish_rejection":
            bool(
                bullish_rejection
            ),

        "volume_confirmation":
            bool(
                volume_condition
            ),

        "wma50_turning_up":
            bool(
                wma_turning_up
            )
    }

    confirmed = all(
        conditions.values()
    )

    # =====================================================
    # Signal
    # =====================================================

    if confirmed:

        signal = (
            "EARLY SUPPORT REBOUND"
        )

        message = (
            "All support-rebound "
            "conditions are confirmed."
        )

        logger.info(
            "SIGNAL CONFIRMED | %s",
            normalized
        )

    else:

        signal = (
            "NO CONFIRMED SIGNAL"
        )

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

        logger.info(
            "NO SIGNAL | %s | failed=%s",
            normalized,
            failed
        )

    # =====================================================
    # Levels
    # =====================================================

    levels = calculate_levels(
        entry
    )

    result = {

        "symbol":
            normalized,

        "signal":
            signal,

        "message":
            message,

        "timeframe":
            TIMEFRAME,

        "market":
            "Binance Spot",

        "price":
            round(
                entry,
                12
            ),

        "entry":
            round(
                entry,
                12
            ),

        "drop_percent":
            round(
                drop_percent,
                3
            ),

        "recent_high":
            round(
                recent_high,
                12
            ),

        "support":
            round(
                support,
                12
            ),

        "support_distance_percent":
            round(
                support_distance_percent,
                3
            ),

        "volume_ratio":
            round(
                volume_ratio,
                3
            ),

        "average_volume":
            round(
                average_volume,
                8
            ),

        "current_volume":
            round(
                current_volume,
                8
            ),

        "wma50":
            round(
                wma_fast,
                12
            ),

        "wma103":
            round(
                wma_slow,
                12
            ),

        "previous_wma50":
            round(
                previous_wma_fast,
                12
            ),

        "candle_body_ratio":
            round(
                candle[
                    "body_ratio"
                ],
                3
            ),

        "lower_wick_ratio":
            round(
                candle[
                    "lower_wick_ratio"
                ],
                3
            ),

        "conditions":
            conditions,

        "levels": {

            "entry":
                round(
                    levels["entry"],
                    12
                ),

            "sl":
                round(
                    levels["sl"],
                    12
                ),

            "tp1":
                round(
                    levels["tp1"],
                    12
                ),

            "tp2":
                round(
                    levels["tp2"],
                    12
                ),

            "tp3":
                round(
                    levels["tp3"],
                    12
                )
        },

        "strategy": {

            "drop_lookback":
                DROP_LOOKBACK,

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

        "candles":
            len(df),

        "updated_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }

    logger.info(
        "SCAN COMPLETE | %s | signal=%s",
        normalized,
        signal
    )

    return result


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    logger.info(
        "HOME | serving index.html"
    )

    index_file = os.path.join(
        STATIC_DIR,
        "index.html"
    )

    if not os.path.isfile(
        index_file
    ):

        logger.error(
            "HOME ERROR | index.html not found"
        )

        raise HTTPException(
            status_code=404,
            detail=(
                "static/index.html not found."
            )
        )

    return FileResponse(
        index_file
    )


# =========================================================
# HEALTH
# =========================================================

@app.get("/api/health")
def health():

    logger.info(
        "HEALTH CHECK"
    )

    return {

        "status":
            "ok",

        "version":
            APP_VERSION,

        "binance_api":
            BINANCE_DATA_API,

        "timeframe":
            TIMEFRAME,

        "market":
            "Binance Spot",

        "strategy":
            (
                "Drop → Support → "
                "Rejection → Volume → "
                "WMA50 Up"
            )
    }


# =========================================================
# DIAGNOSTIC 1
# Test Binance ExchangeInfo
# =========================================================

@app.get("/api/test-binance")
def test_binance():

    logger.info(
        "DIAGNOSTIC | test-binance START"
    )

    try:

        data = binance_get(
            "/api/v3/exchangeInfo"
        )

        if not isinstance(
            data,
            dict
        ):

            return {

                "ok": False,

                "test":
                    "exchangeInfo",

                "message":
                    "Invalid Binance response."
            }

        symbols = data.get(
            "symbols",
            []
        )

        return {

            "ok": True,

            "test":
                "exchangeInfo",

            "api":
                BINANCE_DATA_API,

            "symbols_received":
                len(symbols),

            "message":
                (
                    "Binance public market "
                    "data API is reachable."
                ),

            "time":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }

    except Exception as e:

        logger.exception(
            "DIAGNOSTIC | test-binance FAILED"
        )

        return {

            "ok": False,

            "test":
                "exchangeInfo",

            "api":
                BINANCE_DATA_API,

            "error":
                str(e),

            "message":
                (
                    "Railway could not retrieve "
                    "Binance exchangeInfo."
                ),

            "time":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }


# =========================================================
# DIAGNOSTIC 2
# Test Binance Klines
# =========================================================

@app.get("/api/test-klines")
def test_klines(
    symbol: str = Query(
        "BTCUSDT"
    )
):

    logger.info(
        "DIAGNOSTIC | test-klines START | symbol=%s",
        symbol
    )

    normalized = normalize_symbol(
        symbol
    )

    raw_symbol = binance_symbol(
        normalized
    )

    try:

        data = binance_get(
            "/api/v3/klines",
            {
                "symbol":
                    raw_symbol,

                "interval":
                    TIMEFRAME,

                "limit":
                    10
            }
        )

        if not isinstance(
            data,
            list
        ):

            return {

                "ok": False,

                "test":
                    "klines",

                "symbol":
                    normalized,

                "message":
                    "Invalid klines response."
            }

        if len(data) == 0:

            return {

                "ok": False,

                "test":
                    "klines",

                "symbol":
                    normalized,

                "message":
                    "Binance returned zero candles."
            }

        last_candle = data[-1]

        return {

            "ok": True,

            "test":
                "klines",

            "symbol":
                normalized,

            "binance_symbol":
                raw_symbol,

            "timeframe":
                TIMEFRAME,

            "candles_received":
                len(data),

            "last_open":
                float(
                    last_candle[1]
                ),

            "last_high":
                float(
                    last_candle[2]
                ),

            "last_low":
                float(
                    last_candle[3]
                ),

            "last_close":
                float(
                    last_candle[4]
                ),

            "message":
                (
                    "Binance klines endpoint "
                    "is working."
                ),

            "time":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }

    except Exception as e:

        logger.exception(
            "DIAGNOSTIC | test-klines FAILED"
        )

        return {

            "ok": False,

            "test":
                "klines",

            "symbol":
                normalized,

            "binance_symbol":
                raw_symbol,

            "error":
                str(e),

            "message":
                (
                    "Could not retrieve Binance "
                    "candles."
                ),

            "time":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }


# =========================================================
# SYMBOLS
# =========================================================

@app.get("/api/symbols")
def symbols():

    logger.info(
        "SYMBOLS ENDPOINT START"
    )

    try:

        data = (
            get_spot_usdt_symbols()
        )

        logger.info(
            "SYMBOLS ENDPOINT COMPLETE | count=%s",
            len(data)
        )

        return {

            "count":
                len(data),

            "symbols":
                data
        }

    except Exception as e:

        logger.exception(
            "SYMBOLS ENDPOINT FAILED"
        )

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


# =========================================================
# SCAN
# =========================================================

@app.get("/api/scan")
def scan(
    symbol: str = Query(
        ...,
        description=(
            "Example: BTC/USDT"
        )
    )
):

    logger.info(
        "SCAN ENDPOINT START | symbol=%s",
        symbol
    )

    try:

        result = analyze_symbol(
            symbol
        )

        logger.info(
            "SCAN ENDPOINT COMPLETE | symbol=%s",
            symbol
        )

        return result

    except HTTPException:

        raise

    except Exception as e:

        logger.exception(
            "SCAN ENDPOINT FAILED | symbol=%s",
            symbol
        )

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


# =========================================================
# Startup
# =========================================================

@app.on_event(
    "startup"
)
async def startup_event():

    logger.info(
        "=================================================="
    )

    logger.info(
        "Support Rebound Scanner %s STARTING",
        APP_VERSION
    )

    logger.info(
        "BINANCE API: %s",
        BINANCE_DATA_API
    )

    logger.info(
        "TIMEFRAME: %s",
        TIMEFRAME
    )

    logger.info(
        "CANDLE LIMIT: %s",
        CANDLE_LIMIT
    )

    logger.info(
        "DROP LOOKBACK: %s",
        DROP_LOOKBACK
    )

    logger.info(
        "MIN DROP: %.2f%%",
        MIN_DROP_PERCENT
    )

    logger.info(
        "SUPPORT LOOKBACK: %s",
        SUPPORT_LOOKBACK
    )

    logger.info(
        "SUPPORT TOLERANCE: %.2f%%",
        SUPPORT_TOLERANCE_PERCENT
    )

    logger.info(
        "VOLUME MULTIPLIER: %.2f",
        VOLUME_MULTIPLIER
    )

    logger.info(
        "WMA FAST: %s",
        WMA_FAST
    )

    logger.info(
        "WMA SLOW: %s",
        WMA_SLOW
    )

    logger.info(
        "SL: %.2f%%",
        SL_PERCENT
    )

    logger.info(
        "TP1: %.2f%%",
        TP1_PERCENT
    )

    logger.info(
        "TP2: %.2f%%",
        TP2_PERCENT
    )

    logger.info(
        "TP3: %.2f%%",
        TP3_PERCENT
    )

    logger.info(
        "=================================================="
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

    logger.info(
        "Starting Uvicorn on port %s",
        port
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port
    )
