"""Tiny stdlib stand-in for Ollama, used by the tests (SPEC 6.4).

Serves GET /api/tags and POST /api/chat in four modes:
  normal   - the model is listed; chat returns schema-shaped JSON with Ollama metrics
  slow     - chat sleeps past any client timeout (default 30 s) before answering
  bad_json - chat returns message.content that is not JSON
  stopped  - every connection is closed without a response (like a dead server)

Usage:  python scripts/ollama_stub.py --port 11435 --mode normal
In tests:  stub = OllamaStub(port=0); port = stub.start(); ...; stub.stop()
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODES = ("normal", "slow", "bad_json", "stopped")

# keyword -> (category, item) for the canned extraction
_KEYWORDS = [
    ("洗腎", "LIFE_SUSTAINING"), ("氧氣", "LIFE_SUSTAINING"), ("製氧機", "LIFE_SUSTAINING"),
    ("胰島素", "MEDICATION"), ("降血壓藥", "MEDICATION"), ("藥", "MEDICATION"),
    ("胸痛", "MEDICAL_EMERGENCY"), ("受困", "PERSON_AT_RISK"), ("沒出來", "PERSON_AT_RISK"),
    ("毛毯", "SUPPLIES"), ("奶粉", "INFANT_CHILD"), ("飲用水", "FOOD_WATER"), ("輪椅", "MOBILITY"),
]


def canned_needs(transcript):
    needs, seen = [], set()
    for word, cat in _KEYWORDS:
        if word in transcript and cat not in seen:
            seen.add(cat)
            needs.append({"category": cat, "item": word, "quantity": 0, "subject": "", "subject_group": "unknown",
                          "location": "", "time_phrase": "", "summary": word})
    if not needs:
        needs.append({"category": "OTHER", "item": "", "quantity": 0, "subject": "", "subject_group": "unknown",
                      "location": "", "time_phrase": "", "summary": transcript[:20]})
    return {"needs": needs[:5]}


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # clients that time out and hang up are expected in the 'slow' tests


class OllamaStub:
    def __init__(self, port=0, mode="normal", model="qwen2.5:1.5b", slow_s=30.0, response=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.port = int(port)
        self.mode = mode
        self.model = model
        self.slow_s = float(slow_s)
        self.response = response          # dict to return instead of the canned extraction
        self.omit_metrics = False         # Ollama omits zero-valued metrics; True drops them all
        self.chat_requests = []           # parsed request bodies
        self.tags_requests = 0
        self._prefixes = set()
        self._stop = threading.Event()
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:  # the client gave up (timeout) and closed the socket
                    self.close_connection = True

            def _dead(self):
                self.close_connection = True  # no response at all

            def do_GET(self):
                if stub.mode == "stopped":
                    return self._dead()
                if self.path.startswith("/api/tags"):
                    with stub._lock:
                        stub.tags_requests += 1
                    return self._send(200, {"models": [{"name": stub.model, "model": stub.model, "size": 986000000}]})
                if self.path.startswith("/api/version"):
                    return self._send(200, {"version": "stub"})
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                if stub.mode == "stopped":
                    return self._dead()
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if not self.path.startswith("/api/chat"):
                    return self._send(404, {"error": "not found"})
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    return self._send(400, {"error": "bad request"})
                with stub._lock:
                    stub.chat_requests.append(body)
                if body.get("model") != stub.model:
                    return self._send(404, {"error": f"model '{body.get('model')}' not found"})
                if stub.mode == "slow":
                    end = time.monotonic() + stub.slow_s
                    while time.monotonic() < end and not stub._stop.is_set():
                        time.sleep(0.05)
                msgs = body.get("messages") or []
                prefix = json.dumps(msgs[:-1], ensure_ascii=False, sort_keys=True)
                with stub._lock:
                    cached = prefix in stub._prefixes
                    stub._prefixes.add(prefix)
                user = (msgs[-1].get("content") if msgs else "") or ""
                transcript = user.split("：", 1)[-1]
                if stub.mode == "bad_json":
                    content = "{needs: [this is not json"
                else:
                    content = json.dumps(stub.response if stub.response is not None else canned_needs(transcript),
                                         ensure_ascii=False)
                reply = {"model": stub.model, "created_at": "2026-10-28T00:00:00Z",
                         "message": {"role": "assistant", "content": content}, "done": True, "done_reason": "stop"}
                if not stub.omit_metrics:
                    reply.update({"total_duration": 1_200_000_000, "load_duration": 1_000_000,
                                  "prompt_eval_count": 12 if cached else 412, "prompt_eval_duration": 50_000_000,
                                  "eval_count": 42, "eval_duration": 1_000_000_000})
                return self._send(200, reply)

        return Handler

    def start(self):
        self._stop.clear()
        self._httpd = _QuietServer(("127.0.0.1", self.port), self._handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="ollama-stub", daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        self._stop.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ollama stub server for tests")
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--mode", choices=MODES, default="normal")
    ap.add_argument("--model", default="qwen2.5:1.5b")
    ap.add_argument("--slow-s", type=float, default=30.0)
    a = ap.parse_args(argv)
    stub = OllamaStub(port=a.port, mode=a.mode, model=a.model, slow_s=a.slow_s)
    port = stub.start()
    print(f"ollama stub ({a.mode}) on http://127.0.0.1:{port} - Ctrl+C to stop", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stub.stop()


if __name__ == "__main__":
    main()
