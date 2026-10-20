"""Local server that behaves like the Vercel deployment, for development and the E2E tests.

    python3 scripts/dev_server.py            # http://127.0.0.1:8000, emails are faked
    python3 scripts/dev_server.py --port 8787
    python3 scripts/dev_server.py --real-smtp  # really sends, using SMTP_USER / SMTP_PASS

It serves the site with the headers from vercel.json (so the Content-Security-Policy is
enforced like in production), 404.html for missing pages, and runs api/request.py and
api/health.py. With fake email, "sent" emails are kept in memory:
    GET  /__dev/outbox   the emails so far, as JSON
    POST /__dev/reset    forget them and the API's rate-limit / duplicate memory
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "api", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


request_api = load("request")
health_api = load("health")
OUTBOX = []

with open(os.path.join(ROOT, "vercel.json"), encoding="utf-8") as f:
    HEADER_RULES = [(re.compile("^" + rule["source"] + "$"), rule["headers"]) for rule in json.load(f)["headers"]]


def fake_send(msg, user, password):
    OUTBOX.append({
        "subject": msg["Subject"],
        "to": msg["To"],
        "text": msg.get_body(("plain",)).get_content(),
        "html": msg.get_body(("html",)).get_content(),
    })


class DevHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        path = self.path.split("?")[0]
        for pattern, headers in HEADER_RULES:
            if pattern.match(path):
                for h in headers:
                    self.send_header(h["key"], h["value"])
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/__dev/outbox":
            return self.json(200, OUTBOX)
        if path == "/api/health":
            return self.bridge(HealthBridge, "do_GET")
        if path.startswith("/api/"):
            return self.bridge(RequestBridge, "do_GET")
        if path == "/_vercel/insights/script.js":  # Vercel Analytics only exists when deployed
            return self.raw(200, b"", "text/javascript")
        if path.startswith("/.") or path.startswith(("/scripts/", "/tests/", "/node_modules/")):
            return self.send_error(404)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/__dev/reset":
            OUTBOX.clear()
            for memory in ("_hits", "_sent_keys", "_sent_times"):  # api/request.py's limits
                if hasattr(request_api, memory):
                    getattr(request_api, memory).clear()
            return self.json(200, {"ok": True})
        if path == "/api/request":
            return self.bridge(RequestBridge, "do_POST")
        self.send_error(405)

    def bridge(self, cls, method):
        """Answer this request with an API handler's code (plus this server's headers)."""
        self.__class__ = cls  # one request per connection (HTTP/1.0), so this lasts just this request
        getattr(cls, method)(self)

    def json(self, status, data):
        self.raw(status, json.dumps(data).encode(), "application/json")

    def raw(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        page = os.path.join(ROOT, "404.html")
        if code == 404 and os.path.exists(page):
            with open(page, "rb") as f:
                return self.raw(404, f.read(), "text/html; charset=utf-8")
        super().send_error(code, message, explain)

    def log_message(self, format, *args):
        if os.environ.get("DEV_SERVER_QUIET") != "1":
            super().log_message(format, *args)


# The API handler's methods first, then DevHandler's (vercel.json headers, logging switch)
RequestBridge = type("RequestBridge", (request_api.handler, DevHandler), {})
HealthBridge = type("HealthBridge", (health_api.handler, DevHandler), {})


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--real-smtp", action="store_true", help="really send email (needs SMTP_USER / SMTP_PASS)")
    args = parser.parse_args()
    if not args.real_smtp:
        request_api.send = fake_send
        os.environ.setdefault("SMTP_USER", "dev@example.com")
        os.environ.setdefault("SMTP_PASS", "dev")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DevHandler)
    print(f"Serving on http://127.0.0.1:{args.port} ({'real' if args.real_smtp else 'fake'} email)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
