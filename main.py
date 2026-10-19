import threading, time, os, requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import yfinance as yf
import pandas as pd

# === CONFIG ===
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7999441123:AAFZwyNSg42we8dMYr9HLi24oVcd--q7Fgc")
CHAT_ID = os.environ.get("CHAT_ID", "")
SYMBOL = "GC=F"  # XAUUSD Gold - change to EURUSD=X or BTC-USD if you want
TIMEFRAME = "15m"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SMC GOLD BOT ONLINE 24/7 - LIVE")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

def send_telegram(msg):
    try:
        if not CHAT_ID: return
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print(e)

def check_smc():
    try:
        df = yf.download(SYMBOL, period="5d", interval=TIMEFRAME)
        if len(df) < 200: return
        df['EMA50'] = df['Close'].ewm(span=50).mean()
        df['EMA200'] = df['Close'].ewm(span=200).mean()
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # SMC Logic: EMA Trend + Sweep + CHoCH
        bullish_trend = last['EMA50'] > last['EMA200']
        bullish_choch = last['Close'] > prev['Close'] and last['Close'] > last['EMA50']
        
        # Liquidity sweep: low wick below recent low then close above
        recent_low = df['Low'].iloc[-20:-1].min()
        sweep = last['Low'] < recent_low and last['Close'] > recent_low
        
        print(f"Scan {SYMBOL}: Close={float(last['Close']):.2f} EMA50={float(last['EMA50']):.2f} TrendBull={bullish_trend} Sweep={sweep}")
        
        if bullish_trend and bullish_choch and sweep:
            msg = f"🚀 SMC BUY SIGNAL\nSymbol: {SYMBOL}\nPrice: {float(last['Close']):.2f}\nTimeframe: {TIMEFRAME}\nSetup: Sweep + CHoCH + EMA Bullish\nhttps://smc-bot-i2jt.onrender.com"
            send_telegram(msg)
            print("ALERT SENT!")
    except Exception as e:
        print(f"Error: {e}")

print("=== REAL SMC BOT STARTED ===")
send_telegram("✅ SMC Bot is now LIVE 24/7 on Render! Scanning Gold (XAUUSD) every 2 mins.")

while True:
    check_smc()
    time.sleep(120)  # scan every 2 minutes
