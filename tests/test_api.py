"""Tests for api/request.py and api/health.py. Run: python3 -m unittest discover -s tests"""

import http.client
import io
import importlib.util
import json
import os
import smtplib
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location("api_" + name, os.path.join(ROOT, "api", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load("request")
health = load("health")

GOOD = {
    "form": "booking",
    "key": "0f8e2c1a-1111-4222-8333-444455556666",
    "name": "Jane Doe",
    "phone": "(718) 555-1234",
    "company": "",
    "website": "",
    "lines": ["Name: Jane Doe", "Phone: (718) 555-1234", "Notes: Gate 4\nCall first"],
}


class Server:
    """Runs a handler class on a free local port."""

    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        conn.request(method, path, body=body, headers=headers or {})
        res = conn.getresponse()
        return res.status, dict(res.getheaders()), res.read()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ParseTests(unittest.TestCase):
    def test_valid_booking(self):
        req = api.parse(GOOD)
        self.assertEqual(req["kind"], "booking")
        self.assertEqual(req["key"], GOOD["key"])
        self.assertFalse(req["texting"])
        self.assertEqual(req["lines"][2], "Notes: Gate 4\nCall first")

    def test_rejects(self):
        cases = {
            "unknown form": {**GOOD, "form": "admin"},
            "short name": {**GOOD, "name": "J"},
            "blank name": {**GOOD, "name": "   "},
            "9-digit phone": {**GOOD, "phone": "718555123"},
            "11 digits not starting with 1": {**GOOD, "phone": "27185551234"},
            "phone not a string": {**GOOD, "phone": 7185551234},
            "fleet without company": {**GOOD, "form": "fleet"},
            "no lines": {**GOOD, "lines": []},
            "too many lines": {**GOOD, "lines": ["a: b"] * 31},
            "lines not a list": {**GOOD, "lines": "Name: x"},
            "only control characters": {**GOOD, "lines": ["\x01\x02"]},
            "not an object": [GOOD],
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertIsNone(api.parse(data))

    def test_leading_1_phone_accepted(self):
        self.assertIsNotNone(api.parse({**GOOD, "phone": "+1 718 555 1234"}))

    def test_bad_key_is_ignored_not_rejected(self):
        for key in ["short", "x" * 65, "has spaces in it", 12345678, None]:
            with self.subTest(key=key):
                req = api.parse({**GOOD, "key": key})
                self.assertIsNotNone(req)
                self.assertIsNone(req["key"])

    def test_fields_are_trimmed_and_capped(self):
        req = api.parse({**GOOD, "name": "  " + "N" * 300, "lines": ["x" * 5000]})
        self.assertEqual(len(req["name"]), 100)
        self.assertEqual(len(req["lines"][0]), api.MAX_LINE)


class EmailTests(unittest.TestCase):
    def test_header_injection_is_neutralized(self):
        req = api.parse({**GOOD, "name": "Eve\r\nBcc: victim@example.com"})
        msg = api.build_email(req, "shop@example.com", "shop@example.com")
        self.assertIsNone(msg["Bcc"])
        self.assertNotIn("\n", msg["Subject"])

    def test_html_is_escaped(self):
        req = api.parse({**GOOD, "lines": ["Name: <script>alert(1)</script>", "Notes: <b>hi</b>"]})
        body = api.build_email(req, "a@b.c", "a@b.c").get_body(("html",)).get_content()
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<b>hi</b>", body)

    def test_call_and_text_buttons_use_validated_digits(self):
        req = api.parse({**GOOD, "phone": "1 (718) 555-1234 ext"})
        body = api.build_email(req, "a@b.c", "a@b.c").get_body(("html",)).get_content()
        self.assertIn('href="tel:+17185551234"', body)
        self.assertIn('href="sms:+17185551234"', body)

    def test_texted_copy_is_labelled(self):
        req = api.parse({**GOOD, "channel": "sms"})
        msg = api.build_email(req, "a@b.c", "a@b.c")
        self.assertIn("(texting you)", msg["Subject"])
        self.assertIn("If no text arrives", msg.get_body(("plain",)).get_content())

    def test_fleet_subject_names_company(self):
        req = api.parse({**GOOD, "form": "fleet", "company": "Acme Plumbing"})
        self.assertEqual(api.build_email(req, "a@b.c", "a@b.c")["Subject"], "Fleet quote request: Acme Plumbing (Jane Doe)")


class LimitTests(unittest.TestCase):
    def setUp(self):
        api._hits.clear()
        api._sent_times.clear()
        api._sent_keys.clear()

    def test_per_ip_limit_and_window(self):
        for _ in range(api.PER_IP_LIMIT):
            self.assertTrue(api.allow("1.2.3.4", now=0))
        self.assertFalse(api.allow("1.2.3.4", now=1))
        self.assertTrue(api.allow("5.6.7.8", now=1))
        self.assertTrue(api.allow("1.2.3.4", now=api.PER_IP_WINDOW + 1))

    def test_global_limit(self):
        for i in range(api.GLOBAL_LIMIT):
            api.record_sent(None, now=i)
        self.assertFalse(api.allow("9.9.9.9", now=api.GLOBAL_LIMIT))
        self.assertTrue(api.allow("9.9.9.9", now=api.GLOBAL_WINDOW + api.GLOBAL_LIMIT))

    def test_sent_keys_expire(self):
        api.record_sent("k" * 10, now=0)
        self.assertTrue(api.already_sent("k" * 10, now=10))
        self.assertFalse(api.already_sent("k" * 10, now=api.KEY_TTL + 1))
        self.assertFalse(api.already_sent(None, now=0))


class HandlerTests(unittest.TestCase):
    def setUp(self):
        api._hits.clear()
        api._sent_times.clear()
        api._sent_keys.clear()
        self.sent = []
        self.send_patch = mock.patch.object(api, "send", side_effect=lambda msg, u, p: self.sent.append(msg))
        self.send_patch.start()
        self.env = mock.patch.dict(os.environ, {"SMTP_USER": "shop@example.com", "SMTP_PASS": "x"})
        self.env.start()
        self.server = Server(api.handler)
        self.log = mock.patch.object(sys, "stderr", new_callable=io.StringIO)
        self.log.start()

    def tearDown(self):
        self.log.stop()
        self.server.close()
        self.env.stop()
        self.send_patch.stop()

    def post(self, body, ctype="application/json", headers=None):
        h = {"Content-Type": ctype} if ctype else {}
        h.update(headers or {})
        status, hdrs, data = self.server.request("POST", "/api/request", body, h)
        return status, hdrs, data

    def test_valid_request_sends_once(self):
        status, hdrs, body = self.post(GOOD)
        self.assertEqual((status, json.loads(body)), (200, {"ok": True}))
        self.assertEqual(hdrs["Cache-Control"], "no-store")
        self.assertEqual(len(self.sent), 1)

    def test_same_key_twice_sends_once(self):
        self.assertEqual(self.post(GOOD)[0], 200)
        self.assertEqual(self.post(GOOD)[0], 200)
        self.assertEqual(len(self.sent), 1)

    def test_new_key_sends_again(self):
        self.post(GOOD)
        self.post({**GOOD, "key": "another-key-123"})
        self.assertEqual(len(self.sent), 2)

    def test_failed_send_can_be_retried_with_same_key(self):
        with mock.patch.object(api, "send", side_effect=smtplib.SMTPServerDisconnected("down")):
            self.assertEqual(self.post(GOOD)[0], 502)
        self.assertEqual(self.post(GOOD)[0], 200)
        self.assertEqual(len(self.sent), 1)

    def test_honeypot_answers_ok_without_sending(self):
        status, _, body = self.post({**GOOD, "website": "http://spam"})
        self.assertEqual((status, json.loads(body)), (200, {"ok": True}))
        self.assertEqual(self.sent, [])

    def test_bad_requests(self):
        cases = [
            ("text/plain", GOOD, 415),
            (None, GOOD, 415),
            ("application/jsonx", GOOD, 415),
            ("application/json", "{not json", 400),
            ("application/json", b"\xff\xfe{}", 400),
            ("application/json", "[" * 9000 + "]" * 9000, 400),
            ("application/json", {**GOOD, "name": ""}, 400),
            ("application/json", {**GOOD, "lines": ["x" * 25_000]}, 413),
        ]
        for ctype, body, expected in cases:
            api._hits.clear()
            with self.subTest(ctype=ctype, expected=expected):
                self.assertEqual(self.post(body, ctype)[0], expected)
        self.assertEqual(self.sent, [])

    def test_charset_parameter_is_fine(self):
        self.assertEqual(self.post(GOOD, "application/json; charset=utf-8")[0], 200)

    def test_bad_content_length(self):
        self.assertEqual(self.post(GOOD, headers={"Content-Length": "-5"})[0], 400)
        self.assertEqual(self.post(GOOD, headers={"Content-Length": "abc"})[0], 400)

    def test_smtp_failures_are_502_and_log_no_customer_details(self):
        for err in [smtplib.SMTPAuthenticationError(535, b"bad"), TimeoutError("slow"), ConnectionRefusedError()]:
            api._hits.clear()
            with self.subTest(err=type(err).__name__), mock.patch.object(api, "send", side_effect=err), \
                    mock.patch.object(api, "log") as log:
                self.assertEqual(self.post(GOOD)[0], 502)
                logged = json.dumps([c.kwargs for c in log.call_args_list])
                self.assertIn(type(err).__name__, logged)
                self.assertNotIn("Jane", logged)
                self.assertNotIn("555", logged)

    def test_unexpected_error_is_500_not_a_crash(self):
        with mock.patch.object(api, "build_email", side_effect=KeyError("boom")):
            status, _, body = self.post(GOOD)
        self.assertEqual((status, json.loads(body)), (500, {"ok": False}))

    def test_missing_settings_is_503(self):
        with mock.patch.dict(os.environ, {"SMTP_PASS": ""}):
            self.assertEqual(self.post(GOOD)[0], 503)

    def test_rate_limit_per_ip(self):
        statuses = [self.post({**GOOD, "key": f"key-number-{i:04d}"}, headers={"X-Real-IP": "203.0.113.7"})[0]
                    for i in range(api.PER_IP_LIMIT + 2)]
        self.assertEqual(statuses[:api.PER_IP_LIMIT], [200] * api.PER_IP_LIMIT)
        self.assertEqual(statuses[api.PER_IP_LIMIT:], [429, 429])
        status, hdrs, _ = self.post(GOOD, headers={"X-Real-IP": "203.0.113.7"})
        self.assertEqual(hdrs["Retry-After"], "600")
        self.assertEqual(self.post(GOOD, headers={"X-Real-IP": "198.51.100.1"})[0], 200)

    def test_other_methods(self):
        status, hdrs, _ = self.server.request("GET", "/api/request")
        self.assertEqual((status, hdrs["Allow"]), (405, "POST"))
        # No CORS preflight support: browsers can't post JSON here from other sites
        self.assertEqual(self.server.request("OPTIONS", "/api/request")[0], 501)


class HealthTests(unittest.TestCase):
    TOKEN = "t0ken-for-tests-1234567890"

    def setUp(self):
        self.server = Server(health.handler)
        self.log = mock.patch.object(sys, "stderr", new_callable=io.StringIO)
        self.log.start()

    def tearDown(self):
        self.log.stop()
        self.server.close()

    def get(self, path, headers=None):
        status, _, body = self.server.request("GET", path, headers=headers)
        return status, json.loads(body)

    def test_hidden_without_token_setting(self):
        with mock.patch.dict(os.environ, {"HEALTH_TOKEN": ""}):
            self.assertEqual(self.get("/api/health?token=")[0], 404)

    def test_wrong_or_missing_token(self):
        with mock.patch.dict(os.environ, {"HEALTH_TOKEN": self.TOKEN}), \
                mock.patch.object(health, "check_smtp") as check:
            self.assertEqual(self.get("/api/health")[0], 404)
            self.assertEqual(self.get("/api/health?token=wrong")[0], 404)
            check.assert_not_called()

    def test_ok_with_query_or_bearer_token(self):
        with mock.patch.dict(os.environ, {"HEALTH_TOKEN": self.TOKEN}), \
                mock.patch.object(health, "check_smtp", return_value=None):
            self.assertEqual(self.get(f"/api/health?token={self.TOKEN}"), (200, {"ok": True}))
            self.assertEqual(self.get("/api/health", {"Authorization": f"Bearer {self.TOKEN}"}), (200, {"ok": True}))

    def test_smtp_failure_is_503(self):
        with mock.patch.dict(os.environ, {"HEALTH_TOKEN": self.TOKEN}), \
                mock.patch.object(health, "check_smtp", return_value="SMTPAuthenticationError"):
            self.assertEqual(self.get(f"/api/health?token={self.TOKEN}"),
                             (503, {"ok": False, "error": "SMTPAuthenticationError"}))

    def test_check_smtp_reports_missing_settings_and_errors(self):
        self.assertEqual(health.check_smtp("", ""), "SMTP_USER or SMTP_PASS is not set")
        with mock.patch.object(health.smtplib, "SMTP_SSL", side_effect=TimeoutError()):
            self.assertEqual(health.check_smtp("u", "p"), "TimeoutError")


if __name__ == "__main__":
    unittest.main()
