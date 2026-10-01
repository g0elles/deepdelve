"""Transparent logging proxy for Ollama: forwards everything to 11434 and appends every request body and every streamed response line to a JSONL file.
Used to capture the REAL requests (and real aborted streams) an evidence-path run sends, without touching project code."""
import http.server, socketserver, json, sys, threading, time, urllib.request, urllib.error
UP, LOG = "http://127.0.0.1:11434", sys.argv[2] if len(sys.argv) > 2 else "eval/thinking_effort/proxy_capture.jsonl"
lock = threading.Lock(); counter = [0]
def log(rec):
    with lock, open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def _go(self):
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else b""
        with lock: counter[0] += 1; rid = counter[0]
        log({"id": rid, "t": time.time(), "kind": "request", "method": self.command, "path": self.path, "body": body.decode("utf-8", "replace")})
        req = urllib.request.Request(UP + self.path, data=body or None, method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")})
        try: resp = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e: resp = e
        self.send_response(resp.status if hasattr(resp, "status") else resp.code)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"): self.send_header(k, v)
        self.end_headers()
        buf = b""
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk: break
            self.wfile.write(chunk); self.wfile.flush(); buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip(): log({"id": rid, "t": time.time(), "kind": "line", "line": line.decode("utf-8", "replace")})
        if buf.strip(): log({"id": rid, "t": time.time(), "kind": "line", "line": buf.decode("utf-8", "replace")})
        log({"id": rid, "t": time.time(), "kind": "end"})
    do_GET = do_POST = do_DELETE = _go
class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True
S(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 11500), H).serve_forever()
