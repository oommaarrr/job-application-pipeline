/*
 * Service worker — the only part of the extension that talks to the pipeline.
 *
 * Content scripts run inside the page and their fetches can be blocked by the
 * page's own CSP; LinkedIn's is strict enough to matter. Requests from here are
 * covered by host_permissions instead, so localhost stays reachable no matter
 * what the page allows.
 *
 * Everything is best-effort. If the bridge is not running, collecting still
 * works and still stores to chrome.storage, and the popup's manual JSON export
 * remains the fallback path.
 */

const BRIDGE = "http://127.0.0.1:8765";

async function call(path, body, timeoutMs = 20000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(BRIDGE + path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) return { ok: false, error: `bridge returned ${res.status}` };
    return { ok: true, ...(await res.json()) };
  } catch (e) {
    // Bridge down, or it took longer than the timeout. Not fatal.
    return { ok: false, error: e.name === "AbortError" ? "bridge timed out" : "bridge offline" };
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "bridge:status") {
    call("/status", null, 4000).then(sendResponse);
    return true;
  }
  if (msg?.type === "bridge:push") {
    call("/ingest", { jobs: msg.jobs || [] }).then((r) => {
      if (r.ok) chrome.storage.local.set({ lastPush: { at: Date.now(), ...r } });
      sendResponse(r);
    });
    return true;
  }
  if (msg?.type === "bridge:applied") {
    call("/applied", { urls: msg.urls || [] }).then(sendResponse);
    return true;
  }
  if (msg?.type === "bridge:built") {
    call("/built", null, 4000).then(sendResponse);
    return true;
  }
  if (msg?.type === "bridge:reset") {
    call("/reset", {}).then(sendResponse);
    return true;
  }
  return false;
});
