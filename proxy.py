"""Reverse proxy :5173 → :8080. Uses requests for reliable POST/GET/MJPEG."""
import http.server, requests, sys, threading
from urllib.parse import urljoin

TARGET = "http://localhost:8080"
PORT = 5173

class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'  # MJPEG needs HTTP/1.1
    def do_GET(self):    self._proxy("GET")
    def do_POST(self):   self._proxy("POST")

    def _proxy(self, method):
        url = TARGET + self.path
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

        try:
            headers = {k: v for k, v in self.headers.items()
                      if k.lower() not in ("host", "connection")}

            if self.path.startswith("/video_feed"):
                # Stream MJPEG — preserve original Content-Type
                resp = requests.get(url, stream=True, timeout=30, headers=headers)
                self.send_response(resp.status_code)
                ct = resp.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Content-Type", ct)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for chunk in resp.iter_content(4096):
                    if chunk:
                        try: self.wfile.write(chunk)
                        except: break
                return

            if method == "POST":
                resp = requests.post(url, data=body, headers=headers, timeout=30)
            else:
                resp = requests.get(url, headers=headers, timeout=30)

            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in ("transfer-encoding", "content-encoding"):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.content)
        except Exception as e:
            try: self.send_error(502, str(e))
            except: pass

    def log_message(self, f, *a): pass

class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    print(f"Proxy: http://0.0.0.0:{PORT} → {TARGET}")
    ThreadedHTTPServer(("0.0.0.0", PORT), Proxy).serve_forever()
