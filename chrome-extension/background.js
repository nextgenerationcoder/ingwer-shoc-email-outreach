/**
 * MV3 service worker - owns batch mode's navigate -> generate -> fill
 * sequence, which used to run inside popup.js itself.
 *
 * The bug that motivated this file: a Chrome extension POPUP is destroyed
 * the instant it loses focus - including, often, the very tab navigation
 * this sequence triggers (chrome.tabs.update can shift focus away from
 * the popup). Whatever async chain was running in popup.js's own context
 * dies with it, silently, mid-step, with no error shown anywhere - which
 * is exactly why batch mode could "open a page but fill in nothing": the
 * fill step was never reached before the popup (and its JS) was gone.
 *
 * A service worker is NOT tied to the popup's lifetime, so it keeps
 * running regardless of whether anyone has the popup open - progress is
 * written to chrome.storage.session after every step, which IS what
 * survives (and what a reopened popup reads to show where things stand).
 *
 * popup.js only ever: (1) asks this worker to start/retry/advance/stop a
 * batch via chrome.runtime.sendMessage, (2) reads the current state back
 * out of chrome.storage.session to render. It never runs the navigate/
 * generate/fill sequence itself for batch mode anymore (single-page mode
 * still does its own fill directly from popup.js - that one single click
 * is quick and under the user's own attention, so it doesn't have this
 * problem).
 */

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

// Same two-step "enumerate on the page, classify on the backend, apply on
// the page" flow popup.js's single-page mode uses - see fill-logic.js's
// own top-of-file comment for why this needs two separate
// chrome.scripting.executeScript calls (live DOM references can't cross
// the extension/page boundary, only plain JSON can).
async function fillTab(tabId, fields, baseUrl, apiKey) {
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
    // local keyword matcher rather than leaving the item stuck
  }

  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (f) => fillContactFormOnPage(f),
    args: [fields],
  });
  return result;
}

async function getBatch() {
  const { batch } = await chrome.storage.session.get(["batch"]);
  return batch || null;
}

async function setBatch(patch) {
  const current = await getBatch();
  const next = { ...current, ...patch };
  await chrome.storage.session.set({ batch: next });
  return next;
}

// Runs the whole navigate -> generate -> fill sequence for whatever item
// `batch.index` currently points at, writing progress to storage after
// every step (not just at the end) so a popup that's open WHILE this runs
// can show live status, and a popup that was closed and reopens mid-way
// sees exactly how far it got.
async function processCurrentItem() {
  const batch = await getBatch();
  if (!batch) return;
  const target = batch.queue[batch.index];

  await setBatch({ gen: null, fillResult: null, stage: "navigating" });
  try {
    await chrome.tabs.update(batch.tabId, { url: target.url });
    await waitForTabComplete(batch.tabId);
  } catch (err) {
    await setBatch({ stage: "error", fillResult: { ok: false, error: `Could not open the page: ${err.message}` } });
    return;
  }

  await setBatch({ stage: "generating" });
  let gen;
  try {
    gen = await apiFetch(batch.baseUrl, batch.apiKey, `/api/forms/${target.id}/generate`, { method: "POST" });
  } catch (err) {
    await setBatch({ stage: "error", fillResult: { ok: false, error: err.message } });
    return;
  }
  await setBatch({ gen, stage: "filling" });

  let fillResult;
  try {
    fillResult = await fillTab(
      batch.tabId,
      { name: gen.sender_name, email: gen.sender_email, phone: gen.sender_phone, address: gen.sender_address, subject: gen.subject, message: gen.body },
      batch.baseUrl,
      batch.apiKey
    );
  } catch (err) {
    fillResult = { ok: false, error: err.message };
  }
  await setBatch({ fillResult, stage: "done" });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      if (message.type === "startBatch") {
        const queue = await apiFetch(message.baseUrl, message.apiKey, "/api/forms/pending?limit=50");
        if (queue.length === 0) {
          sendResponse({ ok: false, error: "No pending targets." });
          return;
        }
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        await chrome.storage.session.set({
          batch: { queue, index: 0, tabId: tab.id, baseUrl: message.baseUrl, apiKey: message.apiKey, gen: null, fillResult: null, stage: "starting" },
        });
        processCurrentItem(); // deliberately not awaited - the caller polls storage for progress, same as any later reopen of the popup would
        sendResponse({ ok: true });
        return;
      }

      if (message.type === "retryCurrentItem") {
        const batch = await getBatch();
        if (!batch) {
          sendResponse({ ok: false, error: "No batch in progress." });
          return;
        }
        processCurrentItem();
        sendResponse({ ok: true });
        return;
      }

      if (message.type === "advanceBatch") {
        const batch = await getBatch();
        if (!batch) {
          sendResponse({ ok: false, error: "No batch in progress." });
          return;
        }
        const target = batch.queue[batch.index];
        if (message.outcome === "submitted") {
          await apiFetch(batch.baseUrl, batch.apiKey, `/api/forms/${target.id}/submitted`, { method: "POST" });
        } else {
          await apiFetch(batch.baseUrl, batch.apiKey, `/api/forms/${target.id}/skip`, {
            method: "POST",
            body: JSON.stringify({ reason: "skipped from extension" }),
          });
        }
        const nextIndex = batch.index + 1;
        if (nextIndex >= batch.queue.length) {
          await chrome.storage.session.remove("batch");
          sendResponse({ ok: true, done: true });
          return;
        }
        await chrome.storage.session.set({
          batch: { ...batch, index: nextIndex, gen: null, fillResult: null, stage: "starting" },
        });
        processCurrentItem();
        sendResponse({ ok: true });
        return;
      }

      if (message.type === "stopBatch") {
        await chrome.storage.session.remove("batch");
        sendResponse({ ok: true });
        return;
      }

      sendResponse({ ok: false, error: `Unknown message type: ${message.type}` });
    } catch (err) {
      sendResponse({ ok: false, error: err.message });
    }
  })();
  return true; // keep the message channel open for the async sendResponse above
});
