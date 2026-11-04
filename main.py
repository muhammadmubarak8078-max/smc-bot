import os, time, requests, yfinance as yf, pandas as pd
from flask import Flask, request
from threading import Thread
from datetime import datetime

TOKEN = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") or "https://smc-bot-i2jt.onrender.com"

SYMBOLS = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "NZDUSD": "NZDUSD=X", "USDJPY": "USDJPY=X"}
TFS = ["15m", "1h", "4h"]
HIGH_IMPACT = ["CPI", "NFP", "NONFARM", "FOMC", "FED", "INTEREST RATE", "INFLATION", "GDP", "POWELL", "ECB", "BOE", "UNEMPLOYMENT"]
BULLISH_WORDS = ["rise", "surge", "bullish", "rally", "gain", "strong", "hawkish", "demand"]
BEARISH_WORDS = ["fall", "drop", "bearish", "crash", "decline", "weak", "dovish", "recession"]

app = Flask(__name__)
@app.route('/')
def home():
    return f"SMC AI + NEWS + SLTP 2.0 LIVE - {datetime.now()}"

def send(msg, chat_id=None):
    try:
        cid = str(chat_id or CHAT_ID)
        if not TOKEN or not cid: return
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": cid, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e:
        print(f"SEND ERR {e}")

# ==========================================
# SMC BOT 2.0 - IMPROVED MARKET DATA ENGINE
# ==========================================
def get_data(ticker, tf):
    try:
        if tf == "15m":
            df = yf.download(ticker, interval="15m", period="5d", progress=False, auto_adjust=True)
        elif tf == "1h":
            df = yf.download(ticker, interval="60m", period="30d", progress=False, auto_adjust=True)
        elif tf == "4h":
            df = yf.download(ticker, interval="60m", period="60d", progress=False, auto_adjust=True)
        else:
            return None
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in df.columns for c in required):
            return None
        df = df[required].copy()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna()
        if tf == "4h":
            df = df.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
        if len(df) < 60:
            return None
        return df
    except Exception as e:
        print(f"DATA ERR {ticker} {tf}: {e}")
        return None

def find_swings(df, left=2, right=2):
    highs = df["High"].astype(float)
    lows = df["Low"].astype(float)
    swing_highs = []
    swing_lows = []
    for i in range(left, len(df) - right):
        high_window = highs.iloc[i-left:i+right+1]
        low_window = lows.iloc[i-left:i+right+1]
        if highs.iloc[i] == high_window.max():
            if (high_window == highs.iloc[i]).sum() == 1:
                swing_highs.append((i, float(highs.iloc[i])))
        if lows.iloc[i] == low_window.min():
            if (low_window == lows.iloc[i]).sum() == 1:
                swing_lows.append((i, float(lows.iloc[i])))
    return swing_highs, swing_lows

def get_news_ai(sym):
    try:
        headlines = []
        news_score = 0
        high_impact = False
        impact_reason = ""
        try:
            ticker = yf.Ticker(SYMBOLS[sym])
            news = ticker.news or []
            for n in news[:5]:
                title = ""
                if isinstance(n, dict):
                    title = n.get("title", "")
                    if "content" in n and isinstance(n["content"], dict):
                        title = n["content"].get("title", title)
                if title:
                    headlines.append(f"• {title}")
                    t = title.upper()
                    for kw in HIGH_IMPACT:
                        if kw in t:
                            high_impact = True
                            impact_reason = f"⚠️ HIGH IMPACT NEWS: {title}"
                    tl = title.lower()
                    for w in BULLISH_WORDS:
                        if w in tl: news_score += 1
                    for w in BEARISH_WORDS:
                        if w in tl: news_score -= 1
        except Exception as e:
            print(f"YF NEWS ERR {e}")
        if news_score > 1:
            bias, bias_text = "🟢 NEWS BULLISH", f"News bullish (+{news_score}). Strength in headlines."
        elif news_score < -1:
            bias, bias_text = "🔴 NEWS BEARISH", f"News bearish ({news_score}). Weakness in headlines."
        else:
            bias, bias_text = "⚪ NEWS NEUTRAL", "News neutral / mixed."
        return headlines, bias, bias_text, high_impact, impact_reason
    except Exception as e:
        print(f"NEWS ERR {e}")
        return [], "⚪ NEWS UNKNOWN", "News unavailable", False, ""

# --- NEW TIGHT SL / TP - USES REAL SWING ---
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
        # Safety cap: max risk 1.2% - if swing too far, use tighter SL
        if entry - sl > entry * 0.012:
            sl = entry - (entry * 0.006)
        risk = entry - sl
        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.5
        tp3 = entry + risk * 4.0
        sl_reason = f"SL below swept swing low {swing_low:.2f} + buffer - if price closes below, idea invalid"
    else:
        sl = swing_high + buffer
        if sl - entry > entry * 0.012:
            sl = entry + (entry * 0.006)
        risk = sl - entry
        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.5
        tp3 = entry - risk * 4.0
        sl_reason = f"SL above swept swing high {swing_high:.2f} + buffer - if price closes above, idea invalid"
    return sl, tp1, tp2, tp3, sl_reason, risk

# ==========================================
# IMPROVED SMC ANALYSIS 2.0
# ==========================================
def analyze(df):
    try:
        if df is None or len(df) < 60:
            raise ValueError("Not enough candles")
        data = df.iloc[:-1].copy()
        if len(data) < 55:
            raise ValueError("Not enough completed candles")
        close = data["Close"].astype(float)
        last = data.iloc[-1]
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        if ema20 > ema50 and float(last["Close"]) > ema20:
            trend = "UPTREND"
        elif ema20 < ema50 and float(last["Close"]) < ema20:
            trend = "DOWNTREND"
        else:
            trend = "RANGING"
        swing_highs, swing_lows = find_swings(data)
        if not swing_highs or not swing_lows:
            raise ValueError("No confirmed swing points")
        swing_high = swing_highs[-1][1]
        swing_low = swing_lows[-1][1]
        candle = data.iloc[-1]
        third = data.iloc[-3]
        candle_close = float(candle["Close"])
        candle_low = float(candle["Low"])
        candle_high = float(candle["High"])
        swept_low = (candle_low < swing_low and candle_close > swing_low)
        swept_high = (candle_high > swing_high and candle_close < swing_high)
        liquidity = "SWEPT LOW" if swept_low else "SWEPT HIGH" if swept_high else "NO SWEEP"
        if candle_close > swing_high:
            bos = "BULLISH BOS"
        elif candle_close < swing_low:
            bos = "BEARISH BOS"
        else:
            bos = "NO BOS"
        bullish_fvg = (float(candle["Low"]) > float(third["High"]))
        bearish_fvg = (float(candle["High"]) < float(third["Low"]))
        fvg = "BULLISH FVG" if bullish_fvg else "BEARISH FVG" if bearish_fvg else "NO FVG"
        reasons = []
        no_reasons = []
        if trend == "UPTREND":
            reasons.append("✅ Trend: UPTREND (EMA20 > EMA50)")
            if swept_low: reasons.append(f"✅ Liquidity swept below swing low {swing_low:.2f}")
            else: no_reasons.append("❌ No confirmed sweep of lows")
            if bos == "BULLISH BOS": reasons.append(f"✅ Bullish BOS above {swing_high:.2f}")
            else: no_reasons.append("❌ No bullish BOS")
            if bullish_fvg: reasons.append("✅ Bullish FVG detected")
            else: no_reasons.append("❌ No bullish FVG")
            if swept_low and bos == "BULLISH BOS" and bullish_fvg:
                return ("BUY", reasons, no_reasons, trend, liquidity, bos, fvg, last, swing_high, swing_low)
        elif trend == "DOWNTREND":
            reasons.append("✅ Trend: DOWNTREND (EMA20 < EMA50)")
            if swept_high: reasons.append(f"✅ Liquidity swept above swing high {swing_high:.2f}")
            else: no_reasons.append("❌ No confirmed sweep of highs")
            if bos == "BEARISH BOS": reasons.append(f"✅ Bearish BOS below {swing_low:.2f}")
            else: no_reasons.append("❌ No bearish BOS")
            if bearish_fvg: reasons.append("✅ Bearish FVG detected")
            else: no_reasons.append("❌ No bearish FVG")
            if swept_high and bos == "BEARISH BOS" and bearish_fvg:
                return ("SELL", reasons, no_reasons, trend, liquidity, bos, fvg, last, swing_high, swing_low)
        else:
            no_reasons.append("❌ Market is RANGING - EMA flat")
        return (None, reasons, no_reasons, trend, liquidity, bos, fvg, last, swing_high, swing_low)
    except Exception as e:
        print(f"ANALYZE ERR: {e}")
        return (None, [], ["❌ Analysis error"], "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", df.iloc[-1], 0, 0)

def format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data, df, swing_high, swing_low):
    headlines, bias, bias_text, high_impact, impact_reason = news_data
    price = float(last["Close"])
    dec = 2 if sym in ["XAUUSD","BTCUSD"] else 5
    if sym == "USDJPY":
        dec = 3
    msg = f"🤖 *AI SMC 2.0 + NEWS - {sym} {tf}*\n\nPrice: {price:.{dec}f}\n\n*📰 NEWS:*\n{bias}\n{bias_text}\n"
    if high_impact: msg += f"\n{impact_reason}\n"
    if headlines: msg += "\n" + "\n".join(headlines[:4]) + "\n\n"
    msg += f"*📊 TECHNICAL:*\nTrend: {trend} | Liq: {liq} | {bos} | {fvg}\nSwing High: {swing_high:.{dec}f} | Swing Low: {swing_low:.{dec}f}\n\n"
    if high_impact:
        msg += "🚫 *NO TRADE* - High impact news. Wait after news.\n"
        return msg
    if sig:
        sl, tp1, tp2, tp3, sl_reason, risk = calc_sltp(sym, sig, price, swing_low, swing_high)
        emoji = "🟢" if sig=="BUY" else "🔴"
        msg += f"{emoji} *SIGNAL: {sig}*\n*WHY SETUP:*\n" + "\n".join(reasons) + "\n\n"
        msg += f"*🎯 TRADE PLAN:*\nEntry: {price:.{dec}f}\nStop Loss: {sl:.{dec}f}\nTP1 (1:1.5 - Take 50%): {tp1:.{dec}f}\nTP2 (1:2.5 - Take 30%): {tp2:.{dec}f}\nTP3 (1:4 - Runner 20%): {tp3:.{dec}f}\n\n"
        msg += f"*Why this SL:* {sl_reason}\n"
        msg += f"*Why these TPs:* TP1 = first liquidity, TP2 = next OB, TP3 = major high/low\n"
        msg += f"Risk: {risk:.{dec}f} | Risk:Reward up to 1:4\n"
        msg += f"💡 *Tip:* Move SL to breakeven after TP1 hits."
        if sig=="BUY" and "BEARISH" in bias: msg += "\n\n⚠️ Chart BUY but News BEARISH - use small lot."
        if sig=="SELL" and "BULLISH" in bias: msg += "\n\n⚠️ Chart SELL but News BULLISH - use small lot."
    else:
        msg += "🚫 *NO SETUP*\n*WHY NO SETUP:*\n" + "\n".join(no_reasons)
        if reasons: msg += "\n\n*Good parts:*\n" + "\n".join(reasons)
        msg += "\n\nNo Entry / No SL / No TP - waiting is also a trade."
    return msg

def auto_scanner():
    while True:
        try:
            for sym in SYMBOLS:
                news_data = get_news_ai(sym)
                for tf in TFS:
                    df=get_data(SYMBOLS[sym], tf)
                    if df is None: continue
                    sig,reasons,no_reasons,trend,liq,bos,fvg,last,sh,slw = analyze(df)
                    if sig:
                        send(format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data, df, sh, slw))
                        time.sleep(1)
            time.sleep(900)
        except Exception as e:
            print(f"AUTO ERR {e}"); time.sleep(60)

def set_webhook():
    try:
        time.sleep(5)
        requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={RENDER_URL}/webhook", timeout=10)
    except: pass

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        upd = request.get_json(force=True)
        msg = upd.get("message", {})
        text = msg.get("text","").strip()
        cid = msg.get("chat",{}).get("id")
        if text.lower()=="/start":
            send("🤖 *SMC AI 2.0 + NEWS + SLTP LIVE!*\n\nTry `/signal XAUUSD`\nTry `/news XAUUSD`", cid)
        elif text.lower().startswith("/news"):
            sym = text.split()[-1].upper() if len(text.split())>1 else "XAUUSD"
            if sym in SYMBOLS:
                h,b,bt,hi,ir = get_news_ai(sym)
                send(f"📰 *NEWS {sym}*\n\n{b}\n{bt}\n\n" + "\n".join(h[:6]), cid)
        elif text.lower().startswith("/signal"):
            sym = text.split()[-1].upper() if len(text.split())>1 else ""
            if sym not in SYMBOLS: send("Use: /signal XAUUSD", cid)
            else:
                send(f"⏳ AI analyzing {sym}...", cid)
                news_data = get_news_ai(sym)
                for tf in TFS:
                    df=get_data(SYMBOLS[sym], tf)
                    if df is None: continue
                    sig,reasons,no_reasons,trend,liq,bos,fvg,last,sh,slw = analyze(df)
                    send(format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data, df, sh, slw), cid)
        return "ok"
    except Exception as e:
        print(e); return "ok"

Thread(target=set_webhook, daemon=True).start()
Thread(target=auto_scanner, daemon=True).start()
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))