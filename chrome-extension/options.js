function setStatus(text, ok) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.style.color = ok ? "#3d6b4f" : "#a4423a";
}

function normalizeBaseUrl(url) {
  return url.trim().replace(/\/+$/, "");
}

chrome.storage.sync.get(["baseUrl", "apiKey"], (data) => {
  if (data.baseUrl) document.getElementById("baseUrl").value = data.baseUrl;
  if (data.apiKey) document.getElementById("apiKey").value = data.apiKey;
});

document.getElementById("save").addEventListener("click", () => {
  const baseUrl = normalizeBaseUrl(document.getElementById("baseUrl").value);
  const apiKey = document.getElementById("apiKey").value.trim();
  chrome.storage.sync.set({ baseUrl, apiKey }, () => {
    setStatus("Saved.", true);
  });
});

document.getElementById("test").addEventListener("click", async () => {
  const baseUrl = normalizeBaseUrl(document.getElementById("baseUrl").value);
  const apiKey = document.getElementById("apiKey").value.trim();
  setStatus("Testing...", true);
  try {
    const res = await fetch(`${baseUrl}/api/forms/pending?limit=1`, {
      headers: { "X-API-Key": apiKey },
    });
    if (res.status === 401) {
      setStatus("Connected, but API key is wrong.", false);
    } else if (res.ok) {
      setStatus("Connected successfully.", true);
    } else {
      setStatus(`Unexpected response: ${res.status}`, false);
    }
  } catch (err) {
    setStatus(`Could not reach dashboard: ${err.message}`, false);
  }
});
