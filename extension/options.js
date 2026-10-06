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

chrome.storage.sync.get(DEFAULTS, (cfg) => {
  for (const key of Object.keys(DEFAULTS)) {
    document.getElementById(key).value = cfg[key] || "";
  }
});

document.getElementById("save").addEventListener("click", () => {
  const cfg = {};
  for (const key of Object.keys(DEFAULTS)) {
    cfg[key] = document.getElementById(key).value.trim() || DEFAULTS[key];
  }
  let origin;
  try {
    origin = new URL(cfg.gatewayUrl).origin;
  } catch (_) {
    show("Адрес шлюза указан неверно. Пример: http://localhost:8080", "err");
    return;
  }

  const save = () =>
    chrome.storage.sync.set(cfg, () => show("Сохранено", "ok"));

  if (isLocal(cfg.gatewayUrl)) {
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
