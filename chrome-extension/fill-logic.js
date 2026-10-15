/**
 * Ported from email_pipeline/form_crawler.py's classify_field/find_best_form/
 * fill_form -- same keyword-based field detection, so a form gets filled
 * the same way whether the headless crawler or this extension does it.
 *
 * Self-contained on purpose: this is injected into the page via
 * chrome.scripting.executeScript, which requires the function (and
 * anything it calls) to have no outside closure references.
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
