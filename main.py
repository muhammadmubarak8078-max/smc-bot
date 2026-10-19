import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import time, os

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SMC BOT ONLINE 24/7 - LIVE")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

print("=== SMC BOT STARTED ===")
print("BOT ONLINE 24/7")
while True:
    print("SMC Scan running...")
    time.sleep(10)
