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
    return f"SMC PRO WEBHOOK LIVE - TOKEN OK:{bool(TOKEN)} CHAT OK:{bool(CHAT_ID)} URL:{RENDER_URL}"

def send(msg, chat_id=None):
    try:
        cid = chat_id or CHAT_ID
        if not TOKEN or not cid:
            print(f"SEND FAIL TOKEN={bool(TOKEN)} CID={cid}")
            return
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": cid, "text": msg, "parse_mode":"Markdown"}, timeout=10)
        print(f"SEND RESP: {r.text[:300]}")
    except Exception as e:
        print(f"SEND ERR {e}")

def get_data(ticker, tf):
    try:
        per = "2d" if tf=="15m" else "7d" if tf=="1h" else "30d"
        df = yf.download(ticker, interval=tf, period=per, progress=False, auto_adjust=True)
        if len(df)<50: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df.dropna()
    except Exception as e:
        print(f"DATA ERR {e}")
        return None

def scan_symbol(df):
    last=df.iloc[-1]; prev=df.iloc[-2]; third=df.iloc[-3]
    rh=df["High"].tail(20).max(); rl=df["Low"].tail(20).min()
    bull_bos=last["Close"]>rh; bear_bos=last["Close"]<rl
    ob_bull=last["Close"]>last["Open"] and prev["Close"]<prev["Open"]
    ob_bear=last["Close"]<last["Open"] and prev["Close"]>prev["Open"]
    bull_fvg=last["Low"]>third["High"]; bear_fvg=last["High"]<third["Low"]
    sweep_h=last["High"]>rh and last["Close"]<rh; sweep_l=last["Low"]<rl and last["Close"]>rl
    conf=0; reasons=[]
    if bull_bos: conf+=40; reasons.append("BOS Bull")
    if bear_bos: conf+=40; reasons.append("BOS Bear")
    if ob_bull: conf+=20; reasons.append("Bull OB")
    if ob_bear: conf+=20; reasons.append("Bear OB")
    if bull_fvg: conf+=20; reasons.append("Bull FVG")
    if bear_fvg: conf+=20; reasons.append("Bear FVG")
    if sweep_l: conf+=15; reasons.append("Sweep Low")
    if sweep_h: conf+=15; reasons.append("Sweep High")
    sig=None
    if bull_bos and (ob_bull or bull_fvg or sweep_l) and conf>=60: sig="BUY"
    if bear_bos and (ob_bear or bear_fvg or sweep_h) and conf>=60: sig="SELL"
    return sig,conf,reasons,float(last["Close"])

def scanner():
    time.sleep(10)
    if CHAT_ID: send("🚀 *SMC PRO STARTED - WEBHOOK MODE*\nPairs: 6 - TF: 15m/1h/4h")
    while True:
        for name,ticker in SYMBOLS.items():
            for tf in TFS:
                df=get_data(ticker,tf)
                if df is None: continue
                sig,conf,reas,price=scan_symbol(df)
                if sig:
                    send(f"🔥 *{name} {sig}* 🔥\nTF:{tf}\nPrice:{price:.2f}\nConf:{conf}%\nSetup:{'+'.join(reas)}")
                    time.sleep(5)
        time.sleep(900)

# WEBHOOK HANDLER - THIS FIXES YOUR RECV PROBLEM
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        upd = request.get_json(force=True)
        print(f"WEBHOOK RECV: {upd}")
        msg = upd.get("message", {})
        text = msg.get("text","").strip()
        cid = msg.get("chat",{}).get("id")
        print(f"RECV: {text} from {cid}")
        if not text or not cid: return "ok"
        if text.lower()=="/start":
            send("🚀 *SMC PRO 6 PAIRS LIVE!*\n\n✅ XAUUSD\n✅ BTCUSD\n✅ EURUSD\n✅ GBPUSD\n✅ NZDUSD\n✅ USDJPY\n\nTF: 15m,1h,4h\nUse: /signal EURUSD", cid)
        elif text.lower().startswith("/signal"):
            parts=text.split()
            if len(parts)<2:
                send("Use: /signal EURUSD", cid)
            else:
                sym=parts[1].upper()
                if sym not in SYMBOLS:
                    send(f"❌ {sym} not found. Use: {', '.join(SYMBOLS.keys())}", cid)
                else:
                    send(f"⏳ Scanning {sym}...", cid)
                    for tf in TFS:
                        df=get_data(SYMBOLS[sym], tf)
                        if df is None: continue
                        sig,conf,reas,price=scan_symbol(df)
                        if sig:
                            send(f"🔥 *{sym} {sig}* 🔥\nTF:{tf}\nPrice:{price:.2f}\nConf:{conf}%", cid)
                        else:
                            send(f"ℹ️ {sym} {tf}: No setup yet", cid)
        return "ok"
    except Exception as e:
        print(f"WEBHOOK ERR {e}")
        return "ok"

Thread(target=scanner, daemon=True).start()

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))