import os, time, requests, yfinance as yf, pandas as pd
from flask import Flask
from threading import Thread
from datetime import datetime

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOLS = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD"}
TFS = ["15m", "1h", "4h"]

app = Flask(__name__)

@app.route('/')
def home():
    return "SMC PRO LIVE - XAUUSD + BTC"

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except:
        pass

def get_data(ticker, tf):
    try:
        per = "2d" if tf == "15m" else "7d" if tf == "1h" else "30d"
        df = yf.download(ticker, interval=tf, period=per, progress=False)
        if len(df) < 50:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.dropna()
    except:
        return None

def scan_symbol(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    third = df.iloc[-3]
    rh = df["High"].tail(20).max()
    rl = df["Low"].tail(20).min()

    bull_bos = last["Close"] > rh
    bear_bos = last["Close"] < rl

    ob_bull = last["Close"] > last["Open"] and prev["Close"] < prev["Open"]
    ob_bear = last["Close"] < last["Open"] and prev["Close"] > prev["Open"]

    bull_fvg = last["Low"] > third["High"]
    bear_fvg = last["High"] < third["Low"]

    sweep_h = last["High"] > rh and last["Close"] < rh
    sweep_l = last["Low"] < rl and last["Close"] > rl

    conf = 0
    reasons = []
    if bull_bos:
        conf += 40
        reasons.append("BOS Bull")
    if bear_bos:
        conf += 40
        reasons.append("BOS Bear")
    if ob_bull:
        conf += 20
        reasons.append("Bull OB")
    if ob_bear:
        conf += 20
        reasons.append("Bear OB")
    if bull_fvg:
        conf += 20
        reasons.append("Bull FVG")
    if bear_fvg:
        conf += 20
        reasons.append("Bear FVG")
    if sweep_l:
        conf += 15
        reasons.append("Sweep Low")
    if sweep_h:
        conf += 15
        reasons.append("Sweep High")

    sig = None
    if bull_bos and (ob_bull or bull_fvg or sweep_l) and conf >= 60:
        sig = "BUY"
    if bear_bos and (ob_bear or bear_fvg or sweep_h) and conf >= 60:
        sig = "SELL"

    return sig, conf, reasons, float(last["Close"])

def scanner():
    send("🚀 *SMC PRO STARTED*\nXAUUSD + BTCUSD\nTF: 15m, 1h, 4h\nBOS + OB + FVG + Liquidity")
    while True:
        for name, ticker in SYMBOLS.items():
            for tf in TFS:
                df = get_data(ticker, tf)
                if df is None:
                    continue
                sig, conf, reas, price = scan_symbol(df)
                if sig:
                    r = " + ".join(reas)
                    msg = f"🔥 *{name} {sig}* 🔥\nTF: {tf}\nPrice: {price:.2f}\nConf: {conf}%\nSetup: {r}\nTime: {datetime.now().strftime('%H:%M UTC')}"
                    send(msg)
                    time.sleep(5)
        time.sleep(900)

Thread(target=scanner, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
