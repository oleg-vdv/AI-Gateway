// AI-Gate DLP — service worker.
// Все обращения к шлюзу идут отсюда (host_permissions), чтобы не зависеть
// от CORS-политики страниц веб-LLM. Ключ канала хранится в chrome.storage,
// ключи LLM-провайдера в расширении НЕ хранятся (раздел 8 ТЗ).

const DEFAULTS = {
  gatewayUrl: "http://localhost:8080",
  channelKey: "",
  user: "browser-user",
};

async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(DEFAULTS, resolve);
  });
}

async function gatewayFetch(path, body) {
  const cfg = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (cfg.channelKey) headers["X-AIGate-Key"] = cfg.channelKey;
  const resp = await fetch(cfg.gatewayUrl.replace(/\/$/, "") + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`gateway ${resp.status}: ${detail}`);
  }
  return resp.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    const cfg = await getConfig();
    try {
      if (msg.type === "AIGATE_CHECK") {
        const result = await gatewayFetch("/v1/check", {
          text: msg.text,
          user: cfg.user,
          channel: "browser",
          session_id: msg.sessionId || null,
          dry_run: true,
        });
        sendResponse({ ok: true, result });
      } else if (msg.type === "AIGATE_RESTORE") {
        const result = await gatewayFetch("/v1/restore", {
          session_id: msg.sessionId,
          text: msg.text,
          purge: msg.purge !== false,
        });
        sendResponse({ ok: true, result });
      } else {
        sendResponse({ ok: false, error: "unknown message type" });
      }
    } catch (e) {
      // Fail-closed на стороне канала: если шлюз недоступен —канал должен
      // блокировать отправку, а не пропускать текст как есть.
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // асинхронный sendResponse
});
