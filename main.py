import os, time, requests, yfinance as yf, pandas as pd
from flask import Flask, request
from threading import Thread
from datetime import datetime
try:
    import feedparser
except:
    feedparser = None

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
        if df is None or len(df)<50: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df.dropna()
    except Exception as e:
        print(f"DATA ERR {e}")
        return None

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
                    title = n.get("title", "") or n.get("providerPublishTime", "")
                    # new yfinance format
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

def analyze(df):
    try:
        close = df["Close"]
        last = df.iloc[-1]
        prev20 = df.iloc[:-1]
        hh = float(prev20["High"].max())
        ll = float(prev20["Low"].min())
        trend = "UPTREND" if float(close.tail(20).iloc[-1]) > float(close.tail(20).iloc[0]) else "DOWNTREND" if float(close.tail(20).iloc[-1]) < float(close.tail(20).iloc[0]) else "RANGING"
        swept_low = float(prev20["Low"].min()) > float(df["Low"].iloc[-2]) and float(last["Close"]) > float(prev20["Low"].min())
        swept_high = float(prev20["High"].max()) < float(df["High"].iloc[-2]) and float(last["Close"]) < float(prev20["High"].max())
        liquidity = "SWEPT LOW" if swept_low else "SWEPT HIGH" if swept_high else "NO SWEEP"
        bos = "BULLISH BOS" if float(last["Close"]) > hh else "BEARISH BOS" if float(last["Close"]) < ll else "NO BOS"
        fvg = "BULLISH FVG" if float(df["Low"].iloc[-1]) > float(df["High"].iloc[-3]) else "BEARISH FVG" if float(df["High"].iloc[-1]) < float(df["Low"].iloc[-3]) else "NO FVG"
        reasons, no_reasons = [], []
        if trend == "UPTREND":
            reasons.append("✅ Trend UPTREND")
            if liquidity=="SWEPT LOW": reasons.append("✅ Liquidity swept lows")
            else: no_reasons.append("❌ No sweep of lows yet")
            if bos=="BULLISH BOS": reasons.append(f"✅ BOS broke {hh:.2f}")
            else: no_reasons.append(f"❌ No BOS above {hh:.2f}")
            if fvg=="BULLISH FVG": reasons.append("✅ Bullish FVG")
            else: no_reasons.append("❌ No Bullish FVG")
            if len(reasons)>=3: return "BUY", reasons, no_reasons, trend, liquidity, bos, fvg, last
            return None, reasons, no_reasons, trend, liquidity, bos, fvg, last
        elif trend == "DOWNTREND":
            reasons.append("✅ Trend DOWNTREND")
            if liquidity=="SWEPT HIGH": reasons.append("✅ Liquidity swept highs")
            else: no_reasons.append("❌ No sweep of highs yet")
            if bos=="BEARISH BOS": reasons.append(f"✅ BOS broke {ll:.2f}")
            else: no_reasons.append(f"❌ No BOS below {ll:.2f}")
            if fvg=="BEARISH FVG": reasons.append("✅ Bearish FVG")
            else: no_reasons.append("❌ No Bearish FVG")
            if len(reasons)>=3: return "SELL", reasons, no_reasons, trend, liquidity, bos, fvg, last
            return None, reasons, no_reasons, trend, liquidity, bos, fvg, last
        else:
            no_reasons.append("❌ RANGING - no trend")
            return None, reasons, no_reasons, trend, liquidity, bos, fvg, last
    except Exception as e:
        print(f"ANALYZE ERR {e}")
        return None, [], ["❌ Analysis error"], "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", df.iloc[-1]

def format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data):
    headlines, bias, bias_text, high_impact, impact_reason = news_data
    price = float(last["Close"])
    msg = f"🤖 *AI SMC + NEWS - {sym} {tf}*\n\nPrice: {price:.2f}\n\n*📰 NEWS:*\n{bias}\n{bias_text}\n"
    if high_impact: msg += f"\n{impact_reason}\n"
    if headlines: msg += "\n" + "\n".join(headlines[:4]) + "\n\n"
    msg += f"*📊 TECHNICAL:*\nTrend: {trend} | Liq: {liq} | {bos} | {fvg}\n\n"
    if high_impact:
        msg += "🚫 *NO TRADE* - High impact news. Wait after news.\n"
        return msg
    if sig:
        msg += f"🔥 *SIGNAL: {sig}*\n*WHY SETUP:*\n" + "\n".join(reasons) + f"\n\nEntry: {price:.2f}"
    else:
        msg += "🚫 *NO SETUP*\n*WHY NO SETUP:*\n" + "\n".join(no_reasons)
        if reasons: msg += "\n\n*Good parts:*\n" + "\n".join(reasons)
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
                    if sig:
                        send(format_full(sym, tf, sig, reasons, no_reasons, trend, liq, bos, fvg, last, news_data))
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
            send("🤖 *SMC AI + NEWS LIVE!*\n\nTry `/signal XAUUSD`\nTry `/news XAUUSD`", cid)
        elif text.lower().startswith("/news"):
            sym = text.split()[-1].upper() if len(text.split())>1 else "XAUUSD"
            if sym in SYMBOLS:
                h,b,bt,hi,ir = get_news_ai(sym)
                send(f"📰 *NEWS {sym}*\n\n{b}\n{bt}\n\n" + "\n".join(h[:6]), cid)
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