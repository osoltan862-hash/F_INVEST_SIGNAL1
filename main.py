import os
import logging
from datetime import datetime, timezone

import ccxt
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# ============================================================
# SUPPORT REBOUND SCANNER - WEB VERSION
# ============================================================
#
# الاستراتيجية:
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
#   ↓
# EARLY SUPPORT REBOUND
#
# Binance Spot فقط
# لا يوجد تنفيذ تلقائي للصفقات
# ============================================================


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("support_rebound_web")


# ============================================================
# SETTINGS
# ============================================================

TIMEFRAME = os.getenv("TIMEFRAME", "5m")
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "150"))

# -----------------------------
# Drop Detection
# -----------------------------

DROP_LOOKBACK = int(os.getenv("DROP_LOOKBACK", "24"))
MIN_DROP_PERCENT = float(
    os.getenv("MIN_DROP_PERCENT", "3.0")
)

# -----------------------------
# Support Detection
# -----------------------------

SUPPORT_LOOKBACK = int(
    os.getenv("SUPPORT_LOOKBACK", "12")
)

SUPPORT_TOLERANCE_PERCENT = float(
    os.getenv("SUPPORT_TOLERANCE_PERCENT", "0.20")
)

# -----------------------------
# Volume Confirmation
# -----------------------------

VOLUME_LOOKBACK = int(
    os.getenv("VOLUME_LOOKBACK", "11")
)

VOLUME_MULTIPLIER = float(
    os.getenv("VOLUME_MULTIPLIER", "1.30")
)

# -----------------------------
# WMA
# -----------------------------

WMA_FAST = int(
    os.getenv("WMA_FAST", "50")
)

WMA_SLOW = int(
    os.getenv("WMA_SLOW", "103")
)

# -----------------------------
# Entry / SL / TP
# -----------------------------
#
# حسب الإعداد الذي حددناه:
#
# SL  = 3%
# TP1 = 1%
# TP2 = 2%
# TP3 = 3%
#

SL_PERCENT = float(
    os.getenv("SL_PERCENT", "3.0")
)

TP1_PERCENT = float(
    os.getenv("TP1_PERCENT", "1.0")
)

TP2_PERCENT = float(
    os.getenv("TP2_PERCENT", "2.0")
)

TP3_PERCENT = float(
    os.getenv("TP3_PERCENT", "3.0")
)

# -----------------------------
# Candle Confirmation
# -----------------------------

MIN_BODY_RATIO = float(
    os.getenv("MIN_BODY_RATIO", "0.25")
)


# ============================================================
# BINANCE CONNECTION
# ============================================================

exchange = ccxt.binance(
    {
        "apiKey": os.getenv(
            "BINANCE_API_KEY",
            ""
        ),
        "secret": os.getenv(
            "BINANCE_SECRET_KEY",
            ""
        ),
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
        },
    }
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Support Rebound Scanner",
    version="1.0.0",
    description="Manual Binance Spot Support Rebound Scanner",
)


# ============================================================
# STATIC FILES
# ============================================================

STATIC_DIR = os.path.join(
    os.path.dirname(__file__),
    "static",
)

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)


# ============================================================
# SYMBOL NORMALIZATION
# ============================================================

def normalize_symbol(symbol: str) -> str:
    """
    يقبل:

    BTC/USDT
    BTCUSDT
    BTC-USDT

    ويحولها إلى:

    BTC/USDT
    """

    value = (
        symbol
        .strip()
        .upper()
        .replace(" ", "")
    )

    if value.endswith("-USDT"):
        value = (
            value[:-5]
            + "/USDT"
        )

    elif (
        value.endswith("USDT")
        and "/" not in value
    ):
        value = (
            value[:-4]
            + "/USDT"
        )

    return value


# ============================================================
# GET BINANCE SPOT USDT SYMBOLS
# ============================================================

def get_spot_usdt_symbols():

    markets = exchange.load_markets()

    symbols = []

    for symbol, market in markets.items():

        if not market.get(
            "active",
            True
        ):
            continue

        if market.get(
            "spot"
        ) is not True:
            continue

        if market.get(
            "quote"
        ) != "USDT":
            continue

        symbols.append(symbol)

    return sorted(symbols)


# ============================================================
# FETCH OHLCV
# ============================================================

def fetch_ohlcv(
    symbol: str
) -> pd.DataFrame:

    rows = exchange.fetch_ohlcv(
        symbol,
        timeframe=TIMEFRAME,
        limit=max(
            CANDLE_LIMIT,
            WMA_SLOW + 20,
        ),
    )

    if not rows:
        raise ValueError(
            "Binance returned no candle data."
        )

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

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True,
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = (
        df
        .dropna()
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # تجاهل الشمعة الحالية التي لم تغلق بعد
    # --------------------------------------------------------

    if len(df) > 2:

        df = df.iloc[:-1].copy()

    return df


# ============================================================
# WMA
# ============================================================

def wma(
    series: pd.Series,
    period: int
) -> pd.Series:

    weights = list(
        range(
            1,
            period + 1
        )
    )

    total = sum(weights)

    return series.rolling(
        period
    ).apply(
        lambda values:
        sum(
            value * weight
            for value, weight
            in zip(
                values,
                weights
            )
        ) / total,
        raw=True,
    )


# ============================================================
# CALCULATE ENTRY / SL / TP
# ============================================================

def calculate_levels(
    entry: float
):

    return {

        "entry": entry,

        "sl":
            entry
            * (
                1
                - SL_PERCENT / 100
            ),

        "tp1":
            entry
            * (
                1
                + TP1_PERCENT / 100
            ),

        "tp2":
            entry
            * (
                1
                + TP2_PERCENT / 100
            ),

        "tp3":
            entry
            * (
                1
                + TP3_PERCENT / 100
            ),
    }


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol: str
):

    # ========================================================
    # STRATEGY
    # ========================================================
    #
    # 1. Recent Drop
    # 2. Support / Low Area
    # 3. Support Touch
    # 4. Bullish Rejection
    # 5. Volume Confirmation
    # 6. WMA 50 Turning Up
    #
    # ========================================================

    df = fetch_ohlcv(symbol)

    minimum_needed = max(
        DROP_LOOKBACK + 3,
        SUPPORT_LOOKBACK + 3,
        VOLUME_LOOKBACK + 3,
        WMA_SLOW + 3,
        30,
    )

    if len(df) < minimum_needed:

        raise ValueError(
            f"Not enough closed candles. "
            f"Need {minimum_needed}, "
            f"received {len(df)}."
        )

    # ========================================================
    # WMA
    # ========================================================

    df["wma_fast"] = wma(
        df["close"],
        WMA_FAST,
    )

    df["wma_slow"] = wma(
        df["close"],
        WMA_SLOW,
    )

    # آخر شمعة مغلقة
    i = len(df) - 1

    current = df.iloc[i]

    previous = df.iloc[i - 1]

    # ========================================================
    # 1. DROP
    # ========================================================

    drop_start = max(
        0,
        i - DROP_LOOKBACK,
    )

    prior_window = df.iloc[
        drop_start:i
    ]

    prior_high = float(
        prior_window["high"].max()
    )

    if prior_high <= 0:

        raise ValueError(
            "Invalid prior high."
        )

    drop_percent = (
        (
            prior_high
            - float(current["close"])
        )
        / prior_high
    ) * 100

    drop_confirmed = (
        drop_percent
        >= MIN_DROP_PERCENT
    )

    # ========================================================
    # 2. SUPPORT
    # ========================================================

    support_start = max(
        0,
        i - SUPPORT_LOOKBACK,
    )

    support_window = df.iloc[
        support_start:i + 1
    ]

    support = float(
        support_window["low"].min()
    )

    tolerance = (
        support
        * SUPPORT_TOLERANCE_PERCENT
        / 100
    )

    current_low = float(
        current["low"]
    )

    previous_low = float(
        previous["low"]
    )

    current_touches_support = (
        current_low
        <= support + tolerance
    )

    previous_touches_support = (
        previous_low
        <= support + tolerance
    )

    support_confirmed = (
        current_touches_support
        or previous_touches_support
    )

    # ========================================================
    # 3. BULLISH REJECTION
    # ========================================================

    candle_open = float(
        current["open"]
    )

    candle_high = float(
        current["high"]
    )

    candle_low = float(
        current["low"]
    )

    candle_close = float(
        current["close"]
    )

    candle_range = (
        candle_high
        - candle_low
    )

    if candle_range > 0:

        body_ratio = (
            abs(
                candle_close
                - candle_open
            )
            / candle_range
        )

        close_position = (
            candle_close
            - candle_low
        ) / candle_range

        lower_wick = (
            min(
                candle_open,
                candle_close
            )
            - candle_low
        )

        lower_wick_ratio = (
            lower_wick
            / candle_range
        )

    else:

        body_ratio = 0

        close_position = 0

        lower_wick_ratio = 0

    bullish = (
        candle_close
        > candle_open
    )

    bullish_rejection = (
        bullish
        and body_ratio
        >= MIN_BODY_RATIO
        and close_position
        >= 0.50
        and lower_wick_ratio
        >= 0.15
    )

    # ========================================================
    # 4. VOLUME
    # ========================================================

    volume_start = max(
        0,
        i - VOLUME_LOOKBACK,
    )

    previous_volumes = (
        df.iloc[
            volume_start:i
        ]["volume"]
    )

    average_volume = (
        float(
            previous_volumes.mean()
        )
        if len(previous_volumes)
        else 0
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

        volume_ratio = 0

    volume_confirmed = (
        volume_ratio
        >= VOLUME_MULTIPLIER
    )

    # ========================================================
    # 5. WMA TREND
    # ========================================================

    if pd.notna(
        current["wma_fast"]
    ):

        current_wma_fast = float(
            current["wma_fast"]
        )

    else:

        current_wma_fast = None

    if pd.notna(
        previous["wma_fast"]
    ):

        previous_wma_fast = float(
            previous["wma_fast"]
        )

    else:

        previous_wma_fast = None

    if pd.notna(
        current["wma_slow"]
    ):

        current_wma_slow = float(
            current["wma_slow"]
        )

    else:

        current_wma_slow = None

    wma_turning_up = (
        current_wma_fast
        is not None
        and previous_wma_fast
        is not None
        and current_wma_fast
        > previous_wma_fast
    )

    # ========================================================
    # FINAL CONDITIONS
    # ========================================================

    conditions = {

        "drop":
            drop_confirmed,

        "support":
            support_confirmed,

        "bullish_rejection":
            bullish_rejection,

        "volume":
            volume_confirmed,

        "wma_turning_up":
            wma_turning_up,
    }

    confirmed = all(
        conditions.values()
    )

    # ========================================================
    # SIGNAL
    # ========================================================

    if confirmed:

        signal = (
            "EARLY SUPPORT REBOUND"
        )

    else:

        signal = (
            "NO CONFIRMED SIGNAL"
        )

    # ========================================================
    # LEVELS
    # ========================================================

    entry = candle_close

    levels = calculate_levels(
        entry
    )

    # ========================================================
    # SUPPORT DISTANCE
    # ========================================================

    if support:

        support_distance_percent = (
            (
                entry
                - support
            )
            / support
        ) * 100

    else:

        support_distance_percent = 0

    # ========================================================
    # RETURN
    # ========================================================

    return {

        "symbol":
            symbol,

        "timeframe":
            TIMEFRAME,

        "signal":
            signal,

        "confirmed":
            confirmed,

        "checked_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "candle_time":
            current[
                "datetime"
            ].isoformat(),

        "price":
            candle_close,

        "entry":
            levels["entry"],

        "sl":
            levels["sl"],

        "tp1":
            levels["tp1"],

        "tp2":
            levels["tp2"],

        "tp3":
            levels["tp3"],

        "drop_percent":
            drop_percent,

        "prior_high":
            prior_high,

        "support":
            support,

        "support_distance_percent":
            support_distance_percent,

        "volume_ratio":
            volume_ratio,

        "average_volume":
            average_volume,

        "current_volume":
            current_volume,

        "wma50":
            current_wma_fast,

        "wma103":
            current_wma_slow,

        "body_ratio":
            body_ratio,

        "close_position":
            close_position,

        "lower_wick_ratio":
            lower_wick_ratio,

        "conditions_met":
            sum(
                conditions.values()
            ),

        "conditions_total":
            len(conditions),

        "conditions":
            conditions,
    }


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():

    return FileResponse(
        os.path.join(
            STATIC_DIR,
            "index.html"
        )
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
def health():

    return {

        "status":
            "ok",

        "service":
            "Support Rebound Scanner",

        "timeframe":
            TIMEFRAME,

        "spot_only":
            True,

        "auto_trading":
            False,
    }


# ============================================================
# BINANCE SYMBOLS
# ============================================================

@app.get("/api/symbols")
def symbols():

    try:

        result = (
            get_spot_usdt_symbols()
        )

        return {

            "count":
                len(result),

            "symbols":
                result,
        }

    except Exception as exc:

        logger.exception(
            "Failed to load Binance symbols"
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Could not load "
                "Binance Spot symbols: "
                f"{exc}"
            ),
        )


# ============================================================
# SCAN SELECTED SYMBOL
# ============================================================

@app.get("/api/scan")
def scan(
    symbol: str = Query(
        ...,
        min_length=3,
        max_length=30,
    )
):

    try:

        normalized = (
            normalize_symbol(
                symbol
            )
        )

        markets = (
            exchange.load_markets()
        )

        market = markets.get(
            normalized
        )

        if not market:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"{normalized} "
                    "was not found "
                    "on Binance."
                ),
            )

        if (
            market.get("spot")
            is not True
            or market.get("quote")
            != "USDT"
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    "Please select a "
                    "Binance Spot "
                    "USDT symbol."
                ),
            )

        if (
            market.get(
                "active",
                True
            )
            is False
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    f"{normalized} "
                    "is not currently active."
                ),
            )

        return analyze_symbol(
            normalized
        )

    except HTTPException:

        raise

    except ccxt.BaseError as exc:

        logger.exception(
            "Binance error while scanning"
        )

        raise HTTPException(
            status_code=502,
            detail=(
                f"Binance data error: {exc}"
            ),
        )

    except Exception as exc:

        logger.exception(
            "Unexpected scan error"
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# LOCAL / RAILWAY START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
)
