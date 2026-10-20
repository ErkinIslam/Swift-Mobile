# Swift Mobile Auto Care website

The website for Swift Mobile Auto Care LLC (mobile oil changes, Staten Island): four static pages and one small
Python function that emails booking and fleet-quote requests to the business. Hosted on Vercel. There's no build step
and no runtime dependencies.

```
index.html  fleet.html  about.html  404.html   pages (header, menu and footer are repeated in each)
styles.css  script.js                         shared by every page
api/request.py                                POST /api/request: emails a form request (Gmail SMTP)
api/health.py                                 GET  /api/health: checks email still works, for a monitor
fonts/  img/                                  self-hosted fonts (OFL licensed) and photos
vendor/                                       QR code library (drawn in the browser, computers only)
vercel.json                                   security headers and caching
scripts/dev_server.py                         local server that behaves like Vercel
tests/                                        Python tests (API, page consistency) and browser tests (tests/e2e)
```

## How a request reaches the business

Requests arrive as **texts from the customer's own number**, so you can answer them in the same thread:

- **Phones:** after "Send booking request", the messaging app opens with the whole request written out, addressed
  to (347) 479-6149. The customer only has to hit send.
- **Computers and tablets:** a QR code appears. Scanning it with the phone's camera opens the same pre-written text
  on their phone. If they can't scan it, the panel offers to text it from the computer (Mac/iPad Messages), copy it,
  or call.
- **Email backup:** every request is also emailed to `oilchange@swiftmobileautocare.com`, with "(texting you)" in the
  subject. If an email arrives and no text follows, the customer never hit send: reply with the email's
  "Text [name]" button.
- Each request has a key, so showing the code again, a double click or a retry doesn't send a second email.
  Changing the form makes it a new request (and a new email).

The QR code uses the standard `SMSTO:` format, which iPhone and most Android cameras open directly (a few older
Android phones need Google Lens). It's drawn in the browser by `vendor/qrcode-generator-1.4.4.js` (MIT licence,
from npm `qrcode-generator`), which only loads on computers and tablets.

## Run it locally

```sh
python3 scripts/dev_server.py        # http://127.0.0.1:8000
```

Emails are faked: they're kept in memory and listed at http://127.0.0.1:8000/__dev/outbox. To really send, set
`SMTP_USER` and `SMTP_PASS` and add `--real-smtp`. The server applies the headers in `vercel.json`, so a
Content-Security-Policy problem shows up locally the same way it would in production.

## Tests

```sh
python3 -m unittest discover -s tests      # API behaviour and page consistency (no installs needed)

cd tests/e2e
npm ci && npm run install-browsers         # first time only
npm test                                   # desktop Chrome, iPhone Safari and Android Chrome
npx html-validate ../../*.html             # HTML validation
```

GitHub Actions runs all of them on every push and pull request (`.github/workflows/ci.yml`).

## Editing content

The phone number and prices are written out in several places, on purpose (no build step). After changing them,
run `python3 -m unittest discover -s tests`: `tests/test_site.py` fails if any copy disagrees.

- **Phone number:** every `tel:`, `sms:` and `wa.me` link, the shown numbers, the `"telephone"` in each page's
  JSON-LD, and `BUSINESS.phoneE164` in `script.js`. Update `PHONE_E164` and `PHONE_DISPLAY` in
  `tests/test_site.py` too.
- **Prices:** the services list in `index.html`, the `data-price` on each booking-form checkbox, and the "$99 + tax"
  text on all pages.
- **Photos:** when replacing a photo, give it a new file name (browsers keep `/img/` files for a week).
- **Fonts:** files in `/fonts/` are cached for a year. If one changes, change its name (they include a version).

## Settings (Vercel > Project > Settings > Environment Variables)

| Variable | Needed | What it is |
|---|---|---|
| `SMTP_USER` | yes | Google Workspace address that sends the emails |
| `SMTP_PASS` | yes | An [app password](https://support.google.com/accounts/answer/185833) for that address |
| `NOTIFY_TO` | no | Where the email copies go (defaults to `oilchange@swiftmobileautocare.com`) |
| `HEALTH_TOKEN` | for monitoring | A long random string (at least 16 characters) that unlocks `/api/health` |

Changing the Google account's password cancels its app passwords. When that happens, the site can't send email
until `SMTP_PASS` is replaced, so the monitor below matters. A separate sending account (for example
`website@swiftmobileautocare.com`) limits the damage if the app password ever leaks: it only opens that mailbox.

## Monitoring: know when requests stop arriving

1. Set `HEALTH_TOKEN` in Vercel and redeploy.
2. In an uptime monitor (UptimeRobot, Better Stack, etc.), add an HTTP check of
   `https://swiftmobileautocare.com/api/health?token=<HEALTH_TOKEN>`, **once an hour**, alerting to the owner's
   phone or email. It answers 200 when Gmail accepts the login and 503 (with the kind of failure) when it doesn't.
   Polling much more often isn't wise: Google may flag an account that logs in constantly.
3. Optionally, alert on the function logs. Each line is JSON without customer details. The events are
   `email_sent`, `email_failed`, `rate_limited`, `duplicate_ignored`, `honeypot` and `unexpected_error`. Any
   `email_failed` or `unexpected_error` means requests aren't getting through.

## Abuse protection

`/api/request` needs no login, so it's protected by:
- a hidden honeypot field;
- a size limit;
- at most 8 requests per IP address per 10 minutes;
- at most 60 emails per hour per server instance.

The last two live in memory and reset when Vercel starts a fresh instance. They slow a flood down but aren't a
hard guarantee. For a durable limit, add a **Vercel Firewall rate-limit rule** on `/api/request` (for example 10
requests per minute per IP). That's set in the Vercel dashboard (Firewall), depending on your plan.

## Launch checklist (things outside this code)

- [ ] **Domain:** `swiftmobileautocare.com` currently points to a Squarespace "private site". Add the domain in
      Vercel and point DNS there, with the apex (`swiftmobileautocare.com`) as the main address and `www`
      redirecting to it. The pages' canonical links and sitemap use the apex.
- [ ] **Keep email working while moving DNS:** keep the Google MX record and the SPF (`v=spf1
      include:_spf.google.com ~all`) and DKIM (`google._domainkey`) TXT records.
- [ ] **Add a DMARC record** (there's none today). Start with
      `_dmarc  TXT  "v=DMARC1; p=none; rua=mailto:<owner address>"`.
- [ ] Set `SMTP_USER`, `SMTP_PASS`, `NOTIFY_TO` and `HEALTH_TOKEN`, then set up the monitor above.
- [ ] Enable Vercel Web Analytics for the project (the pages load `/_vercel/insights/script.js`; without it,
      that request fails quietly).
- [ ] After the first deploy, check that https://swiftmobileautocare.com/README.md and `/tests/test_api.py` are 404
      (`.vercelignore` keeps them off the site).
- [ ] Send one real booking from a computer (scan the QR code with an iPhone and with an Android phone), one from
      a phone, and one fleet quote. Confirm the texts arrive and the backup emails reach oilchange@.
