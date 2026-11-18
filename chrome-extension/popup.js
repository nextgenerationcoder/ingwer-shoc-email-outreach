function setStatus(text, ok) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.style.color = ok ? "#3d6b4f" : "#a4423a";
}

async function apiFetch(baseUrl, apiKey, path, options = {}) {
  const res = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: { "X-API-Key": apiKey, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

function render(html) {
  document.getElementById("content").innerHTML = html;
}

// Tries the same LLM-based field understanding the headless crawler has
// (POST /api/forms/classify-fields -> email_pipeline/llm_form_filler.py) -
// handles selects, radio groups, split first/last-name fields, a required
// routing dropdown, none of which the old keyword matcher below ever
// touched. Falls back to that plain keyword matcher if there's no
// contact form to enumerate, the backend call fails, or it comes back
// with no actions (e.g. no DeepSeek key configured server-side) - the
// extension never just stops working without one.
async function fillCurrentTab(tabId, fields, baseUrl, apiKey) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["fill-logic.js"] });

  try {
    const [{ result: enumerated }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => enumerateFormFieldsOnPage(),
    });
    if (enumerated.ok) {
      const classified = await apiFetch(baseUrl, apiKey, "/api/forms/classify-fields", {
        method: "POST",
        body: JSON.stringify({
          fields: enumerated.fields,
          sender: { full_name: fields.name, email: fields.email, phone: fields.phone, address: fields.address, subject: fields.subject, message: fields.message },
        }),
      });
      if (classified.actions) {
        const [{ result }] = await chrome.scripting.executeScript({
          target: { tabId },
          func: (actions) => applyFieldActionsOnPage(actions),
          args: [classified.actions],
        });
        return result;
      }
    }
  } catch (e) {
    // network error, no API key, backend down, etc. - fall through to the
    // local keyword matcher rather than leaving the user with nothing
  }

  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (f) => fillContactFormOnPage(f),
    args: [fields],
  });
  return result;
}

async function main() {
  const { baseUrl, apiKey } = await chrome.storage.sync.get(["baseUrl", "apiKey"]);
  if (!baseUrl || !apiKey) {
    render(`<p class="muted">Not set up yet.</p><button id="openOptions">Open settings</button>`);
    document.getElementById("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
    return;
  }

  const session = await chrome.storage.session.get(["batch"]);
  if (session.batch) {
    renderBatchItem(baseUrl, apiKey);
    return;
  }

  renderSinglePageMode(baseUrl, apiKey);
}

// ---- single-page mode: fill whatever contact page the current tab is on ----

async function renderSinglePageMode(baseUrl, apiKey) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  render(`
    <button id="startBatch">Start batch (go through pending list)</button>
    <hr style="margin:0.8rem 0;border:none;border-top:1px solid #ece8b0">
    <div id="singlePage"><p class="muted">Loading current page...</p></div>
  `);
  document.getElementById("startBatch").addEventListener("click", () => startBatch(baseUrl, apiKey));

  if (!tab || !tab.url || !tab.url.startsWith("http")) {
    document.getElementById("singlePage").innerHTML = `<p class="muted">Open a shop's contact page to fill it individually.</p>`;
    return;
  }

  let matches;
  try {
    matches = await apiFetch(baseUrl, apiKey, `/api/forms/by_url?url=${encodeURIComponent(tab.url)}`);
  } catch (err) {
    document.getElementById("singlePage").innerHTML = `<p class="muted">Could not reach dashboard.</p>`;
    setStatus(err.message, false);
    return;
  }

  const pending = matches.find((m) => m.status === "pending") || matches.find((m) => m.status !== "submitted");
  if (!pending) {
    document.getElementById("singlePage").innerHTML = `
      <p class="muted">No target on file for this site yet.</p>
      <label>Company / shop name (optional)</label>
      <input id="companyName">
      <button id="addTarget">Add this page</button>
    `;
    document.getElementById("addTarget").addEventListener("click", async () => {
      try {
        const company = document.getElementById("companyName").value;
        const added = await apiFetch(baseUrl, apiKey, "/api/forms", {
          method: "POST",
          body: JSON.stringify({ url: tab.url, company }),
        });
        showSingleTarget(baseUrl, apiKey, tab.id, { id: added.id, url: tab.url, company });
      } catch (err) {
        setStatus(err.message, false);
      }
    });
    return;
  }

  showSingleTarget(baseUrl, apiKey, tab.id, pending);
}

function showSingleTarget(baseUrl, apiKey, tabId, target) {
  document.getElementById("singlePage").innerHTML = `
    <div class="card"><strong>${target.company || target.name || target.url}</strong><div class="muted">${target.url}</div></div>
    <button id="generate">Generate text</button>
    <div id="preview"></div>
  `;
  document.getElementById("generate").addEventListener("click", async () => {
    setStatus("Generating...", true);
    try {
      const gen = await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/generate`, { method: "POST" });
      renderPreviewForm(document.getElementById("preview"), gen, {
        onFill: async (fields) => {
          const result = await fillCurrentTab(tabId, fields, baseUrl, apiKey);
          return result;
        },
        onSubmitted: async () => {
          await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/submitted`, { method: "POST" });
          render(`<p class="muted">Marked submitted.</p>`);
        },
        onSkip: async () => {
          await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/skip`, {
            method: "POST",
            body: JSON.stringify({ reason: "skipped from extension" }),
          });
          render(`<p class="muted">Skipped.</p>`);
        },
      });
      setStatus("", true);
    } catch (err) {
      setStatus(err.message, false);
    }
  });
}

// ---- batch mode: walk the pending list ----
//
// The actual navigate -> generate -> fill work happens in background.js
// (a service worker, NOT tied to this popup's lifetime - see its own
// top-of-file comment for why that matters: this popup gets destroyed the
// instant it loses focus, which killed the old in-popup version of this
// sequence silently mid-step). This popup only ever sends it a message
// and polls chrome.storage.session to show progress - closing and
// reopening the popup mid-batch is now harmless, since the worker keeps
// going either way and storage always has the latest state.

const STAGE_LABELS = {
  starting: "Starting…",
  navigating: "Opening the page…",
  generating: "Generating text with AI…",
  filling: "Filling the form…",
};

let batchPollTimer = null;

async function startBatch(baseUrl, apiKey) {
  setStatus("Starting batch…", true);
  const response = await chrome.runtime.sendMessage({ type: "startBatch", baseUrl, apiKey });
  if (!response.ok) {
    setStatus(response.error, false);
    return;
  }
  setStatus("", true);
  renderBatchItem(baseUrl, apiKey);
}

async function renderBatchItem(baseUrl, apiKey) {
  clearTimeout(batchPollTimer);
  const { batch } = await chrome.storage.session.get(["batch"]);
  if (!batch) {
    render(`<p class="muted">Batch complete or stopped.</p>`);
    return;
  }

  const target = batch.queue[batch.index];
  const working = batch.stage && batch.stage !== "done" && batch.stage !== "error";

  render(`
    <div class="card">
      <strong>${target.company || target.url}</strong>
      <div class="muted">${batch.index + 1} of ${batch.queue.length}</div>
    </div>
    <div id="fillStatus" class="muted">${working ? STAGE_LABELS[batch.stage] || "Working…" : ""}</div>
    <div id="preview"></div>
  `);

  if (working) {
    // Still running in background.js - poll until it settles rather than
    // assuming any fixed duration (the AI calls it makes vary in speed).
    batchPollTimer = setTimeout(() => renderBatchItem(baseUrl, apiKey), 800);
    document.getElementById("preview").innerHTML = `<button id="stopBtn" class="secondary">Stop batch</button>`;
    document.getElementById("stopBtn").addEventListener("click", stopBatch);
    return;
  }

  const fillStatusEl = document.getElementById("fillStatus");
  if (batch.fillResult) {
    fillStatusEl.textContent = batch.fillResult.ok
      ? `Filled ${batch.fillResult.filledCount} field(s) -- solve any CAPTCHA and click Submit on the page, then come back here.`
      : `Could not fill: ${batch.fillResult.error}`;
  }

  const previewEl = document.getElementById("preview");
  previewEl.innerHTML = batch.gen
    ? `
      <label>Subject</label>
      <input id="f_subject" value="${(batch.gen.subject || "").replace(/"/g, "&quot;")}" readonly>
      <label>Message</label>
      <textarea id="f_message" readonly>${batch.gen.body || ""}</textarea>
      <button id="retryBtn" class="secondary">Fill again</button>
      <button id="nextBtn">Submitted -- next</button>
      <button id="skipBtn" class="secondary">Skip -- next</button>
      <button id="stopBtn" class="secondary">Stop batch</button>
    `
    : `
      <button id="nextBtn">Submitted -- next</button>
      <button id="skipBtn" class="secondary">Skip -- next</button>
      <button id="stopBtn" class="secondary">Stop batch</button>
    `;

  const retryBtn = document.getElementById("retryBtn");
  if (retryBtn) {
    retryBtn.addEventListener("click", async () => {
      setStatus("Retrying…", true);
      await chrome.runtime.sendMessage({ type: "retryCurrentItem" });
      renderBatchItem(baseUrl, apiKey);
    });
  }
  document.getElementById("nextBtn").addEventListener("click", () => advanceBatch(baseUrl, apiKey, "submitted"));
  document.getElementById("skipBtn").addEventListener("click", () => advanceBatch(baseUrl, apiKey, "skip"));
  document.getElementById("stopBtn").addEventListener("click", stopBatch);
}

async function advanceBatch(baseUrl, apiKey, outcome) {
  setStatus("Saving…", true);
  const response = await chrome.runtime.sendMessage({ type: "advanceBatch", outcome });
  if (!response.ok) {
    setStatus(response.error, false);
    return;
  }
  setStatus("", true);
  if (response.done) {
    render(`<p class="muted">Batch complete.</p>`);
    return;
  }
  renderBatchItem(baseUrl, apiKey);
}

async function stopBatch() {
  clearTimeout(batchPollTimer);
  await chrome.runtime.sendMessage({ type: "stopBatch" });
  render(`<p class="muted">Batch stopped.</p>`);
}

// ---- shared preview/edit form used by both modes ----

function renderPreviewForm(container, gen, { onFill, onSubmitted, onSkip }) {
  container.innerHTML = `
    <label>Sender name</label>
    <input id="f_name" value="${gen.sender_name || ""}">
    <label>Sender email</label>
    <input id="f_email" value="${gen.sender_email || ""}">
    <label>Sender phone (optional)</label>
    <input id="f_phone" value="${gen.sender_phone || ""}">
    <label>Sender address (optional)</label>
    <input id="f_address" value="${gen.sender_address || ""}">
    <label>Subject</label>
    <input id="f_subject" value="${(gen.subject || "").replace(/"/g, "&quot;")}">
    <label>Message</label>
    <textarea id="f_message">${gen.body || ""}</textarea>
    <button id="fillBtn">Fill form on page</button>
    <button id="submittedBtn" class="secondary" disabled>Submitted -- next</button>
    <button id="skipBtn" class="secondary">Skip -- next</button>
    <div id="unmatchedNote" class="muted" style="margin-top:0.5rem;font-size:0.78rem"></div>
  `;

  container.querySelector("#fillBtn").addEventListener("click", async () => {
    setStatus("Filling...", true);
    try {
      const result = await onFill({
        name: document.getElementById("f_name").value,
        email: document.getElementById("f_email").value,
        phone: document.getElementById("f_phone").value,
        address: document.getElementById("f_address").value,
        subject: document.getElementById("f_subject").value,
        message: document.getElementById("f_message").value,
      });
      if (result.ok) {
        setStatus(`Filled ${result.filledCount} field(s) -- review, solve any CAPTCHA, submit yourself.`, true);
        container.querySelector("#submittedBtn").disabled = false;
        const unmatched = result.unmatchedFields || [];
        const noteEl = container.querySelector("#unmatchedNote");
        const notes = [];
        if (result.captchaDetected) {
          notes.push("CAPTCHA detected on this page -- solve it by hand before submitting; some browsers also autofill other fields with saved data after this script runs, so double-check every field before you click submit.");
        }
        if (unmatched.length > 0) {
          const names = unmatched.map((f) => f.label || f.placeholder || f.name || f.id || "?");
          notes.push(`${unmatched.length} field(s) not filled: ${names.join(", ")} -- tell Claude about these if you want support added.`);
        }
        if (noteEl) noteEl.textContent = notes.join(" ");
      } else {
        setStatus(result.error, false);
      }
    } catch (err) {
      setStatus(err.message, false);
    }
  });

  container.querySelector("#submittedBtn").addEventListener("click", async () => {
    try {
      await onSubmitted();
    } catch (err) {
      setStatus(err.message, false);
    }
  });

  container.querySelector("#skipBtn").addEventListener("click", async () => {
    try {
      await onSkip();
    } catch (err) {
      setStatus(err.message, false);
    }
  });
}

main();
