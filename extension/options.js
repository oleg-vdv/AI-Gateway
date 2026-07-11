const DEFAULTS = {
  gatewayUrl: "http://localhost:8080",
  channelKey: "",
  user: "browser-user",
};

chrome.storage.sync.get(DEFAULTS, (cfg) => {
  for (const key of Object.keys(DEFAULTS)) {
    document.getElementById(key).value = cfg[key] || "";
  }
});

document.getElementById("save").addEventListener("click", () => {
  const cfg = {};
  for (const key of Object.keys(DEFAULTS)) {
    cfg[key] = document.getElementById(key).value.trim();
  }
  chrome.storage.sync.set(cfg, () => {
    const s = document.getElementById("status");
    s.textContent = "Сохранено ✓";
    setTimeout(() => (s.textContent = ""), 2000);
  });
});
