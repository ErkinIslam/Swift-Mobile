/* Swift Mobile Auto Care: site behavior (shared by index.html, fleet.html and about.html) */

// The business number for texted requests. The pages' call/text/WhatsApp links are plain HTML;
// tests/test_site.py fails if any of them (or this) stops matching.
const BUSINESS = {
  phoneE164: "+13474796149",
};

(function () {
  "use strict";

  const year = document.getElementById("year");
  if (year) year.textContent = new Date().getFullYear();

  // ---- Mobile menu ------------------------------------------------------
  const menuToggle = document.querySelector(".menu-toggle");
  const mobileMenu = document.getElementById("mobile-menu");
  if (menuToggle && mobileMenu) {
    const setMenu = (open) => {
      mobileMenu.hidden = !open;
      menuToggle.setAttribute("aria-expanded", String(open));
      menuToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    };
    menuToggle.addEventListener("click", () => {
      const open = mobileMenu.hidden;
      setMenu(open);
      const first = mobileMenu.querySelector("a");
      if (open && first) first.focus();
    });
    mobileMenu.addEventListener("click", (e) => {
      if (e.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !mobileMenu.hidden) {
        setMenu(false);
        menuToggle.focus();
      }
    });
    window.matchMedia("(min-width: 900px)").addEventListener("change", (e) => {
      if (e.matches) setMenu(false);
    });
  }

  // ---- Shared form helpers ----------------------------------------------
  const phoneRule = (v) => v.replace(/\D/g, "").replace(/^1/, "").length === 10;

  function buildSmsHref(body) {
    // iOS uses "&body=", most other platforms use "?body="
    const sep = /iPad|iPhone|iPod|Macintosh/.test(navigator.userAgent) ? "&" : "?";
    return "sms:" + BUSINESS.phoneE164 + sep + "body=" + encodeURIComponent(body);
  }

  // Posts a request to api/request.py, which emails it to us. True if it was sent
  async function sendRequest(payload) {
    if (location.protocol === "file:") return false; // page opened from disk: no server
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), 20000);
    try {
      const res = await fetch("/api/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: timeout.signal,
      });
      return res.ok;
    } catch (err) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  // Identifies one request, so sending it again (a retry, a double tap) doesn't email us twice
  const newKey = () => (window.crypto && crypto.randomUUID
    ? crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2));

  // "Remember my details": sending a form with it ticked saves the fields marked data-remember in
  // this browser only (nothing extra reaches us), and the forms fill them back in next visit.
  // data-remember="shared" fields (name, phone) fill in on both forms; the rest only on their own.
  const DETAILS_KEY = "swift-mobile-details";
  const loadDetails = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(DETAILS_KEY));
      return saved && typeof saved === "object" ? saved : {};
    } catch (err) {
      return {}; // storage blocked (private browsing, site data off) or unreadable
    }
  };
  // null forgets everything saved
  const saveDetails = (saved) => {
    try {
      if (saved) localStorage.setItem(DETAILS_KEY, JSON.stringify(saved));
      else localStorage.removeItem(DETAILS_KEY);
    } catch (err) {
      // storage blocked: nothing is remembered, the form still works
    }
  };

  // Phones text their request from their messaging app, so it reaches us as a text from the
  // customer's own number. Computers and tablets (which usually can't text) email it to us.
  const isPhone = /Android.+Mobile|iPhone|iPod/.test(navigator.userAgent);

  // Validates fields by id (errors live in #<id>-err), then sends the message built by
  // buildLines(): texted from the messaging app on phones, emailed everywhere else.
  function setupRequestForm(form, kind, rules, buildLines) {
    const panels = {
      sms: form.querySelector(".form-sms"),
      sent: form.querySelector(".form-sent"),
      failed: form.querySelector(".form-failed"),
    };
    const showPanel = (name) => {
      Object.entries(panels).forEach(([key, el]) => { if (el) el.hidden = key !== name; });
    };
    const submit = form.querySelector('[type="submit"]');
    const submitLabel = submit.textContent;
    const note = form.querySelector(".form-note");
    if (!isPhone && note) note.textContent = note.dataset.emailNote;

    const retryBtn = form.querySelector(".form-retry");
    if (retryBtn) {
      retryBtn.addEventListener("click", () => {
        if (form.requestSubmit) form.requestSubmit(); else submit.click();
      });
    }

    let key = newKey();
    let sentKey = null; // key of the request that reached us
    let sending = false;

    // Changing anything after sending makes it a new request, which can be sent again
    const markChanged = () => {
      key = newKey();
      if (sentKey !== null && !sending) {
        sentKey = null;
        submit.disabled = false;
        submit.textContent = isPhone ? submitLabel : "Send updated request";
      }
    };
    const onEdit = (e) => {
      if (e.target.name === "website" || e.target.closest(".form-remember")) return;
      markChanged();
    };
    form.addEventListener("input", onEdit);
    form.addEventListener("change", onEdit);

    // Remembered details (see loadDetails). The checkbox is hidden without JS, where it can't work.
    const remembered = Array.from(form.querySelectorAll("[data-remember]"));
    const scopeOf = (el) => (el.dataset.remember === "shared" ? "shared" : kind);
    const rememberRow = form.querySelector(".form-remember");
    const rememberBox = rememberRow && rememberRow.querySelector("input");
    const welcome = form.querySelector(".form-welcome");
    if (rememberBox && welcome) {
      rememberRow.hidden = false;
      const saved = loadDetails();
      let filled = false;
      remembered.forEach((el) => {
        const v = (saved[scopeOf(el)] || {})[el.name];
        if (typeof v !== "string" || !v) return;
        if (el.tagName === "SELECT" && !Array.from(el.options).some((o) => o.value === v && !o.disabled)) return;
        el.value = v;
        filled = true;
      });
      if (filled) {
        rememberBox.checked = true;
        welcome.hidden = false;
      }

      // "Not you?": forget them here and on the other form, and empty the fields
      welcome.querySelector("button").addEventListener("click", () => {
        saveDetails(null);
        remembered.forEach((el) => {
          if (el.tagName === "SELECT") Array.from(el.options).forEach((o) => { o.selected = o.defaultSelected; });
          else el.value = "";
        });
        rememberBox.checked = false;
        welcome.hidden = true;
        markChanged();
        remembered[0].focus();
      });
    }

    // Called once the form is valid: ticked saves the details, unticked forgets any saved before
    function storeDetails() {
      if (!rememberBox) return;
      if (!rememberBox.checked) {
        saveDetails(null);
        return;
      }
      const saved = loadDetails();
      remembered.forEach((el) => {
        const scope = scopeOf(el);
        if (!saved[scope] || typeof saved[scope] !== "object") saved[scope] = {};
        saved[scope][el.name] = el.value.trim();
      });
      saveDetails(saved);
    }

    function validateField(id) {
      const input = document.getElementById(id);
      const err = document.getElementById(id + "-err");
      const ok = rules[id](input.value);
      input.setAttribute("aria-invalid", ok ? "false" : "true");
      if (ok) input.removeAttribute("aria-describedby");
      else input.setAttribute("aria-describedby", id + "-err");
      err.hidden = ok;
      return ok;
    }

    // Validate on blur, then live once a field has been flagged
    Object.keys(rules).forEach((id) => {
      const input = document.getElementById(id);
      input.addEventListener("blur", () => {
        if (input.value) validateField(id);
      });
      input.addEventListener("input", () => {
        if (input.getAttribute("aria-invalid") === "true") validateField(id);
      });
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (sending) return;

      const results = Object.keys(rules).map(validateField);
      if (results.includes(false)) {
        const firstBad = form.querySelector('[aria-invalid="true"]');
        if (firstBad) firstBad.focus();
        return;
      }
      storeDetails();

      const data = new FormData(form);
      const lines = buildLines(data);
      const notes = (data.get("notes") || "").trim();
      if (notes) lines.push("Notes: " + notes);

      if (isPhone) {
        const href = buildSmsHref(lines.join("\n"));
        form.querySelectorAll(".sms-fallback").forEach((a) => { a.href = href; });
        sentKey = key;
        showPanel("sms");
        panels.sms.focus();
        window.location.href = href;
        return;
      }

      if (sentKey === key) return; // already sent, and nothing changed since
      sending = true;
      submit.disabled = true;
      submit.textContent = "Sending…";
      const sendingKey = key;
      const ok = await sendRequest({
        form: kind,
        key: sendingKey,
        name: data.get("name"),
        phone: data.get("phone"),
        company: data.get("company") || "",
        website: data.get("website") || "", // hidden field only bots fill in
        lines: lines.slice(1), // the first line is the title, only needed in a text
      });
      sending = false;
      if (ok) {
        sentKey = sendingKey;
        showPanel("sent");
        panels.sent.focus();
        if (key === sendingKey) {
          submit.textContent = "Request sent";
          return; // stays disabled until something changes
        }
        // Edited while it was sending: the new version can be sent too
        submit.disabled = false;
        submit.textContent = "Send updated request";
        return;
      }
      submit.disabled = false;
      submit.textContent = submitLabel;
      showPanel("failed");
      if (retryBtn) retryBtn.focus();
    });

    return validateField;
  }

  // ---- Booking form (index.html) ----------------------------------------
  const form = document.getElementById("booking-form");
  if (form) {
    // "Book this" buttons tick that service in the form (data-service = checkbox id)
    document.querySelectorAll("[data-service]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const box = document.getElementById(btn.dataset.service);
        if (!box.checked) box.click();
      });
    });

    // Date and time: no past days, and no time slots that have already started today.
    // Once the last slot has started, the earliest day is tomorrow.
    const dateInput = document.getElementById("f-date");
    const timeSelect = document.getElementById("f-time");
    const slots = Array.from(timeSelect.options).filter((o) => o.dataset.start);
    const lastStart = Math.max(...slots.map((o) => Number(o.dataset.start)));
    const toISO = (d) => {
      const off = d.getTimezoneOffset();
      return new Date(d.getTime() - off * 60000).toISOString().slice(0, 10);
    };
    // Rerun before validating too, in case the page was left open for hours
    const refreshDateLimits = () => {
      const now = new Date();
      const today = toISO(now);
      const earliest = new Date(now);
      if (now.getHours() >= lastStart) earliest.setDate(earliest.getDate() + 1);
      dateInput.min = toISO(earliest);
      slots.forEach((o) => {
        o.disabled = dateInput.value === today && Number(o.dataset.start) <= now.getHours();
      });
      if (timeSelect.selectedOptions[0] && timeSelect.selectedOptions[0].disabled) timeSelect.value = "No preference";
    };
    refreshDateLimits();
    dateInput.addEventListener("change", refreshDateLimits);
    dateInput.addEventListener("focus", refreshDateLimits);

    // Live price estimate (prices come from data-price in the form)
    const serviceBoxes = form.querySelectorAll('input[name="service"]');
    const estimateLines = document.getElementById("estimate-lines");
    const estimateTotal = document.getElementById("estimate-total");
    const estimateNote = document.getElementById("estimate-note");

    const getEstimate = () => {
      const items = [];
      serviceBoxes.forEach((box) => {
        if (!box.checked) return;
        const qtySelect = box.dataset.qty ? document.getElementById(box.dataset.qty) : null;
        const qty = qtySelect ? Number(qtySelect.value) : 1;
        // data-free-with: this service is free when that other service is ticked
        const free = box.dataset.freeWith && document.getElementById(box.dataset.freeWith).checked;
        // Name the choice made in the picker: an option's data-label replaces the name
        // (e.g. "Differential fluid change"); otherwise its text is added, e.g. "(2 blades)"
        const picked = qtySelect ? qtySelect.selectedOptions[0] : null;
        const base = picked ? (picked.dataset.label || box.value + " (" + picked.textContent + ")") : box.value;
        const name = base + (free ? " (free with oil change)" : "");
        items.push([name, free ? 0 : Number(box.dataset.price) * qty]);
      });
      const labor = items.reduce((sum, item) => sum + item[1], 0);
      const ownParts = form.elements.parts.value === "own";
      const total = items.length
        ? "$" + labor + (ownParts ? "" : " + parts") + " + tax"
        : "Choose a service";
      return { items, ownParts, total };
    };

    const renderEstimate = () => {
      // Show the quantity picker under any ticked add-on that has one
      serviceBoxes.forEach((box) => {
        if (box.dataset.qty) document.getElementById(box.dataset.qty + "-row").hidden = !box.checked;
        if (box.dataset.freeWith) {
          const free = document.getElementById(box.dataset.freeWith).checked;
          box.closest(".choice").querySelector("[data-price-label]").textContent = free ? "Free" : "$" + box.dataset.price;
        }
      });
      const est = getEstimate();
      estimateLines.replaceChildren(...est.items.map(([name, price]) => {
        const li = document.createElement("li");
        li.append(Object.assign(document.createElement("span"), { textContent: name }));
        li.append(Object.assign(document.createElement("span"), { textContent: price ? "$" + price : "Free" }));
        return li;
      }));
      estimateTotal.textContent = est.total;
      estimateNote.textContent = est.ownParts
        ? "You're providing the parts, so there's no parts cost."
        : "Parts are extra, at store price with no upcharge.";
    };

    form.addEventListener("change", renderEstimate);
    renderEstimate();

    const formatDate = (v) => new Date(v + "T12:00:00").toLocaleDateString("en-US", {
      weekday: "short", month: "short", day: "numeric",
    });

    const validateField = setupRequestForm(form, "booking", {
      "f-name": (v) => v.trim().length >= 2,
      "f-phone": phoneRule,
      "f-oil": () => form.querySelector('input[name="service"]:checked') !== null, // at least one service
      "f-year": (v) => {
        const y = Number(v);
        return /^\d{4}$/.test(v.trim()) && y >= 1960 && y <= new Date().getFullYear() + 1;
      },
      "f-make": (v) => v.trim().length >= 2,
      "f-model": (v) => v.trim().length >= 1,
      "f-address": (v) => v.trim().length >= 5,
      "f-spot": (v) => v !== "",
      "f-date": (v) => {
        refreshDateLimits();
        if (!v) return false;
        const d = new Date(v + "T12:00:00");
        return v >= dateInput.min && d.getDay() !== 0; // closed Sundays
      },
    }, (f) => [
      "Booking request - Swift Mobile Auto Care",
      "Name: " + f.get("name").trim(),
      "Phone: " + f.get("phone").trim(),
      "Services: " + getEstimate().items.map((item) => item[0]).join(", "),
      "Parts: " + (f.get("parts") === "own" ? "Customer provides their own" : "Please bring them"),
      "Estimate: " + getEstimate().total,
      "Vehicle: " + [f.get("year"), f.get("make"), f.get("model")].map((s) => s.trim()).join(" "),
      "Address: " + f.get("address").trim(),
      "Parked: " + f.get("spot"),
      "When: " + formatDate(f.get("date")) + ", " + (f.get("time") === "No preference" ? "any time" : f.get("time")),
    ]);

    // Clear the "choose a service" error as soon as any box is ticked
    form.addEventListener("change", (e) => {
      if (e.target.name === "service" && document.getElementById("f-oil").getAttribute("aria-invalid") === "true") {
        validateField("f-oil");
      }
    });
  }

  // ---- Fleet quote form (fleet.html) ------------------------------------
  const fleetForm = document.getElementById("fleet-form");
  if (fleetForm) {
    setupRequestForm(fleetForm, "fleet", {
      "q-company": (v) => v.trim().length >= 2,
      "q-name": (v) => v.trim().length >= 2,
      "q-phone": phoneRule,
      "q-address": (v) => v.trim().length >= 5,
    }, (f) => {
      const lines = [
        "Fleet quote request - Swift Mobile Auto Care",
        "Company: " + f.get("company").trim(),
        "Contact: " + f.get("name").trim(),
        "Phone: " + f.get("phone").trim(),
        "Vehicles: " + f.get("count"),
        "Schedule: " + f.get("schedule"),
        "Invoicing: " + f.get("invoice"),
      ];
      const types = f.get("types").trim();
      if (types) lines.push("Vehicle types: " + types);
      lines.push("Location: " + f.get("address").trim());
      return lines;
    });
  }

  // ---- Work gallery (about.html): filters + photo viewer ----------------
  const workGrid = document.querySelector(".work-grid");
  if (workGrid) {
    const filters = document.querySelector(".work-filters");
    const items = Array.from(workGrid.querySelectorAll("li"));

    // Filters are hidden without JS, so every photo still shows
    filters.hidden = false;
    filters.addEventListener("click", (e) => {
      const btn = e.target.closest(".work-filter");
      if (!btn) return;
      filters.querySelectorAll(".work-filter").forEach((b) => {
        b.setAttribute("aria-pressed", String(b === btn));
      });
      const cat = btn.dataset.filter;
      items.forEach((li) => {
        li.hidden = cat !== "all" && li.dataset.cat !== cat;
      });
    });

    // Viewer: without <dialog> support, links just open the full-size image
    const box = document.querySelector(".lightbox");
    if (box && typeof box.showModal === "function") {
      const img = box.querySelector(".lightbox-img");
      const cap = box.querySelector(".lightbox-cap");
      const count = box.querySelector(".lightbox-count");
      let shown = [];
      let index = 0;

      const show = (i) => {
        index = (i + shown.length) % shown.length;
        const link = shown[index].querySelector(".work-item");
        img.src = link.href;
        img.width = link.dataset.width;
        img.height = link.dataset.height;
        img.alt = link.querySelector("img").alt;
        cap.textContent = link.querySelector(".work-cap").textContent;
        count.textContent = "(" + (index + 1) + " of " + shown.length + ")";
      };

      workGrid.addEventListener("click", (e) => {
        const link = e.target.closest(".work-item");
        if (!link) return;
        e.preventDefault();
        shown = items.filter((li) => !li.hidden);
        show(shown.indexOf(link.parentElement));
        box.showModal();
      });
      box.querySelector(".lightbox-close").addEventListener("click", () => box.close());
      box.querySelector(".lightbox-prev").addEventListener("click", () => show(index - 1));
      box.querySelector(".lightbox-next").addEventListener("click", () => show(index + 1));
      box.addEventListener("keydown", (e) => {
        if (e.key === "ArrowLeft") show(index - 1);
        if (e.key === "ArrowRight") show(index + 1);
      });
      // Tap on the dark area around the photo closes the viewer
      box.addEventListener("click", (e) => {
        if (e.target === box || e.target.classList.contains("lightbox-inner")) box.close();
      });
      // Swipe left / right on phones
      let touchX = null;
      box.addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
      box.addEventListener("touchend", (e) => {
        if (touchX === null) return;
        const dx = e.changedTouches[0].clientX - touchX;
        if (Math.abs(dx) > 50) show(dx < 0 ? index + 1 : index - 1);
        touchX = null;
      });
      // Free the full-size image when closed
      box.addEventListener("close", () => { img.removeAttribute("src"); });
    }
  }

  // ---- Subtle scroll reveal ---------------------------------------------
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduceMotion && "IntersectionObserver" in window) {
    const targets = document.querySelectorAll(
      ".section-head, .price-card, .addons, .step, .why-item, .feature-card, .contact-card, .chips, .book-form, .fleet-band, .gallery, .tech-grid, .work-grid"
    );
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    targets.forEach((el) => {
      el.classList.add("reveal");
      io.observe(el);
    });
  }
})();
