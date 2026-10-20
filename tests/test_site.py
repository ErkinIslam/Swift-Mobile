"""Consistency checks across the static pages. Run: python3 -m unittest discover -s tests

The phone number, prices, header and footer are written out in each page (there's no build
step), so these tests are what keeps the copies in sync."""

import json
import os
import re
import sys
import unittest
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["index.html", "fleet.html", "about.html", "404.html"]
PHONE_E164 = "+13474796149"
PHONE_DISPLAY = "(347) 479-6149"
# Server-side limits in api/request.py: a form field may not allow more than these
SERVER_MAX = {"name": 100, "phone": 40, "company": 100}


class Page(HTMLParser):
    def __init__(self, name):
        super().__init__(convert_charrefs=True)
        self.name = name
        self.elements = []  # (tag, attrs dict)
        self.ids = set()
        self.text = []
        self.jsonld = []
        self._in_jsonld = False
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            self.source = f.read()
        self.feed(self.source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.elements.append((tag, a))
        if "id" in a:
            if a["id"] in self.ids:
                raise AssertionError(f"{self.name}: duplicate id {a['id']}")
            self.ids.add(a["id"])
        self._in_jsonld = tag == "script" and a.get("type") == "application/ld+json"

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_jsonld = False

    def handle_data(self, data):
        if self._in_jsonld:
            self.jsonld.append(data)
        else:
            self.text.append(data)

    def all(self, tag=None, attr=None):
        return [a for t, a in self.elements if (tag is None or t == tag) and (attr is None or attr in a)]


PARSED = {name: Page(name) for name in PAGES}


class PhoneNumberTests(unittest.TestCase):
    def test_every_phone_link_uses_the_business_number(self):
        for name, page in PARSED.items():
            for a in page.all("a", "href"):
                href = a["href"]
                with self.subTest(page=name, href=href[:40]):
                    if href.startswith(("tel:", "sms:")):
                        self.assertEqual(href.split(":", 1)[1], PHONE_E164)
                    if "wa.me/" in href:
                        self.assertEqual(href, "https://wa.me/" + PHONE_E164[1:])

    def test_displayed_numbers_match(self):
        for name, page in PARSED.items():
            for found in re.findall(r"\(\d{3}\)\s\d{3}-\d{4}", " ".join(page.text)):
                self.assertEqual(found, PHONE_DISPLAY, name)

    def test_structured_data_and_script(self):
        for name, page in PARSED.items():
            for block in page.jsonld:
                data = json.loads(block)  # also checks it's valid JSON
                phones = re.findall(r'"telephone":\s*"([^"]+)"', json.dumps(data))
                for phone in phones:
                    self.assertEqual(re.sub(r"\D", "", phone), PHONE_E164[1:], name)
        with open(os.path.join(ROOT, "script.js"), encoding="utf-8") as f:
            self.assertIn(f'phoneE164: "{PHONE_E164}"', f.read())


class PriceTests(unittest.TestCase):
    def test_form_prices_match_the_price_list(self):
        page = PARSED["index.html"]
        listed = {}
        for m in re.finditer(r'<li><span>([^<]+?)(?:<span class="option-sub">.*?</span>)?</span><span class="addon-price">\$(\d+)',
                             page.source):
            listed[m.group(1).strip()] = m.group(2)
        main = re.search(r'<h3>Oil Change</h3>\s*<p class="price">\$(\d+)', page.source).group(1)
        listed["Oil Change"] = main
        boxes = [a for a in page.all("input") if a.get("name") == "service"]
        self.assertEqual(len(boxes), 7)
        for box in boxes:
            with self.subTest(service=box["value"]):
                self.assertIn(box["value"], listed, "service in the form but not in the price list")
                self.assertEqual(box["data-price"], listed[box["value"]])

    def test_oil_change_price_is_the_same_everywhere(self):
        price = re.search(r'value="Oil Change" data-price="(\d+)"', PARSED["index.html"].source).group(1)
        for name in PAGES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                text = f.read()
            for found in re.findall(r"\$(\d+) \+ (?:tax|parts|oil)", text):
                self.assertEqual(found, price, name)


class LinkTests(unittest.TestCase):
    def test_internal_links_and_anchors_exist(self):
        for name, page in PARSED.items():
            for tag, attr in [("a", "href"), ("img", "src"), ("link", "href"), ("script", "src")]:
                for el in page.all(tag, attr):
                    url = el[attr]
                    parts = urlsplit(url)
                    if parts.scheme or url.startswith("//") or url.startswith("/_vercel/"):
                        continue
                    path = unquote(parts.path)
                    target = name if path == "" else ("index.html" if path == "/" else path.lstrip("/"))
                    with self.subTest(page=name, url=url):
                        self.assertTrue(os.path.exists(os.path.join(ROOT, target)), "missing file")
                        if parts.fragment and target in PARSED:
                            self.assertIn(parts.fragment, PARSED[target].ids, "missing anchor")
            for el in page.all("img", "srcset"):
                for candidate in el["srcset"].split(","):
                    src = candidate.strip().split()[0]
                    self.assertTrue(os.path.exists(os.path.join(ROOT, src)), f"{name}: {src}")

    def test_label_and_aria_targets_exist(self):
        for name, page in PARSED.items():
            for attr in ["for", "aria-controls", "aria-labelledby", "aria-describedby"]:
                for el in page.all(None, attr):
                    for ref in el[attr].split():
                        self.assertIn(ref, page.ids, f"{name}: {attr}={ref}")

    def test_external_links_open_safely(self):
        for name, page in PARSED.items():
            for a in page.all("a", "target"):
                self.assertIn("noopener", a.get("rel", ""), f"{name}: {a.get('href')}")

    def test_sitemap_lists_real_pages(self):
        with open(os.path.join(ROOT, "sitemap.xml"), encoding="utf-8") as f:
            urls = re.findall(r"<loc>https://swiftmobileautocare\.com/([^<]*)</loc>", f.read())
        self.assertEqual(sorted(u or "index.html" for u in urls), ["about.html", "fleet.html", "index.html"])


class FormTests(unittest.TestCase):
    def test_field_limits_fit_the_server(self):
        for name in ["index.html", "fleet.html"]:
            for el in PARSED[name].all(None, "name"):
                if el.get("name") in SERVER_MAX:
                    with self.subTest(page=name, field=el.get("id")):
                        self.assertIn("maxlength", el)
                        self.assertLessEqual(int(el["maxlength"]), SERVER_MAX[el["name"]])
            for el in PARSED[name].all("textarea"):
                if el.get("name") == "notes":
                    self.assertLessEqual(int(el["maxlength"]), 1900)  # the server keeps 2,000 per line

    def test_every_validated_field_has_an_error_message(self):
        with open(os.path.join(ROOT, "script.js"), encoding="utf-8") as f:
            script = f.read()
        for field in re.findall(r'"((?:f|q)-[a-z]+)": (?:\(|phoneRule)', script):
            page = PARSED["index.html" if field.startswith("f-") else "fleet.html"]
            with self.subTest(field=field):
                self.assertIn(field, page.ids)
                self.assertIn(field + "-err", page.ids)

    def test_forms_have_all_result_panels(self):
        for name in ["index.html", "fleet.html"]:
            for cls in ["form-sent", "form-sms", "form-failed", "form-copy", "form-retry", "form-hp"]:
                self.assertIn(cls, PARSED[name].source, f"{name}: .{cls}")


class SharedLayoutTests(unittest.TestCase):
    @staticmethod
    def nav_targets(page):
        """Main nav links, written the same way whatever page they're on ("#faq", "fleet.html")."""
        nav = re.search(r'<nav class="nav" aria-label="Main">(.*?)</nav>', page.source, re.S).group(1)
        return [re.sub(r"^/|^index\.html", "", href) for href in re.findall(r'href="([^"]+)"', nav)]

    def test_main_nav_is_the_same_on_every_page(self):
        expected = self.nav_targets(PARSED["index.html"])
        for name in PAGES[1:]:
            self.assertEqual(self.nav_targets(PARSED[name]), expected, name)

    def test_every_page_loads_the_shared_assets(self):
        for name, page in PARSED.items():
            loaded = " ".join(x for _, a in page.elements for x in [a.get("href"), a.get("src")] if x)
            for asset in ["styles.css", "script.js", "fonts/lexend-latin-v26.woff2", "fonts/source-sans-3-latin-v20.woff2"]:
                self.assertIn(asset, loaded, f"{name}: {asset}")

    def test_no_third_party_requests(self):
        allowed = ("https://www.instagram.com", "https://www.tiktok.com", "https://www.facebook.com",
                   "https://wa.me", "https://g.page", "https://www.evrionllc.com", "https://swiftmobileautocare.com",
                   "https://schema.org")
        for name, page in PARSED.items():
            for tag, attr in [("img", "src"), ("link", "href"), ("script", "src")]:
                for el in page.all(tag, attr):
                    url = el[attr]
                    if tag == "link" and el.get("rel") in ("canonical",):
                        continue
                    self.assertFalse(url.startswith(("http:", "https:", "//")), f"{name}: loads {url}")
            for url in re.findall(r'href="(https?://[^"]+)"', page.source):
                self.assertTrue(url.startswith(allowed), f"{name}: unexpected link {url}")


if __name__ == "__main__":
    unittest.main()
