import os
import time
import logging
import threading
import html
from collections import OrderedDict
from datetime import datetime, timezone

import requests
import yfinance as yf
import pandas as pd
from flask import Flask, request

# ==========================================
# CONFIGURATION
# ==========================================

TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("BOT_TOKEN")
    or os.getenv("TOKEN")
    or ""
).strip()

CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

RENDER_URL = (
    os.getenv("RENDER_EXTERNAL_URL")
    or "https://smc-bot-i2jt.onrender.com"
).rstrip("/")

# Optional Telegram webhook verification secret.
# If configured, use the same value in Telegram's webhook setup.
WEBHOOK_SECRET = (
    os.getenv("TELEGRAM_WEBHOOK_SECRET") or ""
).strip()

SYMBOLS = {
    "XAUUSD": "GC=F",
    "BTCUSD": "BTC-USD",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDJPY": "USDJPY=X",
}

TFS = ["4h", "1h", "15m"]

HIGH_IMPACT = [
    "CPI", "NFP", "NONFARM", "FOMC", "FED",
    "INTEREST RATE", "INFLATION", "GDP",
    "POWELL", "ECB", "BOE", "UNEMPLOYMENT"
]

BULLISH_WORDS = [
    "rise", "surge", "bullish", "rally",
    "gain", "strong", "hawkish", "demand"
]

BEARISH_WORDS = [
    "fall", "drop", "bearish", "crash",
    "decline", "weak", "dovish", "recession"
]

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger("SMC_BOT")

# Use a requests session for Telegram calls.
session = requests.Session()

scanner_started = False
scanner_lock = threading.Lock()

# Bounded in-memory duplicate-alert cache.
# This cache resets when the Render process restarts.
last_sent_signals = OrderedDict()
MAX_REMEMBERED_SIGNALS = 1000


# ==========================================
# HOME PAGE
# ==========================================

@app.route("/")
def home():
    return (
        "SMC AI TRADING BOT 3.0 "
        "| MULTI-TIMEFRAME CONFIRMATION ACTIVE | "
        + datetime.now(timezone.utc).isoformat()
    )


# ==========================================
# TELEGRAM MESSAGE ENGINE
# ==========================================

def send(msg, chat_id=None):
    cid = str(chat_id or CHAT_ID)

    if not TOKEN or not cid:
        logger.warning("Telegram TOKEN or CHAT_ID missing")
        return False

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        response = session.post(
            url,
            data={
                "chat_id": cid,
                "text": msg,
                "parse_mode": "HTML"
            },
            timeout=20
        )
        response.raise_for_status()

        result = response.json()
        if not result.get("ok"):
            logger.error("Telegram rejected message: %s", result)
            return False

        return True

    except Exception as exc:
        logger.error("TELEGRAM SEND ERROR: %s", exc)
        return False


# ==========================================
# MARKET DATA ENGINE
# ==========================================

def normalize_yfinance_columns(df):
    """Normalize yfinance's flat or MultiIndex OHLCV columns."""
    required = ["Open", "High", "Low", "Close", "Volume"]

    if not isinstance(df.columns, pd.MultiIndex):
        return df if all(col in df.columns for col in required) else None

    # Find the MultiIndex level containing the OHLCV field names.
    for level in range(df.columns.nlevels):
        names = list(df.columns.get_level_values(level))
        if all(col in names for col in required):
            result = df.copy()
            result.columns = names
            # In case flattening produces duplicate names, keep the first.
            result = result.loc[:, ~pd.Index(result.columns).duplicated()]
            if all(col in result.columns for col in required):
                return result

    return None


def get_data(ticker, tf):
    try:
        settings = {
            "15m": ("15m", "5d"),
            "1h": ("60m", "30d"),
            "4h": ("60m", "60d")
        }

        if tf not in settings:
            logger.warning("Unsupported timeframe: %s", tf)
            return None

        interval, period = settings[tf]

        df = yf.download(
            ticker,
            interval=interval,
            period=period,
            progress=False,
            auto_adjust=True,
            threads=False
        )

        if df is None or df.empty:
            logger.warning("No data for %s %s", ticker, tf)
            return None

        df = normalize_yfinance_columns(df)
        if df is None:
            logger.warning("Missing or unrecognized OHLCV columns for %s", ticker)
            return None

        required = ["Open", "High", "Low", "Close", "Volume"]
        df = df[required].copy()
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()
        df = df.apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        # Some Yahoo FX feeds report missing volume. Volume is not used
        # by this strategy, so safely replace missing volume with zero.
        df["Volume"] = df["Volume"].fillna(0)

        # Build 4H candles from hourly data.
        if tf == "4h":
            df = df.resample(
                "4h",
                label="right",
                closed="right"
            ).agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }).dropna(subset=["Open", "High", "Low", "Close"])

        if len(df) < 60:
            logger.warning(
                "Insufficient candles: %s %s (%s)",
                ticker, tf, len(df)
            )
            return None

        return df

    except Exception as exc:
        logger.exception("DATA ERROR %s %s: %s", ticker, tf, exc)
        return None


# ==========================================
# SWING HIGH AND LOW DETECTION
# ==========================================

def find_swings(df, left=2, right=2):
    highs = df["High"].astype(float)
    lows = df["Low"].astype(float)

    swing_highs = []
    swing_lows = []

    for i in range(left, len(df) - right):
        high_window = highs.iloc[i-left:i+right+1]
        low_window = lows.iloc[i-left:i+right+1]

        current_high = highs.iloc[i]
        current_low = lows.iloc[i]

        if (
            current_high == high_window.max()
            and (high_window == current_high).sum() == 1
        ):
            swing_highs.append((i, float(current_high)))

        if (
            current_low == low_window.min()
            and (low_window == current_low).sum() == 1
        ):
            swing_lows.append((i, float(current_low)))

    return swing_highs, swing_lows


# ==========================================
# TIMEFRAME TREND ANALYSIS
# ==========================================

def analyze_trend(df):
    if df is None or len(df) < 60:
        raise ValueError("Insufficient candles for trend analysis")

    # Ignore the latest potentially incomplete candle.
    data = df.iloc[:-1].copy()

    if len(data) < 55:
        raise ValueError("Insufficient completed candles")

    close = data["Close"].astype(float)

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    price = float(close.iloc[-1])

    if ema20 > ema50 and price > ema20:
        trend = "BULLISH"
    elif ema20 < ema50 and price < ema20:
        trend = "BEARISH"
    else:
        trend = "RANGING"

    swing_highs, swing_lows = find_swings(data)

    swing_high = swing_highs[-1][1] if swing_highs else None
    swing_low = swing_lows[-1][1] if swing_lows else None

    bos = "NO BOS"
    if swing_high is not None and price > swing_high:
        bos = "BULLISH BOS"
    elif swing_low is not None and price < swing_low:
        bos = "BEARISH BOS"

    return {
        "trend": trend,
        "price": price,
        "ema20": float(ema20),
        "ema50": float(ema50),
        "bos": bos,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "last": data.iloc[-1],
        "data": data
    }


# ==========================================
# 15M SMC ENTRY CONFIRMATION
# ==========================================

def analyze_15m_entry(df, direction):
    if df is None or len(df) < 61:
        raise ValueError("Insufficient 15M candles")

    # Exclude the latest potentially incomplete candle.
    data = df.iloc[:-1].copy()
    trend_info = analyze_trend(df)

    result = {
        "confirmed": False,
        "reason": "",
        "trend_info": trend_info,
        "liquidity": "NO SWEEP",
        "bos": "NO BOS",
        "fvg": "NO FVG",
        "swing_high": None,
        "swing_low": None
    }

    if trend_info["trend"] != direction:
        result["reason"] = "15M trend does not agree"
        return result

    swing_highs, swing_lows = find_swings(data)

    # Use swing levels established before the recent setup window.
    window_start = len(data) - 15
    previous_highs = [
        item for item in swing_highs if item[0] < window_start
    ]
    previous_lows = [
        item for item in swing_lows if item[0] < window_start
    ]

    if not previous_highs or not previous_lows:
        result["reason"] = "No suitable previous swing levels"
        return result

    reference_high = previous_highs[-1][1]
    reference_low = previous_lows[-1][1]

    result["swing_high"] = reference_high
    result["swing_low"] = reference_low

    # Find the most recent valid sweep in the recent setup window.
    sweep_index = None

    for i in range(window_start, len(data)):
        candle = data.iloc[i]
        candle_low = float(candle["Low"])
        candle_high = float(candle["High"])
        candle_close = float(candle["Close"])

        if direction == "BULLISH":
            if candle_low < reference_low and candle_close > reference_low:
                sweep_index = i

        elif direction == "BEARISH":
            if candle_high > reference_high and candle_close < reference_high:
                sweep_index = i

    if sweep_index is None:
        result["reason"] = "Waiting for liquidity sweep"
        return result

    result["liquidity"] = (
        "SWEPT LOW" if direction == "BULLISH" else "SWEPT HIGH"
    )

    # Find the first structure break after the sweep.
    bos_index = None

    for i in range(sweep_index + 1, len(data)):
        candle_close = float(data.iloc[i]["Close"])

        if direction == "BULLISH" and candle_close > reference_high:
            bos_index = i
            break

        if direction == "BEARISH" and candle_close < reference_low:
            bos_index = i
            break

    if bos_index is None:
        result["reason"] = "Liquidity sweep found; waiting for BOS"
        return result

    result["bos"] = (
        "BULLISH BOS" if direction == "BULLISH" else "BEARISH BOS"
    )

    # A three-candle FVG needs to complete after the BOS candle.
    latest_index = len(data) - 1
    if latest_index < bos_index + 2:
        result["reason"] = "BOS found; waiting for a post-BOS FVG"
        return result

    candle = data.iloc[-1]
    third = data.iloc[-3]

    bullish_fvg = float(candle["Low"]) > float(third["High"])
    bearish_fvg = float(candle["High"]) < float(third["Low"])

    if bullish_fvg:
        result["fvg"] = "BULLISH FVG"
    elif bearish_fvg:
        result["fvg"] = "BEARISH FVG"

    fvg_confirmed = (
        bullish_fvg if direction == "BULLISH" else bearish_fvg
    )

    result["confirmed"] = fvg_confirmed

    if fvg_confirmed:
        result["reason"] = (
            "Liquidity sweep, BOS and post-BOS matching FVG confirmed"
        )
    else:
        result["reason"] = "Waiting for matching post-BOS FVG"

    return result


# ==========================================
# NEWS ANALYSIS ENGINE
# ==========================================

def get_news_ai(sym):
    try:
        headlines = []
        news_score = 0
        high_impact = False
        impact_reason = ""

        ticker = yf.Ticker(SYMBOLS[sym])
        news = ticker.news or []

        for item in news[:8]:
            title = ""

            if isinstance(item, dict):
                title = item.get("title", "")
                content = item.get("content", {})
                if isinstance(content, dict):
                    title = content.get("title", title)

            if not title:
                continue

            title = str(title)
            headlines.append("• " + html.escape(title))

            upper_title = title.upper()
            lower_title = title.lower()

            for keyword in HIGH_IMPACT:
                if keyword in upper_title:
                    high_impact = True
                    impact_reason = (
                        "⚠️ POSSIBLE HIGH-IMPACT NEWS HEADLINE: "
                        + html.escape(title)
                    )
                    break

            for word in BULLISH_WORDS:
                if word in lower_title:
                    news_score += 1

            for word in BEARISH_WORDS:
                if word in lower_title:
                    news_score -= 1

        if news_score > 1:
            bias = "🟢 NEWS BULLISH"
            bias_text = (
                f"Headline keyword score is positive (+{news_score}); "
                "this is a basic text filter, not verified sentiment."
            )
        elif news_score < -1:
            bias = "🔴 NEWS BEARISH"
            bias_text = (
                f"Headline keyword score is negative ({news_score}); "
                "this is a basic text filter, not verified sentiment."
            )
        else:
            bias = "⚪ NEWS NEUTRAL"
            bias_text = "Headline keyword score is neutral or mixed."

        return headlines, bias, bias_text, high_impact, impact_reason

    except Exception as exc:
        logger.error("NEWS ERROR: %s", exc)
        return [], "⚪ NEWS UNKNOWN", "News unavailable", False, ""


# ==========================================
# STOP LOSS AND TAKE PROFIT CALCULATION
# ==========================================

def calc_sltp(sym, sig, entry, swing_low, swing_high):
    if entry is None or entry <= 0:
        raise ValueError("Invalid entry reference price")

    if sig == "BUY":
        if swing_low is None:
            raise ValueError("Missing swing low for BUY calculation")

        if sym == "XAUUSD":
            buffer = 3.0
        elif sym == "BTCUSD":
            buffer = 80.0
        elif sym == "USDJPY":
            buffer = 0.08
        else:
            buffer = entry * 0.0003

        sl = float(swing_low) - buffer
        if sl <= 0 or sl >= entry or entry - sl > entry * 0.012:
            sl = entry * 0.994

        risk = entry - sl
        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.5
        tp3 = entry + risk * 4.0
        reason = "Below reference swing low"

    elif sig == "SELL":
        if swing_high is None:
            raise ValueError("Missing swing high for SELL calculation")

        if sym == "XAUUSD":
            buffer = 3.0
        elif sym == "BTCUSD":
            buffer = 80.0
        elif sym == "USDJPY":
            buffer = 0.08
        else:
            buffer = entry * 0.0003

        sl = float(swing_high) + buffer
        if sl <= entry or sl - entry > entry * 0.012:
            sl = entry * 1.006

        risk = sl - entry
        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.5
        tp3 = entry - risk * 4.0
        reason = "Above reference swing high"

    else:
        raise ValueError("Signal must be BUY or SELL")

    return sl, tp1, tp2, tp3, reason, risk


# ==========================================
# MULTI-TIMEFRAME CONFIRMATION ENGINE
# ==========================================

def analyze_mtf(sym):
    if sym not in SYMBOLS:
        return {
            "signal": None,
            "status": "INVALID SYMBOL",
            "reason": f"Unsupported symbol: {sym}",
            "results": {}
        }

    results = {}

    for tf in TFS:
        df = get_data(SYMBOLS[sym], tf)

        if df is None:
            return {
                "signal": None,
                "status": "DATA UNAVAILABLE",
                "reason": f"Could not get {tf} data",
                "results": results
            }

        try:
            results[tf] = {
                "df": df,
                "analysis": analyze_trend(df)
            }
        except Exception as exc:
            logger.exception("TREND ANALYSIS ERROR %s %s", sym, tf)
            return {
                "signal": None,
                "status": "ANALYSIS ERROR",
                "reason": f"{tf} analysis failed: {exc}",
                "results": results
            }

    h4 = results["4h"]["analysis"]
    h1 = results["1h"]["analysis"]
    m15_df = results["15m"]["df"]

    # 4H establishes the main direction.
    direction = h4["trend"]

    if direction == "RANGING":
        return {
            "signal": None,
            "status": "NO TRADE",
            "reason": "4H main trend is ranging",
            "results": results
        }

    # 1H must agree with 4H.
    if h1["trend"] != direction:
        return {
            "signal": None,
            "status": "NO TRADE",
            "reason": f"4H is {direction}, but 1H is {h1['trend']}",
            "results": results
        }

    # 15M must confirm trend and the SMC setup.
    try:
        entry = analyze_15m_entry(m15_df, direction)
    except Exception as exc:
        logger.exception("15M ENTRY ERROR %s", sym)
        return {
            "signal": None,
            "status": "ANALYSIS ERROR",
            "reason": f"15M entry analysis failed: {exc}",
            "results": results
        }

    results["15m"]["entry"] = entry

    if not entry["confirmed"]:
        return {
            "signal": None,
            "status": "NO TRADE",
            "reason": entry["reason"],
            "results": results
        }

    signal = "BUY" if direction == "BULLISH" else "SELL"

    return {
        "signal": signal,
        "status": "ALL TIMEFRAMES CONFIRMED",
        "reason": "4H + 1H + 15M confirmation passed",
        "results": results
    }


# ==========================================
# SIGNAL MESSAGE FORMATTER
# ==========================================

def format_mtf_message(sym, analysis, news_data):
    headlines, bias, bias_text, high_impact, impact_reason = news_data
    results = analysis.get("results", {})

    msg = (
        f"🤖 <b>SMC AI 3.0 - {html.escape(sym)}</b>\n"
        "<b>MULTI-TIMEFRAME ANALYSIS</b>\n\n"
    )

    if "4h" in results:
        h4 = results["4h"]["analysis"]
        msg += (
            f"<b>4H:</b> {h4['trend']}\n"
            f"EMA20: {h4['ema20']:.5f}\n"
            f"EMA50: {h4['ema50']:.5f}\n\n"
        )

    if "1h" in results:
        h1 = results["1h"]["analysis"]
        msg += (
            f"<b>1H:</b> {h1['trend']}\n"
            f"EMA20: {h1['ema20']:.5f}\n"
            f"EMA50: {h1['ema50']:.5f}\n\n"
        )

    if "15m" in results:
        m15 = results["15m"]["analysis"]
        msg += (
            f"<b>15M:</b> {m15['trend']}\n"
            f"EMA20: {m15['ema20']:.5f}\n"
            f"EMA50: {m15['ema50']:.5f}\n"
        )

        entry = results["15m"].get("entry")
        if entry:
            msg += (
                f"Liquidity: {entry.get('liquidity', 'UNKNOWN')}\n"
                f"BOS: {entry.get('bos', 'UNKNOWN')}\n"
                f"FVG: {entry.get('fvg', 'UNKNOWN')}\n"
            )

    msg += (
        "\n<b>📰 NEWS:</b>\n"
        f"{bias}\n"
        f"{html.escape(str(bias_text))}\n"
    )

    if headlines:
        msg += "\n" + "\n".join(headlines[:4]) + "\n"

    # Headline keywords are only a warning filter. They are not a live
    # economic calendar and cannot guarantee detection of scheduled events.
    if high_impact:
        msg += (
            f"\n{impact_reason}\n"
            "\n🚫 <b>NO TRADE</b>\n"
            "A potentially relevant headline was detected.\n"
        )
        return msg

    if not analysis.get("signal"):
        msg += (
            "\n🚫 <b>NO TRADE</b>\n"
            f"Reason: {html.escape(str(analysis.get('reason', 'No setup')))}\n"
            "\nWaiting for all timeframes to agree."
        )
        return msg

    sig = analysis["signal"]
    m15 = results["15m"]["analysis"]
    entry = results["15m"]["entry"]

    price = m15["price"]
    swing_high = entry.get("swing_high")
    swing_low = entry.get("swing_low")

    try:
        sl, tp1, tp2, tp3, sl_reason, risk = calc_sltp(
            sym, sig, price, swing_low, swing_high
        )
    except Exception as exc:
        logger.exception("SL/TP CALCULATION ERROR %s", sym)
        return msg + (
            "\n⚠️ <b>PRICE LEVELS NOT AVAILABLE</b>\n"
            f"{html.escape(str(exc))}\n"
            "The analysis signal is not accompanied by calculated levels."
        )

    dec = 2 if sym in ["XAUUSD", "BTCUSD"] else (
        3 if sym == "USDJPY" else 5
    )
    emoji = "🟢" if sig == "BUY" else "🔴"

    msg += (
        f"\n{emoji} <b>CONFIRMED SIGNAL: {sig}</b>\n\n"
        "<b>CONFIRMATION CHECK:</b>\n"
        "✅ 4H main trend confirmed\n"
        "✅ 1H trend agrees with 4H\n"
        "✅ 15M trend agrees\n"
        "✅ 15M liquidity sweep confirmed\n"
        "✅ 15M BOS confirmed after sweep\n"
        "✅ 15M post-BOS FVG confirmed\n\n"
        "<b>ILLUSTRATIVE PRICE LEVELS:</b>\n"
        f"Entry reference: {price:.{dec}f}\n"
        f"Stop Loss: {sl:.{dec}f}\n"
        f"TP1: {tp1:.{dec}f}\n"
        f"TP2: {tp2:.{dec}f}\n"
        f"TP3: {tp3:.{dec}f}\n\n"
        f"SL reasoning: {html.escape(sl_reason)}\n"
        f"Planned price distance: {risk:.{dec}f}\n\n"
        "⚠️ Educational analysis only. "
        "These are estimates, not guaranteed prices or returns. "
        "Test with historical data or a demo environment."
    )
    return msg


# ==========================================
# AUTOMATIC MARKET SCANNER
# ==========================================

def remember_signal(key):
    last_sent_signals[key] = True
    last_sent_signals.move_to_end(key)

    while len(last_sent_signals) > MAX_REMEMBERED_SIGNALS:
        last_sent_signals.popitem(last=False)


def auto_scanner():
    while True:
        try:
            for sym in SYMBOLS:
                try:
                    news_data = get_news_ai(sym)
                    analysis = analyze_mtf(sym)

                    if analysis.get("signal"):
                        completed_data = (
                            analysis["results"]["15m"]["analysis"]["data"]
                        )
                        candle_time = str(completed_data.index[-1])

                        key = (sym, analysis["signal"], candle_time)

                        if key not in last_sent_signals:
                            message = format_mtf_message(
                                sym, analysis, news_data
                            )

                            if send(message):
                                remember_signal(key)

                            time.sleep(1)

                except Exception as exc:
                    # One symbol failing must not stop the other symbols.
                    logger.exception("SCANNER SYMBOL ERROR %s: %s", sym, exc)

            logger.info("MTF scanner cycle completed")
            time.sleep(900)

        except Exception as exc:
            logger.exception("SCANNER ERROR: %s", exc)
            time.sleep(60)


# ==========================================
# TELEGRAM WEBHOOK SETUP
# ==========================================

def set_webhook():
    if not TOKEN:
        logger.warning("Missing Telegram TOKEN")
        return

    if not RENDER_URL.startswith("https://"):
        logger.warning("RENDER_URL must be a public HTTPS URL")
        return

    time.sleep(5)

    try:
        payload = {"url": f"{RENDER_URL}/webhook"}

        if WEBHOOK_SECRET:
            payload["secret_token"] = WEBHOOK_SECRET

        response = session.post(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            data=payload,
            timeout=20
        )
        response.raise_for_status()

        result = response.json()
        if not result.get("ok"):
            logger.error("Webhook setup rejected: %s", result)
        else:
            logger.info("Telegram webhook configured successfully")

    except Exception as exc:
        logger.error("WEBHOOK SETUP ERROR: %s", exc)


# ==========================================
# TELEGRAM COMMAND HANDLER
# ==========================================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if WEBHOOK_SECRET:
            received_secret = request.headers.get(
                "X-Telegram-Bot-Api-Secret-Token", ""
            )
            if received_secret != WEBHOOK_SECRET:
                return "Unauthorized", 403

        upd = request.get_json(silent=True) or {}
        msg = upd.get("message") or {}

        text_message = (msg.get("text") or "").strip()
        cid = msg.get("chat", {}).get("id")

        if not cid:
            return "ok", 200

        command = text_message.split()
        if not command:
            return "ok", 200

        action = command[0].lower().split("@")[0]

        if action == "/start":
            send(
                "🤖 <b>SMC AI TRADING BOT 3.0</b>\n\n"
                "<b>Multi-Timeframe Confirmation:</b>\n"
                "4H = Main trend\n"
                "1H = Trend confirmation\n"
                "15M = SMC entry confirmation\n\n"
                "Available commands:\n"
                "/signal XAUUSD\n"
                "/signal BTCUSD\n"
                "/signal EURUSD\n"
                "/signal GBPUSD\n"
                "/signal NZDUSD\n"
                "/signal USDJPY\n"
                "/news XAUUSD\n\n"
                "Signals-only. No automatic trade execution.",
                cid
            )

        elif action == "/news":
            sym = command[1].upper() if len(command) > 1 else "XAUUSD"

            if sym not in SYMBOLS:
                send("Invalid symbol. Example: /news XAUUSD", cid)
            else:
                h, b, bt, hi, ir = get_news_ai(sym)
                message = (
                    f"📰 <b>NEWS: {html.escape(sym)}</b>\n\n"
                    f"{b}\n"
                    f"{html.escape(str(bt))}\n"
                )

                if hi:
                    message += f"\n{ir}\n"
                if h:
                    message += "\n" + "\n".join(h[:6])

                send(message, cid)

        elif action == "/signal":
            sym = command[1].upper() if len(command) > 1 else ""

            if sym not in SYMBOLS:
                send("Invalid symbol. Example: /signal XAUUSD", cid)
            else:
                send(
                    f"⏳ Checking {sym} on 4H, 1H and 15M...",
                    cid
                )

                news_data = get_news_ai(sym)
                analysis = analyze_mtf(sym)
                message = format_mtf_message(sym, analysis, news_data)
                send(message, cid)

        else:
            send("Unknown command. Use /start for instructions.", cid)

        return "ok", 200

    except Exception as exc:
        logger.exception("WEBHOOK ERROR: %s", exc)
        return "ok", 200


# ==========================================
# START BACKGROUND SERVICES
# ==========================================

def start_background_services():
    global scanner_started

    with scanner_lock:
        if scanner_started:
            return

        scanner_started = True

        if TOKEN:
            threading.Thread(
                target=set_webhook,
                daemon=True,
                name="WebhookSetup"
            ).start()
        else:
            logger.warning("Telegram token missing; webhook setup skipped")

        if TOKEN and CHAT_ID:
            threading.Thread(
                target=auto_scanner,
                daemon=True,
                name="MarketScanner"
            ).start()
        else:
            logger.warning(
                "TOKEN or CHAT_ID missing; automatic scanner not started"
            )


# Start services when imported by Gunicorn as well as when run directly.
# On Render, use ONE Gunicorn worker to avoid duplicate scanner processes.
if os.getenv("DISABLE_BACKGROUND_SERVICES", "0") != "1":
    start_background_services()


# ==========================================
# APPLICATION START
# ==========================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        use_reloader=False
    )
