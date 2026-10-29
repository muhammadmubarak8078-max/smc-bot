import os, time, requests, yfinance as yf, pandas as pd
from flask import Flask, request
from threading import Thread
from datetime import datetime

TOKEN = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL") or "https://smc-bot-i2jt.onrender.com"

SYMBOLS = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "NZDUSD": "NZDUSD=X", "USDJPY": "USDJPY=X"}
TFS = ["15m", "1h", "4h"]

app = Flask(__name__)

@app.route('/')
def home():
    return f"SMC AUTO+MANUAL LIVE - TOKEN:{bool(TOKEN)} CHAT:{bool(CHAT_ID)} {datetime.now()}"

def send(msg, chat_id=None):
    try:
        cid = str(chat_id or CHAT_ID)
        if not TOKEN or not cid: return
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": cid, "text": msg, "parse_mode":"Markdown"}, timeout=15)
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

def scan_symbol(df):
    last=df.iloc[-1]; rh=df["High"].tail(20).max(); rl=df["Low"].tail(20).min()
    bull_bos=last["Close"]>rh; bear_bos=last["Close"]<rl
    if bull_bos: return "BUY",80
    if bear_bos: return "SELL",80
    return None,0

# --- AUTO SCANNER (runs every 15 mins by itself) ---
def auto_scanner():
    print("AUTO SCANNER STARTED")
    while True:
        try:
            print(f"AUTO SCANNING ALL PAIRS {datetime.now()}")
            for sym, ticker in SYMBOLS.items():
                for tf in TFS:
                    df=get_data(ticker, tf)
                    if df is None: continue
                    sig,conf=scan_symbol(df)
                    if sig:
                        msg=f"🚨 *AUTO ALERT!* 🚨\n\n🔥 *{sym} {sig}* on {tf}\nConfidence: {conf}%\nTime: {datetime.now().strftime('%H:%M')}"
                        send(msg)
                        time.sleep(1)
            print("AUTO SCAN DONE, sleeping 15 mins...")
            time.sleep(900) # 15 mins
        except Exception as e:
            print(f"AUTO ERR {e}")
            time.sleep(60)

def set_webhook():
    try:
        time.sleep(5)
        url = f"{RENDER_URL}/webhook"
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={url}", timeout=10)
        print(f"WEBHOOK SET: {r.text}")
    except Exception as e:
        print(f"WEBHOOK ERR {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        upd = request.get_json(force=True)
        msg = upd.get("message