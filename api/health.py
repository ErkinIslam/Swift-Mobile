"""Checks that the website can still send email, for an uptime monitor to poll.

GET /api/health?token=<HEALTH_TOKEN> (or header "Authorization: Bearer <HEALTH_TOKEN>")
logs in to the SMTP server with the same settings as api/request.py, sends nothing, and
answers 200 {"ok": true} or 503 {"ok": false, "error": "<kind of failure>"}.

Without HEALTH_TOKEN set, or with a wrong token, it answers 404, so the public can't make
it log in over and over. Poll it about once an hour (see README.md); Google may lock an
account that logs in very often.
"""

import hmac
import json
import os
import smtplib
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

SMTP_HOST = "smtp.gmail.com"  # same as api/request.py
SMTP_PORT = 465


def check_smtp(user, password):
    """None if logging in works, otherwise the kind of failure."""
    if not user or not password:
        return "SMTP_USER or SMTP_PASS is not set"
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.login(user, password)
    except (smtplib.SMTPException, OSError) as err:
        return type(err).__name__
    return None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        expected = os.environ.get("HEALTH_TOKEN", "")
        given = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            given = auth[len("Bearer "):]
        if len(expected) < 16 or not hmac.compare_digest(given.encode(), expected.encode()):
            return self.reply(404, {"ok": False})

        try:
            error = check_smtp(os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", ""))
        except Exception as err:
            error = type(err).__name__
        if error:
            print(json.dumps({"event": "health_failed", "error": error}), file=sys.stderr, flush=True)
            return self.reply(503, {"ok": False, "error": error})
        self.reply(200, {"ok": True})

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # the request line holds the token
