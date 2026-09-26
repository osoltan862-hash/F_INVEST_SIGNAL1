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
            "defaultType": "spot
