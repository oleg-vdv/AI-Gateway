// AI-Gate DLP — service worker (Chrome/Edge) / background script (Firefox).
//
// Все обращения к шлюзу идут отсюда: host_permissions снимают зависимость
// от CORS-политики страниц веб-LLM. Ключ канала хранится в chrome.storage,
// ключи LLM-провайдера в расширении НЕ хранятся.
//
// v0.2.0:
//  - одна сессия шлюза на один диалог (иначе [PERSON_1] во втором сообщении
//    может означать другого человека, чем в первом, и модель их смешает);
//  - сообщения принимаются только от собственных content scripts
//    на поддерживаемых сайтах;
//  - значок расширения показывает, доступен ли шлюз.

const DEFAULTS = {
  gatewayUrl: "http://localhost:8080",
  channelKey: "",
  user: "browser-user",
};

const ALLOWED_ORIGINS = [
  "https://chatgpt.com",
  "https://chat.openai.com",
  "https://claude.ai",
  "https://gemini.google.com",
];

// storage.session живёт в памяти и очищается при закрытии браузера:
// идентификаторы сессий не должны переживать рабочий день на диске.
const sessionStore = chrome.storage.session || chrome.storage.local;

function getConfig() {
  return new Promise((resolve) => chrome.storage.sync.get(DEFAULTS, resolve));
}

function baseUrl(cfg) {
  return cfg.gatewayUrl.replace(/\/+$/, "");
}

async function gatewayFetch(path, body) {
  const cfg = await getConfig();
  const headers = { "Content-Type": "application/json" };
  if (cfg.channelKey) headers["X-AIGate-Key"] = cfg.channelKey;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const resp = await fetch(baseUrl(cfg) + path, {
      method: body === undefined ? "GET" : "POST",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new Error(`шлюз ответил ${resp.status}: ${detail.slice(0, 200)}`);
    }
    return resp.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error("шлюз не ответил за 15 секунд");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// --- сессии: ключ диалога -> session_id шлюза ---------------------------------

async function getSessionId(convKey) {
  if (!convKey) return null;
  const k = "s:" + convKey;
  const data = await sessionStore.get(k);
  return data[k] || null;
}

async function setSessionId(convKey, sessionId) {
  if (!convKey || !sessionId) return;
  await sessionStore.set({ ["s:" + convKey]: sessionId });
}

async function bindSession(fromKey, toKey) {
  const sid = await getSessionId(fromKey);
  // не затираем сессию, если у нового адреса она уже есть
  if (sid && !(await getSessionId(toKey))) {
    await setSessionId(toKey, sid);
  }
}

// --- значок статуса -------------------------------------------------------------

function setBadge(tabId, ok) {
  if (!chrome.action || tabId === undefined) return;
  chrome.action.setBadgeText({ tabId, text: ok ? "ON" : "!" });
  chrome.action.setBadgeBackgroundColor({ tabId, color: ok ? "#15803d" : "#b91c1c" });
}

// --- проверка отправителя ------------------------------------------------------

function isTrustedSender(sender) {
  if (sender.id !== chrome.runtime.id) return false;
  // страница настроек расширения
  if (!sender.tab) return true;
  try {
    const origin = new URL(sender.url || sender.tab.url).origin;
    return ALLOWED_ORIGINS.includes(origin);
  } catch (_) {
    return false;
  }
}

// --- обработчик сообщений --------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!isTrustedSender(sender)) {
    sendResponse({ ok: false, error: "недоверенный отправитель" });
    return false;
  }
  const tabId = sender.tab ? sender.tab.id : undefined;

  (async () => {
    const cfg = await getConfig();
    try {
      if (msg.type === "AIGATE_CHECK") {
        const sessionId = await getSessionId(msg.convKey);
        const result = await gatewayFetch("/v1/check", {
          text: msg.text,
          user: cfg.user,
          channel: "browser",
          session_id: sessionId,
          dry_run: true,
        });
        await setSessionId(msg.convKey, result.session_id);
        setBadge(tabId, true);
        sendResponse({ ok: true, result });
      } else if (msg.type === "AIGATE_RESTORE") {
        const sessionId = await getSessionId(msg.convKey);
        if (!sessionId) {
          sendResponse({ ok: false, error: "нет сессии для этого диалога" });
          return;
        }
        const result = await gatewayFetch("/v1/restore", {
          session_id: sessionId,
          text: msg.text,
          purge: false,
        });
        sendResponse({ ok: true, result });
      } else if (msg.type === "AIGATE_BIND") {
        await bindSession(msg.fromKey, msg.toKey);
        sendResponse({ ok: true });
      } else if (msg.type === "AIGATE_HEALTH") {
        const result = await gatewayFetch("/healthz");
        setBadge(tabId, true);
        sendResponse({ ok: true, result });
      } else {
        sendResponse({ ok: false, error: "неизвестный тип сообщения" });
      }
    } catch (e) {
      // Fail-closed: при недоступности шлюза канал блокирует отправку,
      // а не пропускает текст как есть.
      setBadge(tabId, false);
      sendResponse({ ok: false, error: String(e.message || e) });
    }
  })();
  return true; // асинхронный sendResponse
});

if (chrome.action && chrome.action.onClicked) {
  chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
}
