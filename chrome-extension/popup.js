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

function waitForTabComplete(tabId, timeoutMs = 20000) {
  return new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => {
      if (!done) {
        done = true;
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }, timeoutMs);
    function listener(id, changeInfo) {
      if (id === tabId && changeInfo.status === "complete") {
        done = true;
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function fillCurrentTab(tabId, fields) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["fill-logic.js"] });
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
  if (session.batch && session.batch.queue.length > session.batch.index) {
    renderBatchItem(baseUrl, apiKey, session.batch);
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
          const result = await fillCurrentTab(tabId, fields);
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

// ---- batch mode: walk the pending list, navigating the tab for you ----

async function startBatch(baseUrl, apiKey) {
  setStatus("Loading pending list...", true);
  try {
    const queue = await apiFetch(baseUrl, apiKey, "/api/forms/pending?limit=50");
    if (queue.length === 0) {
      setStatus("No pending targets.", false);
      return;
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const batch = { queue, index: 0, tabId: tab.id };
    await chrome.storage.session.set({ batch });
    await goToBatchItem(baseUrl, apiKey, batch);
  } catch (err) {
    setStatus(err.message, false);
  }
}

async function goToBatchItem(baseUrl, apiKey, batch) {
  const target = batch.queue[batch.index];
  render(`<p class="muted">Opening ${target.company || target.url} (${batch.index + 1} of ${batch.queue.length})...</p>`);

  await chrome.tabs.update(batch.tabId, { url: target.url });
  await waitForTabComplete(batch.tabId);

  let gen;
  try {
    gen = await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/generate`, { method: "POST" });
  } catch (err) {
    setStatus(err.message, false);
    return;
  }

  let fillResult;
  try {
    fillResult = await fillCurrentTab(batch.tabId, {
      name: gen.sender_name,
      email: gen.sender_email,
      subject: gen.subject,
      message: gen.body,
    });
  } catch (err) {
    fillResult = { ok: false, error: err.message };
  }

  renderBatchItem(baseUrl, apiKey, batch, gen, fillResult);
}

function renderBatchItem(baseUrl, apiKey, batch, gen, fillResult) {
  const target = batch.queue[batch.index];
  render(`
    <div class="card">
      <strong>${target.company || target.url}</strong>
      <div class="muted">${batch.index + 1} of ${batch.queue.length}</div>
    </div>
    <div id="fillStatus" class="muted"></div>
    <div id="preview"></div>
  `);

  const fillStatusEl = document.getElementById("fillStatus");
  if (fillResult) {
    fillStatusEl.textContent = fillResult.ok
      ? `Filled ${fillResult.filledCount} field(s) -- solve any CAPTCHA and click Submit on the page, then come back here.`
      : `Could not fill: ${fillResult.error}`;
  }

  if (!gen) {
    // reopened popup mid-batch without a fresh generation -- just offer next/skip
    document.getElementById("preview").innerHTML = `
      <button id="nextBtn">Submitted -- next</button>
      <button id="skipBtn" class="secondary">Skip -- next</button>
      <button id="stopBtn" class="secondary">Stop batch</button>
    `;
  } else {
    renderPreviewForm(document.getElementById("preview"), gen, {
      showFillButton: true,
      onFill: (fields) => fillCurrentTab(batch.tabId, fields),
      onSubmitted: () => advanceBatch(baseUrl, apiKey, batch, "submitted"),
      onSkip: () => advanceBatch(baseUrl, apiKey, batch, "skip"),
    });
    const extra = document.createElement("div");
    extra.innerHTML = `<button id="stopBtn" class="secondary">Stop batch</button>`;
    document.getElementById("preview").appendChild(extra);
  }

  const stopBtn = document.getElementById("stopBtn");
  if (stopBtn) stopBtn.addEventListener("click", async () => {
    await chrome.storage.session.remove("batch");
    render(`<p class="muted">Batch stopped.</p>`);
  });

  const nextBtn = document.getElementById("nextBtn");
  if (nextBtn) nextBtn.addEventListener("click", () => advanceBatch(baseUrl, apiKey, batch, "submitted"));
  const skipBtn = document.getElementById("skipBtn");
  if (skipBtn) skipBtn.addEventListener("click", () => advanceBatch(baseUrl, apiKey, batch, "skip"));
}

async function advanceBatch(baseUrl, apiKey, batch, outcome) {
  const target = batch.queue[batch.index];
  try {
    if (outcome === "submitted") {
      await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/submitted`, { method: "POST" });
    } else {
      await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/skip`, {
        method: "POST",
        body: JSON.stringify({ reason: "skipped from extension" }),
      });
    }
  } catch (err) {
    setStatus(err.message, false);
    return;
  }

  batch.index += 1;
  await chrome.storage.session.set({ batch });

  if (batch.index >= batch.queue.length) {
    await chrome.storage.session.remove("batch");
    render(`<p class="muted">Batch complete -- ${batch.queue.length} target(s) processed.</p>`);
    return;
  }

  await goToBatchItem(baseUrl, apiKey, batch);
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
        subject: document.getElementById("f_subject").value,
        message: document.getElementById("f_message").value,
      });
      if (result.ok) {
        setStatus(`Filled ${result.filledCount} field(s) -- review, solve any CAPTCHA, submit yourself.`, true);
        container.querySelector("#submittedBtn").disabled = false;
        const unmatched = result.unmatchedFields || [];
        const noteEl = container.querySelector("#unmatchedNote");
        if (noteEl && unmatched.length > 0) {
          const names = unmatched.map((f) => f.label || f.placeholder || f.name || f.id || "?");
          noteEl.textContent = `${unmatched.length} field(s) not filled: ${names.join(", ")} -- tell Claude about these if you want support added.`;
        }
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
