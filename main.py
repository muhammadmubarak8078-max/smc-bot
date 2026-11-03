import os, time, requests, yfinance as yf, pandas as pd, feedparser
from flask import Flask, request
from threading import Thread
from datetime import datetime, timedelta

TOKEN = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") or "https://smc-bot-i2jt.onrender.com"

SYMBOLS = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "NZDUSD": "NZDUSD=X", "USDJPY": "USDJPY=X"}
TFS = ["15m", "1h", "4h"]

# High Impact News Keywords - if these appear, bot pauses
HIGH_IMPACT = ["CPI", "NFP", "NONFARM", "FOMC", "FED", "INTEREST RATE", "INFLATION", "GDP", "POWELL", "ECB", "BOE", "UNEMPLOYMENT", "RETAIL SALES"]
BULLISH_WORDS = ["rise", "surge", "bullish", "rally", "gain", "strong", "hawkish", "rate hike", "demand"]
BEARISH_WORDS = ["fall", "drop", "bearish", "crash", "decline", "weak", "dovish", "rate cut", "recession"]

app = Flask(__name__)

@app.route('/')
def home():
    return f"SMC AI + NEWS LIVE - {datetime.now()}"

def send(msg, chat_id=None):
    try:
        cid = str(chat_id or CHAT_ID)
        if not TOKEN or not cid: return
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": cid, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e:
        print(f"SEND ERR {e}")

def get_data(ticker, tf):
    try:
        per = "2d" if tf=="15m" else "7d" if tf=="1h" else "30d"
        df = yf.download(ticker, interval=tf, period=per, progress=False, auto_adjust=True)
        if len(df)<50: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df.dropna()
    except:
        return None

# --- NEW: NEWS AI ---
def get_news_ai(sym):
    try:
        # Use Yahoo Finance News for that symbol
        ticker = yf.Ticker(SYMBOLS[sym])
        news = ticker.news or []
        headlines = []
        news_score = 0
        high_impact = False
        impact_reason = ""

        for n in news[:5]:
            title = n.get("title", "")
            headlines.append(f"• {title}")
            t = title.upper()
            for kw in HIGH_IMPACT:
                if kw in t:
                    high_impact = True
                    impact_reason = f"⚠️ HIGH IMPACT NEWS: '{title}' - {kw} news can cause big spike. Smart traders wait."
                    break
            tl = title.lower()
            for w in BULLISH_WORDS:
                if w in tl: news_score += 1
            for w in BEARISH_WORDS:
                if w in tl: news_score -= 1

        # Also check general Forex news RSS
        try:
            feed = feedparser.parse("https://www.forexlive.com/feed")
            for e in feed.entries[:3]:
                headlines.append(f"• [ForexLive] {e.title}")
                if any(k in e.title.upper() for k in HIGH_IMPACT):
                    high_impact = True
                    impact_reason = f"⚠️ HIGH IMPACT: {e.title}"
        except: pass

        if news_score > 1:
            bias = "🟢 NEWS BULLISH"
            bias_text = f"News is bullish (+{news_score}). Headlines show strength / demand."
        elif news_score < -1:
            bias = "🔴 NEWS BEARISH"
            bias_text = f"News is bearish ({news_score}). Headlines show weakness / fear."
        else:
            bias = "⚪ NEWS NEUTRAL"
            bias_text = "News is neutral / mixed. No strong fundamental direction."

        return headlines, bias, bias_text, high_impact, impact_reason
    except Exception as e:
        return [], "⚪ NEWS UNKNOWN", "Could not fetch news", False, ""

def analyze(df):
    close = df["Close"]
    last = df.iloc[-1]
    prev20 = df.iloc[:-1]
    hh = prev20["High"].max()
    ll = prev20["Low"].min()
    trend_up = close.tail(20).iloc[-1] > close.tail(20).iloc[0]
    trend_down = close.tail(20).iloc[-1] < close.tail(20).iloc[0]
    trend = "UPTREND" if trend_up else "DOWNTREND" if trend_down else "RANGING"
    swept_high = prev20["High"].max() < df["High"].iloc[-2] and last["Close"] < prev20["High"].max()
    swept_low = prev20["Low"].min() > df["Low"].iloc[-2] and last["Close"] > prev20["Low"].min()
    liquidity = "SWEPT HIGH" if swept_high else "SWEPT LOW" if swept_low else "NO SWEEP"
    bos_bull = last["Close"] > hh
    bos_bear = last["Close"] < ll
    bos = "BULLISH BOS" if bos_bull else "BEARISH BOS" if bos_bear else "NO BOS"
    fvg_bull = df["Low"].iloc[-1] > df["High"].iloc[-3]
    fvg_bear = df["High"].iloc[-1] < df["Low"].iloc[-3]
    fvg = "BULLISH FVG" if fvg_bull else "BEARISH FVG" if fvg_bear else "NO FVG"
    reasons, no_reasons = [], []

    if trend == "UPTREND":
        reasons.append("✅ Trend UPTREND")
        if liquidity == "SWEPT LOW": reasons.append("✅ Liquidity swept lows")
        else: no_reasons.append("❌ No sweep of lows yet")
        if bos == "BULLISH BOS": reasons.append(f"✅ BOS broke {hh:.2f}")
        else: no_reasons.append(f"❌ No BOS above {hh:.2f}")
        if fvg == "BULLISH FVG": reasons.append("✅ Bullish FVG")
        else: no_reasons.append("❌ No Bullish FVG")
        if len(reasons) >= 3: return "BUY", reasons, no_reasons, trend, liquidity, bos, fvg, last
        return None, reasons, no_reasons, trend, liquidity, bos, fvg, last
    elif trend == "DOWNTREND":
        reasons.append("✅ Trend DOWNTREND")
        if liquidity == "SWEPT HIGH": reasons.append("✅ Liquidity swept highs")
        else: no_reasons.append("❌ No sweep of highs yet")
        if bos == "BEARISH BOS": reasons.append(f"✅ BOS broke {ll:.2f}")
        else: no_reasons.append(f"❌ No BOS below {ll:.2f}")
        if fvg == "BEARISH FVG": reasons.append("✅ Bearish FVG")
        else: no_reasons.append("❌ No Bearish FVG")
        if len(reasons) >= 3: return "SELL", reasons, no_reasons, trend, liquidity, bos, fvg, last
        return None, reasons, no_reasons, trend, liquidity, bos, fvg, last
    else:
        no_reasons.append("❌ RANGING - no trend")
        return None, reasons, no_reasons, trend, liquidity, bos, fvg, last

def format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data):
    headlines, bias, bias_text, high_impact, impact_reason = news_data
    price = float(last["Close"])
    msg = f"🤖 *AI SMC + NEWS - {sym} {tf}*\n\n"
    msg += f"Price: {price:.2f}\n\n"
    # NEWS SECTION
    msg += f"*📰 NEWS ANALYSIS:*\n{bias}\n{bias_text}\n"
    if high_impact:
        msg += f"\n{impact_reason}\n"
    if headlines:
        msg += "\n*Latest Headlines:*\n" + "\n".join(headlines[:4]) + "\n\n"
    # TECHNICAL SECTION
    msg += f"*📊 TECHNICAL:*\nTrend: {trend} | Liq: {liq} | {bos} | {fvg}\n\n"
    if high_impact:
        msg += f"🚫 *FINAL DECISION: NO TRADE*\nReason: High impact news coming. Even if chart looks good, news can wipe SL. Wait after news.\n"
        return msg
    if sig:
        # Check News vs Tech Conflict
        conflict = ""
        if sig == "BUY" and "BEARISH" in bias: conflict = "\n⚠️ WARNING: Chart says BUY but News is BEARISH. Take with small lot / wait for confirmation."
        if sig == "SELL" and "BULLISH" in bias: conflict = "\n⚠️ WARNING: Chart says SELL but News is BULLISH. Take with small lot / wait for confirmation."
        msg += f"🔥 *SIGNAL: {sig}* ✅\n*WHY SETUP:*\n" + "\n".join(reasons) + f"\n{conflict}\n"
        msg += f"\nEntry: {price:.2f}\nTP: Next liquidity | SL: Below/Above sweep"
    else:
        msg += f"🚫 *NO SETUP*\n*WHY NO SETUP:*\n" + "\n".join(no_reasons) + "\n"
        if reasons: msg += f"\n*Good parts:*\n" + "\n".join(reasons) + "\n"
        msg += f"\nWaiting for all rules + news to align."
    return msg

def auto_scanner():
    while True:
        try:
            for sym in SYMBOLS:
                news_data = get_news_ai(sym)
                for tf in TFS:
                    df=get_data(SYMBOLS[sym], tf)
                    if df is None: continue
                    sig,reasons,no_reasons,trend,liq,bos,fvg,last = analyze(df)
                    if sig:  # Only auto-send signals
                        send(format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data))
                        time.sleep(1)
            time.sleep(900)
        except Exception as e:
            print(e); time.sleep(60)

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
            send("🤖 *SMC AI + NEWS LIVE!*\n\nI explain Technical + News.\nTry `/signal XAUUSD`\nTry `/news XAUUSD` for news only", cid)
        elif text.lower().startswith("/news"):
            sym = text.split()[-1].upper() if len(text.split())>1 else "XAUUSD"
            if sym in SYMBOLS:
                headlines,bias,bias_text,hi,ir = get_news_ai(sym)
                send(f"📰 *NEWS {sym}*\n\n{bias}\n{bias_text}\n\n" + "\n".join(headlines[:6]) + f"\n\n{ir}", cid)
        elif text.lower().startswith("/signal"):
            sym = text.split()[-1].upper() if len(text.split())>1 else ""
            if sym not in SYMBOLS: send("Use: /signal XAUUSD", cid)
            else:
                send(f"⏳ AI + News analyzing {sym}...", cid)
                news_data = get_news_ai(sym)
                for tf in TFS:
                    df=get_data(SYMBOLS[sym], tf)
                    if df is None: continue
                    sig,reasons,no_reasons,trend,liq,bos,fvg,last = analyze(df)
                    send(format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data), cid)
        return "ok"
    except Exception as e:
        print(e); return "ok"

Thread(target=set_webhook, daemon=True).start()
Thread(target=auto_scanner, daemon=True).start()
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))