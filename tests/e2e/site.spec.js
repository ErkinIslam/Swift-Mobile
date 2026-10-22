// End-to-end tests: real browsers against scripts/dev_server.py (emails go to its outbox).
const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

// A fixed "now" so date rules are predictable: Tuesday 20 Oct 2026, 10:30 AM New York time
const NOW = new Date("2026-10-20T10:30:00-04:00");
const WEDNESDAY = "2026-10-21";
const SUNDAY = "2026-10-25";
const PAGES = ["/", "/fleet.html", "/about.html", "/404.html"];

const isPhone = (testInfo) => testInfo.project.name !== "desktop";
const outbox = async (request) => (await request.get("/__dev/outbox")).json();

test.beforeEach(async ({ request, page }) => {
  await request.post("/__dev/reset");
  await page.clock.setFixedTime(NOW);
});

async function fillBooking(page, overrides = {}) {
  const values = {
    "#f-name": "Jane Doe",
    "#f-phone": "(718) 555-1234",
    "#f-year": "2019",
    "#f-make": "Honda",
    "#f-model": "CR-V",
    "#f-address": "12 Main St, 10301",
  };
  for (const [sel, v] of Object.entries(overrides)) if (sel.startsWith("#")) values[sel] = v;
  for (const [sel, v] of Object.entries(values)) await page.fill(sel, v);
  await page.selectOption("#f-spot", "Driveway");
  await page.fill("#f-date", overrides.date || WEDNESDAY);
  await page.fill("#f-notes", "Gate code 4321");
}

test.describe("booking form on a computer", () => {
  test.skip(({ isMobile }) => isMobile, "computers email the request");

  test("emails us the request and confirms it was received", async ({ page, request }) => {
    await page.goto("/#book");
    await fillBooking(page);
    const submit = page.locator('#booking-form [type="submit"]');
    await submit.click();

    const sent = page.locator("#booking-form .form-sent");
    await expect(sent).toBeVisible();
    await expect(sent).toBeFocused();
    await expect(sent).toContainText("Request received. Thank you!");
    await expect(sent).toContainText("We'll text or call you soon to confirm your booking.");
    await expect(page.locator("#booking-form .form-sms")).toBeHidden();
    await expect(page.locator("#booking-form .form-failed")).toBeHidden();
    await expect(submit).toBeDisabled();
    await expect(submit).toHaveText("Request sent");

    const mail = await outbox(request);
    expect(mail).toHaveLength(1);
    expect(mail[0].to).toBe("oilchange@swiftmobileautocare.com");
    expect(mail[0].subject).toBe("Booking request: Jane Doe");
    for (const line of ["Name: Jane Doe", "Phone: (718) 555-1234", "Services: Oil Change", "Estimate: $99 + parts + tax",
      "Vehicle: 2019 Honda CR-V", "Parked: Driveway", "When: Wed, Oct 21, any time", "Notes: Gate code 4321"]) {
      expect(mail[0].text).toContain(line);
    }
  });

  test("no second email unless something changed", async ({ page, request }) => {
    await page.goto("/#book");
    await fillBooking(page);
    const submit = page.locator('#booking-form [type="submit"]');
    await submit.click();
    await expect(submit).toHaveText("Request sent");
    await page.locator("#booking-form").evaluate((f) => f.requestSubmit()); // e.g. Enter in a field
    await page.waitForTimeout(300);
    expect(await outbox(request)).toHaveLength(1);

    await page.fill("#f-notes", "Gate code 1234");
    await expect(submit).toBeEnabled();
    await expect(submit).toHaveText("Send updated request");
    await submit.click();
    await expect(submit).toHaveText("Request sent");
    const mail = await outbox(request);
    expect(mail).toHaveLength(2);
    expect(mail[1].text).toContain("Gate code 1234");
  });

  test("changing a quantity picker counts as a change", async ({ page, request }) => {
    await page.goto("/#book");
    await fillBooking(page);
    await page.check('input[value="Wiper blade replacement"]');
    const submit = page.locator('#booking-form [type="submit"]');
    await submit.click();
    await expect(submit).toHaveText("Request sent");
    await page.selectOption("#f-wiper-qty", "3");
    await expect(submit).toHaveText("Send updated request");
    await submit.click();
    await expect(submit).toHaveText("Request sent");
    expect((await outbox(request))[1].text).toContain("Wiper blade replacement (3 blades (incl. rear))");
  });

  test("server error: the customer is told, details kept, Try again works", async ({ page, request }) => {
    await page.route("**/api/request", (route) => route.fulfill({ status: 502, contentType: "application/json", body: '{"ok": false}' }));
    await page.goto("/#book");
    await fillBooking(page);
    await page.click('#booking-form [type="submit"]');

    const failed = page.locator("#booking-form .form-failed");
    await expect(failed).toBeVisible();
    await expect(failed).toContainText("We couldn't send your request.");
    await expect(failed).toContainText("(347) 479-6149");
    await expect(page.locator("#booking-form .form-sent")).toBeHidden();
    await expect(page.locator("#booking-form .form-retry")).toBeFocused();
    await expect(page.locator("#f-name")).toHaveValue("Jane Doe");
    await expect(page.locator('#booking-form [type="submit"]')).toBeEnabled();
    expect(await outbox(request)).toHaveLength(0);

    await page.unroute("**/api/request");
    await page.click("#booking-form .form-retry");
    await expect(page.locator("#booking-form .form-sent")).toBeVisible();
    await expect(failed).toBeHidden();
    expect(await outbox(request)).toHaveLength(1);
  });

  test("no connection: the customer is told", async ({ page }) => {
    await page.route("**/api/request", (route) => route.abort("internetdisconnected"));
    await page.goto("/#book");
    await fillBooking(page);
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-failed")).toBeVisible();
  });

  test("lost reply: Try again doesn't email twice", async ({ page, request }) => {
    let first = true;
    await page.route("**/api/request", async (route) => {
      if (!first) return route.continue();
      first = false;
      await route.fetch(); // the server sends the email...
      await route.abort("connectionreset"); // ...but its answer never arrives
    });
    await page.goto("/#book");
    await fillBooking(page);
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-failed")).toBeVisible();
    await page.click("#booking-form .form-retry");
    await expect(page.locator("#booking-form .form-sent")).toBeVisible();
    expect(await outbox(request)).toHaveLength(1);
  });

  test("double click: one email", async ({ page, request }) => {
    await page.goto("/#book");
    await fillBooking(page);
    await page.locator('#booking-form [type="submit"]').dblclick();
    await expect(page.locator("#booking-form .form-sent")).toBeVisible();
    expect(await outbox(request)).toHaveLength(1);
  });

  test("result messages are accessible", async ({ page }) => {
    await page.goto("/#book");
    await fillBooking(page);
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-sent")).toBeVisible();
    await page.evaluate(() => document.querySelectorAll(".reveal").forEach((el) => el.classList.add("is-visible")));
    let axe = await new AxeBuilder({ page }).include("#booking-form").analyze();
    expect(axe.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id)).toEqual([]);

    await page.route("**/api/request", (route) => route.abort("internetdisconnected"));
    await page.fill("#f-notes", "changed");
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-failed")).toBeVisible();
    axe = await new AxeBuilder({ page }).include("#booking-form").analyze();
    expect(axe.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id)).toEqual([]);
  });

  test("live estimate", async ({ page }) => {
    await page.goto("/#book");
    const total = page.locator("#estimate-total");
    await expect(total).toHaveText("$99 + parts + tax");
    await page.check('input[value="Check engine light diagnostic scan"]');
    await expect(total).toHaveText("$99 + parts + tax"); // free with an oil change
    await page.uncheck("#f-oil");
    await expect(total).toHaveText("$50 + parts + tax");
    await page.check('input[value="Wiper blade replacement"]');
    await expect(total).toHaveText("$70 + parts + tax"); // 2 blades by default
    await page.check('input[value="Manual gearbox / differential fluid change"]');
    await page.selectOption("#f-fluid-qty", { label: "Both" });
    await expect(total).toHaveText("$268 + parts + tax");
    await page.check('input[name="parts"][value="own"]');
    await expect(total).toHaveText("$268 + tax");
    await expect(page.locator("#estimate-lines")).toContainText("Manual gearbox + differential fluid change");
    await page.selectOption("#f-fluid-qty", { label: "Differential" });
    await expect(page.locator("#estimate-lines")).toContainText("Differential fluid change$99");
  });

  test("fleet quote", async ({ page, request }) => {
    await page.goto("/fleet.html#quote");
    await page.click('#fleet-form [type="submit"]');
    await expect(page.locator("#q-company")).toBeFocused();
    await expect(page.locator("#q-company-err")).toBeVisible();

    await page.fill("#q-company", "Acme Plumbing");
    await page.fill("#q-name", "Bob Smith");
    await page.fill("#q-phone", "347-555-0000");
    await page.fill("#q-address", "1 Depot Rd, Bronx");
    await page.selectOption("#q-count", "6–10");
    await page.click('#fleet-form [type="submit"]');
    await expect(page.locator("#fleet-form .form-sent")).toContainText("We'll get back to you soon with your quote.");
    const mail = await outbox(request);
    expect(mail).toHaveLength(1);
    expect(mail[0].subject).toBe("Fleet quote request: Acme Plumbing (Bob Smith)");
    expect(mail[0].text).toContain("Vehicles: 6–10");
  });

  test("wide screens use more of the width", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const width = await page.locator(".site-header .header-inner").evaluate((el) => el.getBoundingClientRect().width);
    expect(width).toBe(1440);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  });
});

test.describe("booking form on a phone", () => {
  test.skip(({ isMobile }) => !isMobile, "phones open their messaging app");

  test("opens the text with the whole request, and sends no email", async ({ page, request }, testInfo) => {
    let posted = 0;
    page.on("request", (r) => { if (r.url().includes("/api/request")) posted++; });
    await page.goto("/#book");
    await fillBooking(page);
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-sms")).toBeVisible();
    await expect(page.locator("#booking-form .form-sent")).toBeHidden();
    const href = await page.locator("#booking-form .form-sms .sms-fallback").getAttribute("href");
    // iOS wants "&body=", Android "?body="
    expect(href).toMatch(testInfo.project.name === "iphone" ? /^sms:\+13474796149&body=/ : /^sms:\+13474796149\?body=/);
    const body = decodeURIComponent(href);
    for (const line of ["Booking request - Swift Mobile Auto Care", "Name: Jane Doe", "Vehicle: 2019 Honda CR-V", "Notes: Gate code 4321"]) {
      expect(body).toContain(line);
    }

    await page.waitForTimeout(500);
    expect(posted).toBe(0);
    expect(await outbox(request)).toHaveLength(0);
  });

  test("mobile menu", async ({ page }) => {
    await page.goto("/");
    const toggle = page.locator(".menu-toggle");
    await toggle.click();
    await expect(page.locator("#mobile-menu")).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#mobile-menu a").first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.locator("#mobile-menu")).toBeHidden();
    await expect(toggle).toBeFocused();
    await toggle.click();
    await page.locator("#mobile-menu a", { hasText: "FAQ" }).click();
    await expect(page.locator("#mobile-menu")).toBeHidden();
  });
});

test.describe("form rules (every device)", () => {
  test("empty form: every error shown, first field focused", async ({ page }) => {
    await page.goto("/#book");
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#f-name")).toBeFocused();
    for (const id of ["f-name", "f-phone", "f-year", "f-make", "f-model", "f-address", "f-spot", "f-date"]) {
      await expect(page.locator(`#${id}-err`)).toBeVisible();
      await expect(page.locator(`#${id}`)).toHaveAttribute("aria-invalid", "true");
      await expect(page.locator(`#${id}`)).toHaveAttribute("aria-describedby", `${id}-err`);
    }
    // Fixing a field clears its error as you type
    await page.fill("#f-name", "Jo");
    await expect(page.locator("#f-name-err")).toBeHidden();
  });

  test("no Sundays, no past dates, at least one service", async ({ page }) => {
    await page.goto("/#book");
    await fillBooking(page, { date: SUNDAY });
    await page.uncheck("#f-oil");
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#f-date-err")).toBeVisible();
    await expect(page.locator("#f-oil-err")).toBeVisible();
    await page.check("#f-oil");
    await expect(page.locator("#f-oil-err")).toBeHidden();
    await page.fill("#f-date", "2026-10-19");
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#f-date-err")).toBeVisible();
  });

  test("today: slots that already started can't be picked", async ({ page }) => {
    await page.goto("/#book");
    await expect(page.locator("#f-date")).toHaveAttribute("min", "2026-10-20");
    await page.selectOption("#f-time", "8–10 AM");
    await page.fill("#f-date", "2026-10-20");
    await page.locator("#f-date").dispatchEvent("change");
    await expect(page.locator('#f-time option[data-start="8"]')).toBeDisabled();
    await expect(page.locator('#f-time option[data-start="10"]')).toBeDisabled(); // 10:30 now
    await expect(page.locator('#f-time option[data-start="12"]')).toBeEnabled();
    await expect(page.locator("#f-time")).toHaveValue("No preference");
    await page.fill("#f-date", WEDNESDAY);
    await page.locator("#f-date").dispatchEvent("change");
    await expect(page.locator('#f-time option[data-start="8"]')).toBeEnabled();
  });

  test("after the last slot starts, the earliest day is tomorrow", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-10-20T16:15:00-04:00"));
    await page.goto("/#book");
    await expect(page.locator("#f-date")).toHaveAttribute("min", "2026-10-21");
  });

  test("remember my details: filled in next visit, on both forms, until cleared", async ({ page }) => {
    await page.goto("/#book");
    await expect(page.locator("#booking-form .form-welcome")).toBeHidden();
    await expect(page.locator("#f-remember")).not.toBeChecked();
    await fillBooking(page);
    await page.check("#f-remember");
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-sms:visible, #booking-form .form-sent:visible")).toBeVisible();

    await page.goto("about:blank"); // a fresh visit, not a jump within the page or a reload
    await page.goto("/#book");
    await expect(page.locator("#booking-form .form-welcome")).toBeVisible();
    await expect(page.locator("#f-remember")).toBeChecked();
    for (const [sel, v] of [["#f-name", "Jane Doe"], ["#f-phone", "(718) 555-1234"], ["#f-year", "2019"],
      ["#f-make", "Honda"], ["#f-model", "CR-V"], ["#f-address", "12 Main St, 10301"], ["#f-spot", "Driveway"]]) {
      await expect(page.locator(sel), sel).toHaveValue(v);
    }
    await expect(page.locator("#f-date")).toHaveValue(""); // a new booking needs a new day
    await expect(page.locator("#f-notes")).toHaveValue("");

    // Name and phone carry over to the fleet form; the booking's address doesn't
    await page.goto("/fleet.html#quote");
    await expect(page.locator("#q-name")).toHaveValue("Jane Doe");
    await expect(page.locator("#q-phone")).toHaveValue("(718) 555-1234");
    await expect(page.locator("#q-address")).toHaveValue("");

    await page.goto("/#book");
    await page.click("#booking-form .form-welcome button");
    await expect(page.locator("#booking-form .form-welcome")).toBeHidden();
    await expect(page.locator("#f-name")).toHaveValue("");
    await expect(page.locator("#f-name")).toBeFocused();
    await expect(page.locator("#f-spot")).toHaveValue("");
    await expect(page.locator("#f-remember")).not.toBeChecked();
    expect(await page.evaluate(() => localStorage.getItem("swift-mobile-details"))).toBeNull();
  });

  test("remember my details left unticked: nothing is saved", async ({ page }) => {
    await page.goto("/#book");
    await fillBooking(page);
    await page.click('#booking-form [type="submit"]');
    await expect(page.locator("#booking-form .form-sms:visible, #booking-form .form-sent:visible")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("swift-mobile-details"))).toBeNull();
    await page.goto("about:blank");
    await page.goto("/#book");
    await expect(page.locator("#f-name")).toHaveValue("");
    await expect(page.locator("#booking-form .form-welcome")).toBeHidden();
  });

  test("text fields are capped to what the server keeps", async ({ page }) => {
    await page.goto("/#book");
    await page.fill("#f-notes", "x".repeat(1500));
    expect((await page.inputValue("#f-notes")).length).toBe(1000);
  });
});

test.describe("about page gallery", () => {
  test("filters and photo viewer", async ({ page }, testInfo) => {
    await page.goto("/about.html#experience");
    await page.click('.work-filter[data-filter="brakes"]');
    await expect(page.locator(".work-grid li:visible")).toHaveCount(2);
    await expect(page.locator('.work-filter[data-filter="brakes"]')).toHaveAttribute("aria-pressed", "true");
    await page.locator(".work-grid li:visible .work-item").first().click();
    const box = page.locator(".lightbox");
    await expect(box).toBeVisible();
    await expect(page.locator(".lightbox-count")).toHaveText("(1 of 2)");
    await expect(page.locator(".lightbox-img")).toHaveAttribute("src", /img\/work\/brakes\.jpg$/);
    if (!isPhone(testInfo)) {
      await page.keyboard.press("ArrowRight");
      await expect(page.locator(".lightbox-count")).toHaveText("(2 of 2)");
      await page.keyboard.press("ArrowRight");
      await expect(page.locator(".lightbox-count")).toHaveText("(1 of 2)");
    } else {
      await page.click(".lightbox-next");
      await expect(page.locator(".lightbox-count")).toHaveText("(2 of 2)");
    }
    await page.keyboard.press("Escape");
    await expect(box).toBeHidden();
  });
});

test.describe("every page", () => {
  for (const path of PAGES) {
    test(`${path}: no errors, no CSP violations, no third-party requests, accessible`, async ({ page }) => {
      const problems = [];
      page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
      page.on("pageerror", (e) => problems.push(e.message));
      page.on("request", (r) => { if (!r.url().startsWith("http://127.0.0.1")) problems.push("third-party request " + r.url()); });
      await page.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => console.error("CSP " + e.violatedDirective + " " + e.blockedURI)));

      const res = await page.goto(path, { waitUntil: "networkidle" });
      expect(res.status()).toBe(path === "/404.html" ? 200 : 200);
      expect(res.headers()["content-security-policy"]).toContain("default-src 'self'");
      expect(res.headers()["x-frame-options"]).toBe("DENY");

      await page.evaluate(() => document.fonts.ready);
      const fonts = await page.evaluate(() => [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family));
      expect(fonts).toEqual(expect.arrayContaining(["Lexend", "Source Sans 3"]));

      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

      // Scroll-reveal hides blocks until they're on screen; show them so contrast is measured on the real colors
      await page.evaluate(() => document.querySelectorAll(".reveal").forEach((el) => el.classList.add("is-visible")));
      await page.waitForTimeout(500);
      const axe = await new AxeBuilder({ page }).analyze();
      const serious = axe.violations.filter((v) => ["serious", "critical"].includes(v.impact))
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      expect(serious).toEqual([]);
      expect(problems).toEqual([]);
    });
  }

  test("small phones (320px): nothing overflows", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    for (const path of PAGES) {
      await page.goto(path);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), path).toBeLessThanOrEqual(0);
    }
  });

  test("unknown address shows the 404 page", async ({ page }) => {
    const res = await page.goto("/no/such/page");
    expect(res.status()).toBe(404);
    await expect(page.locator("h1")).toHaveText("Sorry, we couldn't find that page.");
    await expect(page.locator(".site-header .brand")).toHaveAttribute("href", "/");
  });

  test("printing shows every section", async ({ page }) => {
    await page.goto("/");
    await page.emulateMedia({ media: "print" });
    const hidden = await page.evaluate(() => [...document.querySelectorAll(".reveal")].filter((el) => getComputedStyle(el).opacity === "0").length);
    expect(hidden).toBe(0);
  });
});
