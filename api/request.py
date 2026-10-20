"""Emails booking and fleet quote requests from the website to the business inbox.

Runs on Vercel as a Python function at /api/request. The forms post JSON:
{"form": "booking" | "fleet", "name", "phone", "company", "lines", "website",
 "key", "channel"}
and this sends it as an email through the Google Workspace account below.
"key" identifies one request so a retry isn't emailed twice; "channel": "sms" marks a
copy of a request the customer was asked to text us (see script.js): requests arrive as
texts from the customer's own number, and this email is the backup.

Settings (Vercel > Project > Settings > Environment Variables):
  SMTP_USER  the Google Workspace address that sends the email
  SMTP_PASS  an app password for that address (not its normal password)
  NOTIFY_TO  optional: where requests go (defaults to DEFAULT_NOTIFY_TO below)

Logs are one JSON object per line ({"event": ...}) with no customer details, so a log
drain or alert can match on "email_failed" (see README.md).
"""

import html
import json
import os
import re
import smtplib
import sys
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formataddr
from http.server import BaseHTTPRequestHandler

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
DEFAULT_NOTIFY_TO = "oilchange@swiftmobileautocare.com"
MAX_BYTES = 20_000  # a full request is about 1 KB
MAX_LINES = 30
MAX_LINE = 2_000

# Abuse limits. They live in this instance's memory, so they reset when Vercel starts a
# new instance: they slow a flood down but aren't a hard guarantee (README.md: firewall rule).
PER_IP_LIMIT = 8            # requests per IP...
PER_IP_WINDOW = 10 * 60     # ...per 10 minutes
GLOBAL_LIMIT = 60           # emails per instance per hour, whoever sends them
GLOBAL_WINDOW = 60 * 60
KEY_TTL = 60 * 60           # how long a sent request's key is remembered

FORMS = {
    "booking": "Booking request",
    "fleet": "Fleet quote request",
}

_lock = threading.Lock()
_hits = {}                  # ip -> deque of request times
_sent_times = deque()       # times of emails sent
_sent_keys = {}             # key -> time sent


def log(event, **fields):
    """One JSON line on stderr. Never pass customer details here."""
    print(json.dumps({"event": event, **fields}), file=sys.stderr, flush=True)


def one_line(value, limit):
    """Text with no line breaks or control characters, at most `limit` long."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:limit]


def phone_digits(phone):
    """Same rule as the form: 10 digits once a leading 1 is dropped."""
    return re.sub(r"^1", "", re.sub(r"\D", "", phone))


def parse(data):
    """The request's details, or None if it isn't a valid request."""
    if not isinstance(data, dict) or data.get("form") not in FORMS:
        return None
    kind = data["form"]
    name = one_line(data.get("name"), 100)
    phone = one_line(data.get("phone"), 40)
    company = one_line(data.get("company"), 100)
    lines = data.get("lines")
    if len(name) < 2 or len(phone_digits(phone)) != 10:
        return None
    if kind == "fleet" and len(company) < 2:
        return None
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        return None
    if not all(isinstance(line, str) and line.strip() for line in lines):
        return None
    # Each line is "Label: value". Only notes can span lines, so keep \n and drop other control characters
    lines = [re.sub(r"[\x00-\x09\x0b-\x1f\x7f]+", " ", line).strip()[:MAX_LINE] for line in lines]
    lines = [line for line in lines if line]
    if not lines:
        return None
    key = data.get("key")
    if not (isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9-]{8,64}", key)):
        key = None
    return {"kind": kind, "name": name, "phone": phone, "company": company, "lines": lines,
            "key": key, "texting": data.get("channel") == "sms"}


def build_email(req, sender, to):
    title = FORMS[req["kind"]]
    who = f'{req["company"]} ({req["name"]})' if req["company"] else req["name"]
    tel = "+1" + phone_digits(req["phone"])
    first = html.escape(req["name"].split()[0][:20])
    texting_note = ("They were asked to text this to you from their phone. "
                    "If no text arrives, follow up with them.") if req["texting"] else ""

    msg = EmailMessage()
    msg["Subject"] = f"{title}{' (texting you)' if req['texting'] else ''}: {who}"
    msg["From"] = formataddr(("Swift Mobile website", sender))
    msg["To"] = to
    msg.set_content("\n".join([title, ""] + ([texting_note, ""] if texting_note else []) + req["lines"]) + "\n")

    rows = []
    for line in req["lines"]:
        label, _, value = line.partition(": ")
        if not value:
            label, value = "", label
        rows.append(
            '<tr><th style="text-align:left;vertical-align:top;padding:6px 16px 6px 0;color:#525252;font-weight:normal;white-space:nowrap">'
            + html.escape(label)
            + '</th><td style="padding:6px 0">'
            + html.escape(value).replace("\n", "<br>")
            + "</td></tr>"
        )
    button = "display:inline-block;margin:0 8px 8px 0;padding:12px 20px;border-radius:8px;background:#0A0A0A;color:#FFFFFF;font-weight:bold;text-decoration:none"
    note_html = f'<p style="margin:0 0 12px;color:#525252">{html.escape(texting_note)}</p>' if texting_note else ""
    msg.add_alternative(
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.4;color:#0A0A0A;max-width:560px">'
        f'<p style="margin:0 0 12px;font-size:20px;font-weight:bold">{title}</p>'
        f"{note_html}"
        f'<table style="border-collapse:collapse">{"".join(rows)}</table>'
        f'<p style="margin:20px 0 0"><a href="tel:{tel}" style="{button}">Call {first}</a>'
        f'<a href="sms:{tel}" style="{button}">Text {first}</a></p>'
        '<p style="margin:16px 0 0;font-size:13px;color:#525252">Sent from the form on swiftmobileautocare.com.</p>'
        "</div>",
        subtype="html",
    )
    return msg


def send(msg, user, password):
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def allow(ip, now=None):
    """False if this IP, or this instance as a whole, has sent too much lately."""
    now = time.monotonic() if now is None else now
    with _lock:
        while _sent_times and now - _sent_times[0] > GLOBAL_WINDOW:
            _sent_times.popleft()
        if len(_sent_times) >= GLOBAL_LIMIT:
            return False
        hits = _hits.setdefault(ip, deque())
        while hits and now - hits[0] > PER_IP_WINDOW:
            hits.popleft()
        if len(hits) >= PER_IP_LIMIT:
            return False
        hits.append(now)
        if len(_hits) > 10_000:  # forget idle IPs so memory stays small
            for stale in [k for k, v in _hits.items() if not v or now - v[-1] > PER_IP_WINDOW]:
                del _hits[stale]
        return True


def already_sent(key, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        for stale in [k for k, t in _sent_keys.items() if now - t > KEY_TTL]:
            del _sent_keys[stale]
        return key is not None and key in _sent_keys


def record_sent(key, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        _sent_times.append(now)
        if key is not None:
            _sent_keys[key] = now


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            self.handle_request()
        except Exception as err:  # never leak a traceback; the form shows its own recovery options
            log("unexpected_error", error=type(err).__name__)
            self.reply(500)

    def handle_request(self):
        if not self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json":
            return self.reply(415)
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size > MAX_BYTES:
            return self.reply(413)
        if size <= 0:
            return self.reply(400)

        ip = (self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0]).strip() \
            or self.client_address[0]
        if not allow(ip):
            log("rate_limited")
            return self.reply(429)

        try:
            data = json.loads(self.rfile.read(size))
        except (ValueError, RecursionError):
            return self.reply(400)

        # The hidden "website" field is invisible to people, so only bots fill it in.
        # Answer as if it worked so they don't try again.
        if isinstance(data, dict) and data.get("website"):
            log("honeypot")
            return self.reply(200)

        req = parse(data)
        if req is None:
            return self.reply(400)
        if already_sent(req["key"]):
            log("duplicate_ignored", form=req["kind"])
            return self.reply(200)

        user = os.environ.get("SMTP_USER", "")
        password = os.environ.get("SMTP_PASS", "")
        if not user or not password:
            log("email_failed", error="SMTP_USER or SMTP_PASS is not set")
            return self.reply(503)
        try:
            send(build_email(req, user, os.environ.get("NOTIFY_TO") or DEFAULT_NOTIFY_TO), user, password)
        except (smtplib.SMTPException, OSError) as err:
            # Log the kind of failure only: the request holds customer details
            log("email_failed", error=type(err).__name__, code=getattr(err, "smtp_code", None))
            return self.reply(502)
        record_sent(req["key"])
        log("email_sent", form=req["kind"], texting=req["texting"])
        self.reply(200)

    def do_GET(self):
        self.reply(405)

    def reply(self, status):
        body = json.dumps({"ok": status == 200}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status == 405:
            self.send_header("Allow", "POST")
        if status == 429:
            self.send_header("Retry-After", "600")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # the default access log would print client IPs and paths; Vercel logs requests already
