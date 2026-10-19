"""Emails booking and fleet quote requests from the website to the business inbox.

Runs on Vercel as a Python function at /api/request. Phones text their requests
instead (see script.js); on computers and tablets the forms post JSON:
{"form": "booking" | "fleet", "name", "phone", "company", "lines", "website"}
and this sends it as an email through the Google Workspace account below.

Settings (Vercel > Project > Settings > Environment Variables):
  SMTP_USER  the Google Workspace address that sends the email
  SMTP_PASS  an app password for that address (not its normal password)
  NOTIFY_TO  optional: where requests go (defaults to SMTP_USER)
"""

import html
import json
import os
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from http.server import BaseHTTPRequestHandler

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
MAX_BYTES = 20_000  # a full request is about 1 KB
MAX_LINES = 30
MAX_LINE = 2_000

FORMS = {
    "booking": "Booking request",
    "fleet": "Fleet quote request",
}


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
    return {"kind": kind, "name": name, "phone": phone, "company": company, "lines": lines}


def build_email(req, sender, to):
    title = FORMS[req["kind"]]
    who = f'{req["company"]} ({req["name"]})' if req["company"] else req["name"]
    tel = "+1" + phone_digits(req["phone"])
    first = html.escape(req["name"].split()[0][:20])

    msg = EmailMessage()
    msg["Subject"] = f"{title}: {who}"
    msg["From"] = formataddr(("Swift Mobile website", sender))
    msg["To"] = to
    msg.set_content("\n".join([title, ""] + req["lines"]) + "\n")

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
    msg.add_alternative(
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.4;color:#0A0A0A;max-width:560px">'
        f'<p style="margin:0 0 12px;font-size:20px;font-weight:bold">{title}</p>'
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


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.reply(415)
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        if not 0 < size <= MAX_BYTES:
            return self.reply(413 if size else 400)
        try:
            data = json.loads(self.rfile.read(size))
        except ValueError:
            return self.reply(400)

        # The hidden "website" field is invisible to people, so only bots fill it in.
        # Answer as if it worked so they don't try again.
        if isinstance(data, dict) and data.get("website"):
            return self.reply(200)

        req = parse(data)
        if req is None:
            return self.reply(400)

        user = os.environ.get("SMTP_USER", "")
        password = os.environ.get("SMTP_PASS", "")
        if not user or not password:
            print("Email not sent: SMTP_USER or SMTP_PASS is not set", file=sys.stderr)
            return self.reply(503)
        try:
            send(build_email(req, user, os.environ.get("NOTIFY_TO") or user), user, password)
        except (smtplib.SMTPException, OSError) as err:
            # Log the kind of failure only: the request holds customer details
            print(f"Email not sent: {type(err).__name__} {getattr(err, 'smtp_code', '')}", file=sys.stderr)
            return self.reply(502)
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
        self.end_headers()
        self.wfile.write(body)
