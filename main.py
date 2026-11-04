
import os
import time
import logging
import threading
import html
import requests
import yfinance as yf
import pandas as pd

from flask import Flask, request
from datetime import datetime, timezone

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
session = requests.Session()

scanner_started = False
scanner_lock = threading.Lock()

# Prevent repeated alerts for the same candle and setup.
last_sent_signals = {}


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

    except Exception as e:
        logger.error("TELEGRAM SEND ERROR: %s", e)
        return False


# ==========================================
# MARKET DATA ENGINE
# ==========================================

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

        # Handle Yahoo Finance MultiIndex columns.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        required = [
            "Open", "High", "Low", "Close", "Volume"
        ]

        if not all(col in df.columns for col in required):
            logger.warning("Missing columns for %s", ticker)
            return None

        df = df[required].copy()
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()
        df = df.apply(pd.to_numeric, errors="coerce")
        df = df.dropna()

        # Build 4H candles from hourly data.
        if tf == "4h":

            df = df.resample("4h").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }).dropna()

        if len(df) < 60:
            logger.warning(
                "Insufficient candles: %s %s (%s)",
                ticker, tf, len(df)
            )
            return None

        return df

    except Exception as e:
        logger.exception("DATA ERROR %s %s: %s", ticker, tf, e)
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

        high_window = highs.iloc[
            i-left:i+right+1
        ]

        low_window = lows.iloc[
            i-left:i+right+1
        ]

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

    ema20 = close.ewm(
        span=20,
        adjust=False
    ).mean().iloc[-1]

    ema50 = close.ewm(
        span=50,
        adjust=False
    ).mean().iloc[-1]

    price = float(close.iloc[-1])

    if ema20 > ema50 and price > ema20:
        trend = "BULLISH"

    elif ema20 < ema50 and price < ema20:
        trend = "BEARISH"

    else:
        trend = "RANGING"

    swing_highs, swing_lows = find_swings(data)

    swing_high = (
        swing_highs[-1][1] if swing_highs else None
    )

    swing_low = (
        swing_lows[-1][1] if swing_lows else None
    )

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

    data = df.iloc[:-1].copy()

    if len(data) < 60:
        raise ValueError("Insufficient 15M candles")

    trend_info = analyze_trend(df)

    if trend_info["trend"] != direction:
        return {
            "confirmed": False,
            "reason": "15M trend does not agree",
            "trend_info": trend_info,
            "liquidity": "NO SWEEP",
            "bos": "NO BOS",
            "fvg": "NO FVG"
        }

    swing_highs, swing_lows = find_swings(data)

    # Reference levels must exist before the recent setup window.
    window_start = len(data) - 15

    previous_highs = [
        item for item in swing_highs
        if item[0] < window_start
    ]

    previous_lows = [
        item for item in swing_lows
        if item[0] < window_start
    ]

    if not previous_highs or not previous_lows:
        return {
            "confirmed": False,
            "reason": "No suitable previous swing levels",
            "trend_info": trend_info,
            "liquidity": "UNKNOWN",
            "bos": "NO BOS",
            "fvg": "NO FVG"
        }

    reference_high = previous_highs[-1][1]
    reference_low = previous_lows[-1][1]

    start = max(0, window_start)

    sweep_index = None
    liquidity = "NO SWEEP"
    bos = "NO BOS"

    # Find a liquidity sweep first.
    for i in range(start, len(data) - 1):

        candle = data.iloc[i]

        candle_low = float(candle["Low"])
        candle_high = float(candle["High"])
        candle_close = float(candle["Close"])

        if direction == "BULLISH":

            if (
                candle_low < reference_low
                and candle_close > reference_low
            ):
                sweep_index = i
                liquidity = "SWEPT LOW"

        elif direction == "BEARISH":

            if (
                candle_high > reference_high
                and candle_close < reference_high
            ):
                sweep_index = i
                liquidity = "SWEPT HIGH"

    # A structure break must happen AFTER the sweep.
    if sweep_index is not None:

        for i in range(sweep_index + 1, len(data)):

            candle_close = float(
                data.iloc[i]["Close"]
            )

            if direction == "BULLISH":

                if candle_close > reference_high:
                    bos = "BULLISH BOS"

            elif direction == "BEARISH":

                if candle_close < reference_low:
                    bos = "BEARISH BOS"

    # Fair Value Gap on the latest completed 15M candle.
    candle = data.iloc[-1]
    third = data.iloc[-3]

    bullish_fvg = (
        float(candle["Low"]) > float(third["High"])
    )

    bearish_fvg = (
        float(candle["High"]) < float(third["Low"])
    )

    if bullish_fvg:
        fvg = "BULLISH FVG"

    elif bearish_fvg:
        fvg = "BEARISH FVG"

    else:
        fvg = "NO FVG"

    if direction == "BULLISH":

        fvg_confirmed = bullish_fvg
        bos_confirmed = bos == "BULLISH BOS"

    else:

        fvg_confirmed = bearish_fvg
        bos_confirmed = bos == "BEARISH BOS"

    confirmed = (
        sweep_index is not None
        and bos_confirmed
        and fvg_confirmed
    )

    reasons = []

    if sweep_index is not None:
        reasons.append("Liquidity sweep confirmed")

    if bos_confirmed:
        reasons.append("Structure break confirmed")

    if fvg_confirmed:
        reasons.append("Matching FVG detected")

    if not confirmed:
        reasons.append(
            "Waiting for sweep, BOS and matching FVG"
        )

    return {
        "confirmed": confirmed,
        "reason": "; ".join(reasons),
        "trend_info": trend_info,
        "liquidity": liquidity,
        "bos": bos,
        "fvg": fvg,
        "swing_high": reference_high,
        "swing_low": reference_low
    }


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

            headlines.append(
                "• " + html.escape(str(title))
            )

            upper_title = str(title).upper()
            lower_title = str(title).lower()

            for keyword in HIGH_IMPACT:

                if keyword in upper_title:

                    high_impact = True

                    impact_reason = (
                        "⚠️ HIGH IMPACT NEWS: "
                        + html.escape(str(title))
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
                f"News bullish (+{news_score}). "
                "Positive headline sentiment."
            )

        elif news_score < -1:

            bias = "🔴 NEWS BEARISH"
            bias_text = (
                f"News bearish ({news_score}). "
                "Negative headline sentiment."
            )

        else:

            bias = "⚪ NEWS NEUTRAL"
            bias_text = "News neutral / mixed."

        return (
            headlines,
            bias,
            bias_text,
            high_impact,
            impact_reason
        )

    except Exception as e:

        logger.error("NEWS ERROR: %s", e)

        return (
            [],
            "⚪ NEWS UNKNOWN",
            "News unavailable",
            False,
            ""
        )


# ==========================================
# STOP LOSS AND TAKE PROFIT ENGINE
# ==========================================

def calc_sltp(sym, sig, entry, swing_low, swing_high):

    if sym == "XAUUSD":
        buffer = 3.0

    elif sym == "BTCUSD":
        buffer = 80.0

    elif sym == "USDJPY":
        buffer = 0.08

    else:
        buffer = entry * 0.0003

    if sig == "BUY":

        sl = swing_low - buffer

        if sl <= 0 or sl >= entry:
            sl = entry * 0.994

        if entry - sl > entry * 0.012:
            sl = entry * 0.994

        risk = entry - sl

        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.5
        tp3 = entry + risk * 4.0

        reason = (
            f"Below reference swing low {swing_low:.5f}"
        )

    else:

        sl = swing_high + buffer

        if sl <= entry:
            sl = entry * 1.006

        if sl - entry > entry * 0.012:
            sl = entry * 1.006

        risk = sl - entry

        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.5
        tp3 = entry - risk * 4.0

        reason = (
            f"Above reference swing high {swing_high:.5f}"
        )

    return sl, tp1, tp2, tp3, reason, risk


# ==========================================
# MULTI-TIMEFRAME CONFIRMATION ENGINE
# ==========================================

def analyze_mtf(sym):

    results = {}

    # Download all three timeframes.
    for tf in TFS:

        df = get_data(SYMBOLS[sym], tf)

        if df is None:
            return {
                "signal": None,
                "status": "DATA UNAVAILABLE",
                "reason": f"Could not get {tf} data",
                "results": results
            }

        results[tf] = {
            "df": df,
            "analysis": analyze_trend(df)
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
            "reason": (
                f"4H is {direction}, but "
                f"1H is {h1['trend']}"
            ),
            "results": results
        }

    # 15M must confirm trend and the SMC entry setup.
    entry = analyze_15m_entry(
        m15_df,
        direction
    )

    results["15m"]["entry"] = entry

    if not entry["confirmed"]:

        return {
            "signal": None,
            "status": "NO TRADE",
            "reason": entry["reason"],
            "results": results
        }

    signal = (
        "BUY" if direction == "BULLISH"
        else "SELL"
    )

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

    results = analysis["results"]

    msg = (
        f"🤖 <b>SMC AI 3.0 - {sym}</b>\n"
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
                f"Liquidity: {entry['liquidity']}\n"
                f"BOS: {entry['bos']}\n"
                f"FVG: {entry['fvg']}\n"
            )

    msg += (
        "\n<b>📰 NEWS:</b>\n"
        f"{bias}\n"
        f"{html.escape(bias_text)}\n"
    )

    if headlines:
        msg += "\n" + "\n".join(headlines[:4]) + "\n"

    if high_impact:

        msg += (
            f"\n{impact_reason}\n"
            "\n🚫 <b>NO TRADE</b>\n"
            "News filter blocked this signal.\n"
        )

        return msg

    if not analysis["signal"]:

        msg += (
            "\n🚫 <b>NO TRADE</b>\n"
            f"Reason: {html.escape(analysis['reason'])}\n"
            "\nWaiting for all timeframes to agree."
        )

        return msg

    sig = analysis["signal"]

    m15 = results["15m"]["analysis"]
    entry = results["15m"]["entry"]

    price = m15["price"]

    swing_high = entry["swing_high"]
    swing_low = entry["swing_low"]

    sl, tp1, tp2, tp3, sl_reason, risk = calc_sltp(
        sym,
        sig,
        price,
        swing_low,
        swing_high
    )

    dec = 2 if sym in ["XAUUSD", "BTCUSD"] else (
        3 if sym == "USDJPY" else 5
    )

    emoji = "🟢" if sig == "BUY" else "🔴"

    msg += (
        "\n<badge></badge>"
        f"\n{emoji} <b>CONFIRMED SIGNAL: {sig}</b>\n\n"
        "<b>CONFIRMATION CHECK:</b>\n"
        "✅ 4H main trend confirmed\n"
        "✅ 1H trend agrees with 4H\n"
        "✅ 15M trend agrees\n"
        "✅ 15M liquidity sweep confirmed\n"
        "✅ 15M BOS confirmed after sweep\n"
        "✅ 15M FVG confirmed\n\n"
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

def auto_scanner():

    while True:

        try:

            for sym in SYMBOLS:

                news_data = get_news_ai(sym)

                analysis = analyze_mtf(sym)

                if analysis["signal"]:

                    # Use the latest completed 15M candle as
                    # the signal's unique identifier.
                    m15_df = analysis["results"]["15m"]["df"]

                    candle_time = str(m15_df.index[-2])

                    key = (
                        sym,
                        analysis["signal"],
                        candle_time
                    )

                    if key not in last_sent_signals:

                        message = format_mtf_message(
                            sym,
                            analysis,
                            news_data
                        )

                        if send(message):
                            last_sent_signals[key] = True

                        time.sleep(1)

            logger.info("MTF scanner cycle completed")

            time.sleep(900)

        except Exception as e:

            logger.exception("SCANNER ERROR: %s", e)

            time.sleep(60)


# ==========================================
# TELEGRAM WEBHOOK SETUP
# ==========================================

def set_webhook():

    if not TOKEN:
        logger.warning("Missing Telegram TOKEN")
        return

    time.sleep(5)

    try:

        response = session.post(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            data={
                "url": f"{RENDER_URL}/webhook"
            },
            timeout=20
        )

        response.raise_for_status()

        logger.info("Webhook response: %s", response.text)

    except Exception as e:

        logger.error("WEBHOOK ERROR: %s", e)


# ==========================================
# TELEGRAM COMMAND HANDLER
# ==========================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        upd = request.get_json(silent=True) or {}

        msg = upd.get("message") or {}

        text_message = (
            msg.get("text") or ""
        ).strip()

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

            sym = (
                command[1].upper()
                if len(command) > 1
                else "XAUUSD"
            )

            if sym not in SYMBOLS:

                send(
                    "Invalid symbol. Example: /news XAUUSD",
                    cid
                )

            else:

                h, b, bt, hi, ir = get_news_ai(sym)

                message = (
                    f"📰 <b>NEWS: {sym}</b>\n\n"
                    f"{b}\n"
                    f"{html.escape(bt)}\n"
                )

                if hi:
                    message += f"\n{ir}\n"

                if h:
                    message += "\n" + "\n".join(h[:6])

                send(message, cid)

        elif action == "/signal":

            sym = (
                command[1].upper()
                if len(command) > 1
                else ""
            )

            if sym not in SYMBOLS:

                send(
                    "Invalid symbol. Example: /signal XAUUSD",
                    cid
                )

            else:

                send(
                    f"⏳ Checking {sym} on 4H, 1H and 15M...",
                    cid
                )

                news_data = get_news_ai(sym)

                analysis = analyze_mtf(sym)

                message = format_mtf_message(
                    sym,
                    analysis,
                    news_data
                )

                send(message, cid)

        else:

            send(
                "Unknown command. Use /start for instructions.",
                cid
            )

        return "ok", 200

    except Exception as e:

        logger.exception("WEBHOOK ERROR: %s", e)

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

        threading.Thread(
            target=set_webhook,
            daemon=True,
            name="WebhookSetup"
        ).start()

        threading.Thread(
            target=auto_scanner,
            daemon=True,
            name="MarketScanner"
        ).start()


# ==========================================
# APPLICATION START
# ==========================================

if __name__ == "__main__":

    start_background_services()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        use_reloader=False
    )
