/**
 * Three entry points, all injected into the page via
 * chrome.scripting.executeScript (which requires each function, and
 * anything it calls, to be fully self-contained - no outside closure
 * references, nothing shared between separate injections):
 *
 * - enumerateFormFieldsOnPage(): finds the best contact form on the page
 *   and returns every field on it as plain JSON (same shape
 *   email_pipeline/llm_form_filler.py's enumerate_fields produces, minus
 *   the Playwright "locator" that has no browser-extension equivalent) -
 *   popup.js sends this to POST /api/forms/classify-fields for the SAME
 *   LLM-based understanding (selects, radio groups, split first/last-name
 *   fields, a required routing dropdown) the headless crawler now has.
 * - applyFieldActionsOnPage(actions): re-enumerates the SAME form (the
 *   two calls can't share live DOM references across the extension/page
 *   boundary - only plain JSON crosses it) and applies the actions the
 *   backend returned. Relies on the enumeration being deterministic, i.e.
 *   the page not changing between the two calls - reasonable for a static
 *   contact form.
 * - fillContactFormOnPage(fields): the original plain keyword matcher
 *   (ported from form_crawler.py's FIELD_KEYWORDS/classify_field/
 *   find_best_form/fill_form) - kept as the fallback for when no DeepSeek
 *   key is configured server-side or the classify-fields call fails, so
 *   the extension never just stops working without one.
 */

// Shared by all three entry points below - duplicated into each function's
// own body (not a real shared declaration, since each injection is its own
// isolated call) is exactly what "self-contained, no outside closure
// references" already requires throughout this file.
function _ingwerFormHelpers() {
  const COOKIE_ACCEPT_TEXTS = [
    "alle akzeptieren", "akzeptieren", "alle cookies akzeptieren", "zustimmen",
    "accept all", "accept all cookies", "i accept", "accept",
    "alles akzeptieren", "ok", "got it", "verstanden",
  ];
  const CAPTCHA_KEYWORDS = [
    "captcha", "sicherheitscode", "sicherheitsabfrage", "zeichenfolge",
    "prüfcode", "pruefcode", "tragen sie", "rechenaufgabe", "spamschutz",
    "code eingeben", "schutzcode",
  ];

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }

  function hasCaptcha() {
    const images = Array.from(document.querySelectorAll("img"));
    if (images.some((img) => {
      const src = (img.getAttribute("src") || "").toLowerCase();
      const alt = (img.getAttribute("alt") || "").toLowerCase();
      return isVisible(img) && (src.includes("captcha") || alt.includes("captcha"));
    })) {
      return true;
    }
    const text = document.body.innerText.toLowerCase();
    return CAPTCHA_KEYWORDS.some((kw) => text.includes(kw));
  }

  function dismissCookieBanner() {
    const buttons = Array.from(document.querySelectorAll("button, a"));
    for (const text of COOKIE_ACCEPT_TEXTS) {
      const match = buttons.find((b) => b.innerText && b.innerText.trim().toLowerCase() === text && isVisible(b));
      if (match) {
        match.click();
        return true;
      }
    }
    return false;
  }

  function labelTextFor(id) {
    if (!id) return "";
    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
    return label ? label.innerText.trim().slice(0, 150) : "";
  }

  // Best-effort for forms (plain table/paragraph layouts) that never link
  // a label to its field by id at all - same fallback
  // email_pipeline/llm_form_filler.py's _label_for() uses server-side.
  function nearbyTextFor(field) {
    const ancestor = field.closest("p, label, div");
    return ancestor ? ancestor.innerText.trim().slice(0, 150) : "";
  }

  return { isVisible, hasCaptcha, dismissCookieBanner, labelTextFor, nearbyTextFor };
}

function enumerateFormFieldsOnPage() {
  const { dismissCookieBanner, labelTextFor, nearbyTextFor } = _ingwerFormHelpers();

  function findBestForm() {
    let best = null;
    let bestScore = -1;
    for (const form of document.querySelectorAll("form")) {
      const inputs = form.querySelectorAll('input[type="email"], input:not([type]), input[type="text"], textarea');
      const score = inputs.length;
      if (score > bestScore) {
        best = form;
        bestScore = score;
      }
    }
    return best;
  }

  function enumerateFields(form) {
    const fields = [];
    const seenRadioGroups = new Set();
    for (const el of form.querySelectorAll("input, textarea, select")) {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (["submit", "button", "hidden", "image", "reset"].includes(type)) continue;

      const name = el.getAttribute("name") || "";
      const id = el.getAttribute("id") || "";
      const placeholder = el.getAttribute("placeholder") || "";
      const required = el.hasAttribute("required") || (el.getAttribute("aria-required") || "").toLowerCase() === "true";
      const label = labelTextFor(id) || nearbyTextFor(el);

      if (type === "radio") {
        if (!name || seenRadioGroups.has(name)) continue;
        seenRadioGroups.add(name);
        const options = Array.from(form.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)).map((opt) => {
          const optId = opt.getAttribute("id") || "";
          return { value: opt.getAttribute("value") || "", label: (optId && labelTextFor(optId)) || nearbyTextFor(opt) };
        });
        fields.push({ index: fields.length, tag: "radio-group", name, label, required, options });
        continue;
      }
      if (type === "checkbox") {
        fields.push({ index: fields.length, tag: "checkbox", name, label, required, options: null });
        continue;
      }
      if (tag === "select") {
        const options = Array.from(el.querySelectorAll("option")).map((o) => ({ value: o.getAttribute("value") || "", label: o.innerText.trim() }));
        fields.push({ index: fields.length, tag: "select", name, label, required, options });
        continue;
      }
      fields.push({ index: fields.length, tag: type, name, id, placeholder, label, required, options: null });
    }
    return fields;
  }

  dismissCookieBanner();
  const form = findBestForm();
  if (!form) return { ok: false, error: "no contact form found on this page" };
  const fields = enumerateFields(form);
  if (fields.length === 0) return { ok: false, error: "form found but it has no fillable fields" };
  return { ok: true, fields };
}

function applyFieldActionsOnPage(actions) {
  const { hasCaptcha, labelTextFor, nearbyTextFor } = _ingwerFormHelpers();

  function findBestForm() {
    let best = null;
    let bestScore = -1;
    for (const form of document.querySelectorAll("form")) {
      const inputs = form.querySelectorAll('input[type="email"], input:not([type]), input[type="text"], textarea');
      const score = inputs.length;
      if (score > bestScore) {
        best = form;
        bestScore = score;
      }
    }
    return best;
  }

  // Same enumeration as enumerateFormFieldsOnPage, but keeping the live
  // element (not serializing it away) - the two must stay in lockstep
  // (same fields, same order) or an action meant for one field would land
  // on another.
  function enumerateFieldsWithElements(form) {
    const fields = [];
    const seenRadioGroups = new Set();
    for (const el of form.querySelectorAll("input, textarea, select")) {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (["submit", "button", "hidden", "image", "reset"].includes(type)) continue;
      const name = el.getAttribute("name") || "";
      const id = el.getAttribute("id") || "";
      const placeholder = el.getAttribute("placeholder") || "";
      const label = labelTextFor(id) || nearbyTextFor(el);

      if (type === "radio") {
        if (!name || seenRadioGroups.has(name)) continue;
        seenRadioGroups.add(name);
        const radios = Array.from(form.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`));
        fields.push({ index: fields.length, tag: "radio-group", name, label, radios });
        continue;
      }
      if (type === "checkbox") {
        fields.push({ index: fields.length, tag: "checkbox", name, label, el });
        continue;
      }
      if (tag === "select") {
        fields.push({ index: fields.length, tag: "select", name, label, el });
        continue;
      }
      fields.push({ index: fields.length, tag: type, name, id, placeholder, label, el });
    }
    return fields;
  }

  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  const form = findBestForm();
  if (!form) return { ok: false, error: "no contact form found on this page" };
  const fields = enumerateFieldsWithElements(form);

  let filledCount = 0;
  const unmatched = [];
  for (const field of fields) {
    const actionEntry = actions[field.index] || actions[String(field.index)] || { action: "skip" };
    const action = actionEntry.action || "skip";
    const value = actionEntry.value || "";
    try {
      if (action === "fill" && value && field.el && ["text", "email", "tel", "textarea", "search", "url"].includes(field.tag)) {
        setNativeValue(field.el, value);
        field.el.style.outline = "2px solid #3d6b4f";
        filledCount += 1;
        continue;
      }
      if (action === "select" && value && field.tag === "select") {
        field.el.value = value;
        field.el.dispatchEvent(new Event("change", { bubbles: true }));
        field.el.style.outline = "2px solid #3d6b4f";
        filledCount += 1;
        continue;
      }
      if (action === "check" && field.tag === "checkbox" && field.el) {
        field.el.checked = true;
        field.el.dispatchEvent(new Event("change", { bubbles: true }));
        filledCount += 1;
        continue;
      }
      if (action === "check" && value && field.tag === "radio-group") {
        const match = field.radios.find((r) => r.getAttribute("value") === value);
        if (match) {
          match.checked = true;
          match.dispatchEvent(new Event("change", { bubbles: true }));
          filledCount += 1;
          continue;
        }
      }
    } catch (e) {
      // falls through to "unmatched" below, same as a classification miss
    }
    unmatched.push({ name: field.name, id: field.id || "", placeholder: field.placeholder || "", label: field.label, type: field.tag });
  }

  if (filledCount === 0) return { ok: false, error: "form found but no fields could be filled" };
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  return { ok: true, filledCount, unmatchedFields: unmatched, captchaDetected: hasCaptcha() };
}

/**
 * Plain keyword matcher - ported from email_pipeline/form_crawler.py's
 * classify_field/find_best_form/fill_form. Used as the fallback when no
 * DeepSeek key is configured server-side, or the classify-fields call
 * fails - see popup.js's fillCurrentTab().
 */
function fillContactFormOnPage(fields) {
  const FIELD_KEYWORDS = {
    email: ["email", "mail", "e-mail"],
    name: ["name", "vorname", "firstname"],
    phone: ["phone", "telefon", "mobile", "handy", "fax"],
    address: ["address", "adresse", "strasse", "straße", "anschrift", "plz", "postleitzahl", "postal"],
    subject: ["subject", "betreff", "topic", "reason"],
    message: ["message", "nachricht", "comment", "kommentar", "anliegen", "inquiry", "body", "frage"],
  };

  const COOKIE_ACCEPT_TEXTS = [
    "alle akzeptieren", "akzeptieren", "alle cookies akzeptieren", "zustimmen",
    "accept all", "accept all cookies", "i accept", "accept",
    "alles akzeptieren", "ok", "got it", "verstanden",
  ];

  // a custom text-in-image CAPTCHA (common on German pharmacy/medical
  // sites) isn't Google reCAPTCHA or hCaptcha, so it has no dedicated
  // widget to detect -- catch it via the image or its instruction text.
  const CAPTCHA_KEYWORDS = [
    "captcha", "sicherheitscode", "sicherheitsabfrage", "zeichenfolge",
    "prüfcode", "pruefcode", "tragen sie", "rechenaufgabe", "spamschutz",
    "code eingeben", "schutzcode",
  ];

  function hasCaptcha(form) {
    const images = Array.from(document.querySelectorAll("img"));
    if (images.some((img) => {
      const src = (img.getAttribute("src") || "").toLowerCase();
      const alt = (img.getAttribute("alt") || "").toLowerCase();
      return isVisible(img) && (src.includes("captcha") || alt.includes("captcha"));
    })) {
      return true;
    }
    const text = document.body.innerText.toLowerCase();
    return CAPTCHA_KEYWORDS.some((kw) => text.includes(kw));
  }

  function innermostName(name) {
    // many sites namespace every field under one array-style wrapper, e.g.
    // name="mail[name]" -- the wrapper ("mail") isn't the field's own
    // identity and would make every field in the form look like whatever
    // category the wrapper word happens to match. Classify by the
    // innermost bracket content instead.
    const brackets = [...name.matchAll(/\[([^\[\]]+)\]/g)];
    return brackets.length > 0 ? brackets[brackets.length - 1][1] : name;
  }

  function classify(name, id, placeholder, label) {
    const signal = [innermostName(name), id, placeholder, label].filter(Boolean).join(" ").toLowerCase();
    for (const [category, keywords] of Object.entries(FIELD_KEYWORDS)) {
      if (keywords.some((kw) => signal.includes(kw))) return category;
    }
    return null;
  }

  function labelTextFor(id) {
    if (!id) return "";
    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
    return label ? label.innerText : "";
  }

  function dismissCookieBanner() {
    const buttons = Array.from(document.querySelectorAll("button, a"));
    for (const text of COOKIE_ACCEPT_TEXTS) {
      const match = buttons.find(
        (b) => b.innerText && b.innerText.trim().toLowerCase() === text && isVisible(b)
      );
      if (match) {
        match.click();
        return true;
      }
    }
    return false;
  }

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.visibility !== "hidden" &&
      style.display !== "none"
    );
  }

  function classifyFieldsIn(form) {
    const matches = [];
    const unmatched = [];
    const inputs = form.querySelectorAll("input, textarea, select");
    for (const field of inputs) {
      const type = (field.getAttribute("type") || "text").toLowerCase();
      if (["submit", "button", "hidden"].includes(type)) continue;
      const name = field.getAttribute("name") || "";
      const id = field.getAttribute("id") || "";
      const placeholder = field.getAttribute("placeholder") || "";
      const label = labelTextFor(id);

      if (["checkbox", "radio"].includes(type)) {
        unmatched.push({ name, id, placeholder, label, type });
        continue;
      }

      if (field.tagName === "SELECT") {
        // a dropdown needs the right option picked, not text typed in --
        // out of scope for keyword-based filling, report it instead of
        // silently attempting (and failing) to set a value
        unmatched.push({ name, id, placeholder, label, type: "select" });
        continue;
      }

      const category = classify(name, id, placeholder, label);
      if (category) {
        matches.push({ field, category });
      } else {
        unmatched.push({ name, id, placeholder, label, type });
      }
    }
    return { matches, unmatched };
  }

  function findBestForm() {
    let best = null;
    let bestMatches = [];
    let bestUnmatched = [];
    for (const form of document.querySelectorAll("form")) {
      const { matches, unmatched } = classifyFieldsIn(form);
      const categories = new Set(matches.map((m) => m.category));
      if (!categories.has("email") && !categories.has("message")) continue;
      if (matches.length > bestMatches.length) {
        best = form;
        bestMatches = matches;
        bestUnmatched = unmatched;
      }
    }
    return { form: best, matches: bestMatches, unmatched: bestUnmatched };
  }

  // setting .value directly doesn't notify React/Vue-controlled inputs --
  // this goes through the native setter so their change listeners fire
  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  dismissCookieBanner();

  const { form, matches, unmatched } = findBestForm();
  if (!form) {
    return { ok: false, error: "no contact form found on this page" };
  }

  const values = {
    email: fields.email,
    name: fields.name,
    phone: fields.phone,
    address: fields.address,
    subject: fields.subject,
    message: fields.message,
  };

  let filledCount = 0;
  for (const { field, category } of matches) {
    const value = values[category];
    if (!value) continue;
    try {
      setNativeValue(field, value);
      field.style.outline = "2px solid #3d6b4f";
      filledCount += 1;
    } catch (e) {
      // skip this field, keep going
    }
  }

  if (filledCount === 0) {
    return { ok: false, error: "form found but no matching fields could be filled" };
  }

  form.scrollIntoView({ behavior: "smooth", block: "center" });
  return { ok: true, filledCount, unmatchedFields: unmatched, captchaDetected: hasCaptcha(form) };
}
