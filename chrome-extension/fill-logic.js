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
    subject: ["subject", "betreff", "topic", "reason"],
    message: ["message", "nachricht", "comment", "anliegen", "inquiry", "body", "frage"],
  };

  const COOKIE_ACCEPT_TEXTS = [
    "alle akzeptieren", "akzeptieren", "alle cookies akzeptieren", "zustimmen",
    "accept all", "accept all cookies", "i accept", "accept",
    "alles akzeptieren", "ok", "got it", "verstanden",
  ];

  function classify(name, id, placeholder, label) {
    const signal = [name, id, placeholder, label].filter(Boolean).join(" ").toLowerCase();
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
    const inputs = form.querySelectorAll("input, textarea, select");
    for (const field of inputs) {
      const type = (field.getAttribute("type") || "text").toLowerCase();
      if (["submit", "button", "hidden", "checkbox", "radio"].includes(type)) continue;
      const category = classify(
        field.getAttribute("name") || "",
        field.getAttribute("id") || "",
        field.getAttribute("placeholder") || "",
        labelTextFor(field.getAttribute("id"))
      );
      if (category) matches.push({ field, category });
    }
    return matches;
  }

  function findBestForm() {
    let best = null;
    let bestMatches = [];
    for (const form of document.querySelectorAll("form")) {
      const matches = classifyFieldsIn(form);
      const categories = new Set(matches.map((m) => m.category));
      if (!categories.has("email") && !categories.has("message")) continue;
      if (matches.length > bestMatches.length) {
        best = form;
        bestMatches = matches;
      }
    }
    return { form: best, matches: bestMatches };
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

  const { form, matches } = findBestForm();
  if (!form) {
    return { ok: false, error: "no contact form found on this page" };
  }

  const values = {
    email: fields.email,
    name: fields.name,
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
  return { ok: true, filledCount };
}
