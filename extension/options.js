const DEFAULTS = {
  gatewayUrl: "http://localhost:8080",
  channelKey: "",
  user: "browser-user",
};

const statusEl = document.getElementById("status");

function show(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

function isLocal(url) {
  try {
    const h = new URL(url).hostname;
    return h === "localhost" || h === "127.0.0.1";
  } catch (_) {
    return false;
  }
}

function readArea(area, keys) {
  return new Promise((resolve) => {
    if (!area) return resolve({});
    try {
      area.get(keys, (items) => resolve(chrome.runtime.lastError ? {} : items || {}));
    } catch (_) {
      resolve({});
    }
  });
}

// Настройки в storage.local (не синхронизируются в аккаунт Google);
// значения из политики администратора (storage.managed) важнее и не редактируются.
let managed = {};

(async () => {
  const keys = Object.keys(DEFAULTS);
  const synced = await readArea(chrome.storage.sync, keys);
  const local = await readArea(chrome.storage.local, keys);
  managed = await readArea(chrome.storage.managed, keys);
  const cfg = { ...DEFAULTS, ...synced, ...local, ...managed };
  for (const key of keys) {
    const input = document.getElementById(key);
    input.value = cfg[key] || "";
    if (key in managed) {
      input.disabled = true;
      input.title = "Задано администратором";
    }
  }
  if (Object.keys(managed).length) {
    show("Часть настроек задал администратор, их нельзя изменить здесь.");
  }
})();

document.getElementById("save").addEventListener("click", () => {
  const cfg = {};
  for (const key of Object.keys(DEFAULTS)) {
    if (key in managed) continue;
    cfg[key] = document.getElementById(key).value.trim() || DEFAULTS[key];
  }
  const gatewayUrl = managed.gatewayUrl || cfg.gatewayUrl;
  let origin;
  try {
    origin = new URL(gatewayUrl).origin;
  } catch (_) {
    show("Адрес шлюза указан неверно. Пример: http://localhost:8080", "err");
    return;
  }

  const save = () =>
    chrome.storage.local.set(cfg, () => {
      chrome.storage.sync.remove(Object.keys(DEFAULTS));
      show("Сохранено", "ok");
    });

  if (isLocal(gatewayUrl)) {
    save();
    return;
  }
  // permissions.request должен вызываться прямо в обработчике клика
  chrome.permissions.request({ origins: [origin + "/*"] }, (granted) => {
    if (granted) save();
    else show("Без разрешения расширение не сможет обращаться к шлюзу", "err");
  });
});

document.getElementById("test").addEventListener("click", () => {
  show("Проверяю…");
  chrome.runtime.sendMessage({ type: "AIGATE_HEALTH" }, (resp) => {
    if (chrome.runtime.lastError) {
      show(chrome.runtime.lastError.message, "err");
    } else if (resp && resp.ok) {
      show("Шлюз отвечает. Сохраните настройки, если меняли их.", "ok");
    } else {
      show("Шлюз недоступен: " + (resp ? resp.error : "нет ответа"), "err");
    }
  });
});
