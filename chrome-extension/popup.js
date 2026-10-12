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

async function main() {
  const { baseUrl, apiKey } = await chrome.storage.sync.get(["baseUrl", "apiKey"]);
  if (!baseUrl || !apiKey) {
    render(`<p class="muted">Not set up yet.</p><button id="openOptions">Open settings</button>`);
    document.getElementById("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
    return;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !tab.url.startsWith("http")) {
    render(`<p class="muted">Open a shop's contact page first.</p>`);
    return;
  }

  render(`<p class="muted">Looking up ${new URL(tab.url).hostname}...</p>`);

  let matches;
  try {
    matches = await apiFetch(baseUrl, apiKey, `/api/forms/by_url?url=${encodeURIComponent(tab.url)}`);
  } catch (err) {
    render(`<p class="muted">Could not reach dashboard.</p>`);
    setStatus(err.message, false);
    return;
  }

  const pending = matches.find((m) => m.status === "pending") || matches.find((m) => m.status !== "submitted");

  if (!pending) {
    render(`
      <p class="muted">No target on file for this site yet.</p>
      <label>Company / shop name (optional)</label>
      <input id="companyName">
      <button id="addTarget">Add this page</button>
    `);
    document.getElementById("addTarget").addEventListener("click", async () => {
      try {
        const company = document.getElementById("companyName").value;
        const added = await apiFetch(baseUrl, apiKey, "/api/forms", {
          method: "POST",
          body: JSON.stringify({ url: tab.url, company }),
        });
        showTarget(baseUrl, apiKey, tab, { id: added.id, url: tab.url, company });
      } catch (err) {
        setStatus(err.message, false);
      }
    });
    return;
  }

  showTarget(baseUrl, apiKey, tab, pending);
}

function showTarget(baseUrl, apiKey, tab, target) {
  render(`
    <div class="card">
      <strong>${target.company || target.name || target.url}</strong>
      <div class="muted">${target.url}</div>
    </div>
    <button id="generate">Generate text</button>
    <div id="preview"></div>
  `);

  document.getElementById("generate").addEventListener("click", async () => {
    setStatus("Generating...", true);
    try {
      const gen = await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/generate`, { method: "POST" });
      showPreview(baseUrl, apiKey, tab, target, gen);
      setStatus("", true);
    } catch (err) {
      setStatus(err.message, false);
    }
  });
}

function showPreview(baseUrl, apiKey, tab, target, gen) {
  document.getElementById("preview").innerHTML = `
    <label>Sender name</label>
    <input id="f_name" value="${gen.sender_name || ""}">
    <label>Sender email</label>
    <input id="f_email" value="${gen.sender_email || ""}">
    <label>Subject</label>
    <input id="f_subject" value="${gen.subject.replace(/"/g, "&quot;")}">
    <label>Message</label>
    <textarea id="f_message">${gen.body}</textarea>
    <button id="fillBtn">Fill form on page</button>
    <button id="submittedBtn" class="secondary" disabled>Mark submitted</button>
    <button id="skipBtn" class="secondary">Skip</button>
  `;

  document.getElementById("fillBtn").addEventListener("click", async () => {
    setStatus("Filling...", true);
    try {
      const [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["fill-logic.js"],
      });
      const [{ result: fillResult }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (fields) => fillContactFormOnPage(fields),
        args: [{
          name: document.getElementById("f_name").value,
          email: document.getElementById("f_email").value,
          subject: document.getElementById("f_subject").value,
          message: document.getElementById("f_message").value,
        }],
      });
      if (fillResult.ok) {
        setStatus(`Filled ${fillResult.filledCount} field(s) -- review on the page, then submit it yourself.`, true);
        document.getElementById("submittedBtn").disabled = false;
      } else {
        setStatus(fillResult.error, false);
      }
    } catch (err) {
      setStatus(err.message, false);
    }
  });

  document.getElementById("submittedBtn").addEventListener("click", async () => {
    try {
      await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/submitted`, { method: "POST" });
      setStatus("Marked submitted.", true);
      render(`<p class="muted">Done -- close this popup or move to the next site.</p>`);
    } catch (err) {
      setStatus(err.message, false);
    }
  });

  document.getElementById("skipBtn").addEventListener("click", async () => {
    try {
      await apiFetch(baseUrl, apiKey, `/api/forms/${target.id}/skip`, {
        method: "POST",
        body: JSON.stringify({ reason: "skipped from extension" }),
      });
      setStatus("Skipped.", true);
      render(`<p class="muted">Skipped.</p>`);
    } catch (err) {
      setStatus(err.message, false);
    }
  });
}

main();
